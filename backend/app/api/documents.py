"""Documents: secure upload, versions, evidence view, corrections, official checks and officer review."""

from __future__ import annotations

import json
from datetime import date
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import audit, storage
from .. import serializers as ser
from ..config import get_settings
from ..db import get_db
from ..deps import Auth, require_officer
from ..documents.extract import TableResult
from ..documents.fields import FIELD_LABELS, Candidate, FieldExtraction
from ..documents.normalize import normalize_academic_year
from ..documents.upload import UploadRejected, safe_filename, validate_document_upload
from ..documents.validation import ValidationContext, run_document_rules
from ..integrations.clamav import scan_bytes
from ..integrations.official import generic_qr_allowed, qr_allowed
from ..jobs import enqueue, retry
from ..models import (
    CheckRun,
    Decision,
    Document,
    DocumentCategory,
    DocumentPage,
    DocumentVersion,
    EvidenceArtifact,
    ExternalVerificationAttempt,
    ExtractedField,
    ExtractedTable,
    FieldCorrection,
    Finding,
    Institution,
    Integration,
    Job,
    Officer,
    Program,
    TextBlock,
    ValidationResult,
    VerificationCase,
    utcnow,
)
from ..security.crypto import decrypt_secret, sha256_hex
from ..verification.engine import (
    DOCUMENT_STATUSES,
    category_label,
    latest_integrity,
    latest_validation,
    refresh_document_status,
    sync_document_findings,
)
from ..verification.jobs import qr_links, unable_attempt
from .common import file_url, get_or_404, like, page_params, paginate

router = APIRouter(prefix="/api", tags=["documents"])

REVIEW_OUTCOMES = {
    "accepted_after_review": "Accepted after officer review",
    "requires_resubmission": "Resubmission required",
    "requires_physical_verification": "Requires physical verification",
    "rejected": "Rejected by authorized officer",
}


async def _read_limited(file: UploadFile, limit: int) -> bytes:
    data = await file.read(limit + 1)
    if len(data) > limit:
        raise HTTPException(413, f"The file exceeds the {limit // (1024 * 1024)} MB limit.")
    return data


def _decrypt_copy(data: bytes, password: str) -> bytes:
    import pymupdf

    doc = pymupdf.open(stream=data, filetype="pdf")
    try:
        if not doc.authenticate(password):
            raise HTTPException(422, "The supplied PDF password is incorrect.")
        return doc.tobytes(encryption=pymupdf.PDF_ENCRYPT_NONE, garbage=0)
    finally:
        doc.close()


@router.post("/documents", status_code=201)
async def upload_document(
    file: UploadFile = File(...),
    institution_id: str = Form(...),
    case_id: str | None = Form(None),
    document_id: str | None = Form(None),
    title: str | None = Form(None, max_length=400),
    category_key: str | None = Form(None),
    academic_year: str | None = Form(None),
    issuing_authority: str | None = Form(None, max_length=300),
    note: str | None = Form(None, max_length=4000),
    password: str | None = Form(None, max_length=200),
    is_historical: bool = Form(False),
    auth: Auth = Depends(require_officer),
    db: Session = Depends(get_db),
) -> dict:
    s = get_settings()
    inst = get_or_404(db, Institution, institution_id, "Institution")
    case = None
    if case_id:
        case = get_or_404(db, VerificationCase, case_id, "Case")
        if case.institution_id != inst.id:
            raise HTTPException(422, "The case belongs to a different institution.")
    if category_key and not db.get(DocumentCategory, category_key):
        raise HTTPException(422, "Unknown document category.")
    ay = None
    if academic_year:
        ay, _m, ok = normalize_academic_year(academic_year)
        if not ok:
            raise HTTPException(422, "Academic year must be like 2026-27.")
    filename = safe_filename(file.filename)
    data = await _read_limited(file, s.max_document_mb * 1024 * 1024)
    try:
        info = validate_document_upload(filename, data, password or None)
    except UploadRejected as exc:
        audit.record(db, "document.upload_rejected", officer=auth.officer, entity_type="institution", entity_id=inst.id,
                     institution_id=inst.id, summary=str(exc), after={"filename": filename, "size": len(data)}, ctx=auth.ctx)
        db.commit()
        raise HTTPException(422, str(exc)) from exc
    mal_status, mal_detail = scan_bytes(data)
    if mal_status == "infected":
        audit.record(db, "document.upload_rejected", officer=auth.officer, entity_type="institution", entity_id=inst.id,
                     institution_id=inst.id, summary="Malware detected", outcome="infected",
                     after={"filename": filename, "signature": mal_detail}, ctx=auth.ctx)
        db.commit()
        raise HTTPException(422, f"The file was rejected by the malware scanner ({mal_detail}).")
    if s.malware_scan_required and mal_status != "clean":
        raise HTTPException(503, f"Uploads require a malware scan, which is unavailable: {mal_detail}")

    sha = sha256_hex(data)
    dup = db.execute(select(DocumentVersion).where(DocumentVersion.sha256 == sha).order_by(DocumentVersion.uploaded_at)).scalars().first()
    original_key = storage.put(data, "originals")
    processing_key = None
    blocked_reason = None
    if info.encrypted and info.detected_type == "pdf":
        if password:
            processing_key = storage.put(_decrypt_copy(data, password), "processing")
        elif any("password-protected" in w for w in info.warnings):
            blocked_reason = ("The PDF is password-protected. Upload it again with its password, or request an unprotected copy. "
                              "The original has been preserved.")

    if document_id:
        doc = get_or_404(db, Document, document_id, "Document")
        if doc.institution_id != inst.id:
            raise HTTPException(422, "The document belongs to a different institution.")
        version_no = (db.execute(select(func.max(DocumentVersion.version_no)).where(DocumentVersion.document_id == doc.id)).scalar_one() or 0) + 1
        doc.review_status = "unreviewed"
        doc.reviewed_at = None
        doc.reviewed_by_id = None
    else:
        doc = Document(institution_id=inst.id, case_id=case.id if case else None, title=(title or filename)[:400],
                       category_key=category_key, category_source="officer" if category_key else "unclassified",
                       academic_year=ay or (case.academic_year if case else None), issuing_authority=issuing_authority,
                       is_historical=is_historical, created_by_id=auth.officer.id)
        db.add(doc)
        db.flush()
        version_no = 1
    v = DocumentVersion(
        document_id=doc.id, version_no=version_no, original_filename=filename, declared_mime=file.content_type,
        detected_type=info.detected_type, size_bytes=len(data), sha256=sha, storage_key=original_key,
        processing_key=processing_key, page_count=info.page_count, pdf_encrypted=info.encrypted,
        active_content=info.active_content, malware_status=mal_status, malware_detail=mal_detail,
        duplicate_of_version_id=dup.id if dup else None, upload_note=note, uploaded_by_id=auth.officer.id,
        processing_status="blocked" if blocked_reason else "queued", processing_error=blocked_reason,
        metadata_info=info.metadata,
    )
    db.add(v)
    db.flush()
    doc.current_version_id = v.id
    doc.status = "requires_manual_review" if blocked_reason else "uploaded"
    doc.status_reason = blocked_reason
    job = None
    if not blocked_reason:
        job = enqueue(db, "process_document_version", {"version_id": v.id}, idempotency_key=f"process:{v.id}",
                      entity_type="document", entity_id=doc.id, institution_id=inst.id, created_by_id=auth.officer.id)
    audit.record(db, "document.uploaded", officer=auth.officer, entity_type="document", entity_id=doc.id, institution_id=inst.id,
                 case_id=doc.case_id, summary=f"Uploaded '{doc.title}' version {version_no}",
                 after={"filename": filename, "size_bytes": len(data), "sha256": sha, "detected_type": info.detected_type,
                        "malware_scan": mal_status, "duplicate_of": dup.id if dup else None, "blocked": bool(blocked_reason)},
                 evidence=[{"type": "document_version", "id": v.id, "sha256": sha}], ctx=auth.ctx)
    if doc.case_id:
        enqueue(db, "evaluate_case", {"case_id": doc.case_id}, entity_type="case", entity_id=doc.case_id,
                institution_id=inst.id, created_by_id=auth.officer.id, priority=60)
    db.commit()
    return {"document": ser.document(doc), "version": ser.version(v), "job": ser.job(job) if job else None,
            "duplicate_of": {"version_id": dup.id, "document_id": dup.document_id} if dup else None,
            "warnings": info.warnings + ([mal_detail] if mal_status != "clean" and mal_detail else [])}


@router.get("/documents")
def list_documents(q: str | None = None, institution_id: str | None = None, case_id: str | None = None,
                   status: str | None = None, category_key: str | None = None, review_status: str | None = None,
                   historical: bool | None = None, pg: tuple[int, int] = Depends(page_params),
                   auth: Auth = Depends(require_officer), db: Session = Depends(get_db)) -> dict:
    stmt = select(Document).join(Institution, Institution.id == Document.institution_id)
    if q:
        stmt = stmt.where(or_(Document.title.ilike(like(q)), Institution.name.ilike(like(q))))
    for col, val in ((Document.institution_id, institution_id), (Document.case_id, case_id), (Document.status, status),
                     (Document.category_key, category_key), (Document.review_status, review_status)):
        if val:
            stmt = stmt.where(col == val)
    if historical is not None:
        stmt = stmt.where(Document.is_historical.is_(historical))
    rows, total = paginate(db, stmt.order_by(Document.created_at.desc()), *pg)
    inst_names = dict(db.execute(select(Institution.id, Institution.name).where(Institution.id.in_({r.institution_id for r in rows}))).all()) if rows else {}
    cats = {c.key: c.label for c in db.execute(select(DocumentCategory)).scalars()}
    items = []
    for d in rows:
        v = db.get(DocumentVersion, d.current_version_id) if d.current_version_id else None
        items.append(ser.document(d, extra={"institution_name": inst_names.get(d.institution_id),
                                            "category_label": cats.get(d.category_key), "status_label": DOCUMENT_STATUSES.get(d.status),
                                            "version": ser.version(v) if v else None}))
    return ser.page_of(items, total, *pg)


def _fields_with_corrections(db: Session, version_id: str) -> list[dict]:
    fields = db.execute(select(ExtractedField).where(ExtractedField.version_id == version_id)
                        .order_by(ExtractedField.group_index.is_not(None), ExtractedField.group_index, ExtractedField.field_key)).scalars().all()
    corr: dict[str, list[FieldCorrection]] = {}
    if fields:
        for c in db.execute(select(FieldCorrection).where(FieldCorrection.field_id.in_([f.id for f in fields]))
                            .order_by(FieldCorrection.corrected_at)).scalars():
            corr.setdefault(c.field_id, []).append(c)
    return [ser.field(f, corr.get(f.id)) for f in fields]


@router.get("/documents/{doc_id}")
def get_document(doc_id: str, version_id: str | None = None, auth: Auth = Depends(require_officer), db: Session = Depends(get_db)) -> dict:
    doc = get_or_404(db, Document, doc_id, "Document")
    inst = db.get(Institution, doc.institution_id)
    case = db.get(VerificationCase, doc.case_id) if doc.case_id else None
    versions = db.execute(select(DocumentVersion).where(DocumentVersion.document_id == doc.id).order_by(DocumentVersion.version_no.desc())).scalars().all()
    vid = version_id or doc.current_version_id
    v = db.get(DocumentVersion, vid) if vid else None
    if v is None or v.document_id != doc.id:
        raise HTTPException(404, "Version not found")
    oid = auth.officer.id
    pages = db.execute(select(DocumentPage).where(DocumentPage.version_id == v.id).order_by(DocumentPage.page_no)).scalars().all()
    tables = db.execute(select(ExtractedTable).where(ExtractedTable.version_id == v.id).order_by(ExtractedTable.page_no)).scalars().all()
    artifacts = db.execute(select(EvidenceArtifact).where(EvidenceArtifact.version_id == v.id)).scalars().all()
    job = db.execute(select(Job).where(Job.idempotency_key == f"process:{v.id}")).scalar_one_or_none()
    attempts = db.execute(select(ExternalVerificationAttempt).where(ExternalVerificationAttempt.document_id == doc.id)
                          .order_by(ExternalVerificationAttempt.attempted_at.desc())).scalars().all()
    decisions = db.execute(select(Decision).where(Decision.document_id == doc.id).order_by(Decision.decided_at.desc())).scalars().all()
    findings = db.execute(select(Finding).where(Finding.document_id == doc.id).order_by(Finding.created_at.desc())).scalars().all()
    officers = {o.id: o for o in db.execute(select(Officer)).scalars()}
    qr = qr_links(db, doc) if v.processing_status == "completed" else []
    dup = None
    if v.duplicate_of_version_id:
        ov = db.get(DocumentVersion, v.duplicate_of_version_id)
        od = db.get(Document, ov.document_id) if ov else None
        if od:
            dup = {"document_id": od.id, "title": od.title, "institution_id": od.institution_id, "uploaded_at": ser.iso(ov.uploaded_at)}
    return {
        "document": ser.document(doc, extra={"status_label": DOCUMENT_STATUSES.get(doc.status), "category_label": category_label(db, doc.category_key)}),
        "institution": {"id": inst.id, "name": inst.name, "aicte_permanent_id": inst.aicte_permanent_id},
        "case": ser.case(case) if case else None,
        "versions": [ser.version(x) | {"uploaded_by": (officers.get(x.uploaded_by_id).full_name if officers.get(x.uploaded_by_id) else None)} for x in versions],
        "version": ser.version(v),
        "download_url": file_url("original", v.id, oid, download=True),
        "job": ser.job(job) if job else None,
        "pages": [{"id": p.id, "page_no": p.page_no, "url": file_url("page", p.id, oid) if p.render_key else None,
                   "width": p.render_width, "height": p.render_height, "text_source": p.text_source,
                   "ocr_confidence": p.ocr_confidence, "is_blank": p.is_blank, "unreadable": p.unreadable,
                   "char_count": p.char_count} for p in pages],
        "fields": _fields_with_corrections(db, v.id),
        "field_labels": FIELD_LABELS,
        "tables": [{"id": t.id, "page_no": t.page_no, "bbox": t.bbox, "table_type": t.table_type, "header": t.header,
                    "rows": t.rows} for t in tables],
        "validation": [ser.validation(r) for r in latest_validation(db, v.id)],
        "integrity": (integ := [ser.integrity(c) for c in latest_integrity(db, v.id)]),
        "ai_pending": any((c.get("details") or {}).get("pending") for c in integ),
        "artifacts": [{"id": a.id, "kind": a.kind, "page_no": a.page_no, "url": file_url("artifact", a.id, oid),
                       "width": a.width, "height": a.height, "meta": a.meta} for a in artifacts],
        "official_attempts": [ser.attempt(a) for a in attempts],
        "decisions": [ser.decision(d, officers) for d in decisions],
        "findings": [ser.finding(f) for f in findings],
        "qr_links": [{"url": u, "allowlisted": _qr_route(db, u)[0] is not None, "host": urlsplit(u).hostname} for u in qr],
        "duplicate_of": dup,
        "review_outcomes": REVIEW_OUTCOMES,
        "summary": __import__("app.verification.summary", fromlist=["document_checks"]).document_checks(db, doc),
    }


@router.get("/documents/{doc_id}/text")
def page_text(doc_id: str, page_no: int, version_id: str | None = None, auth: Auth = Depends(require_officer),
              db: Session = Depends(get_db)) -> dict:
    doc = get_or_404(db, Document, doc_id, "Document")
    vid = version_id or doc.current_version_id
    rows = db.execute(select(TextBlock).where(TextBlock.version_id == vid, TextBlock.page_no == page_no).order_by(TextBlock.seq)).scalars().all()
    return {"items": [{"seq": t.seq, "text": t.text, "bbox": t.bbox, "confidence": t.confidence, "source": t.source} for t in rows]}


class DocumentPatch(BaseModel):
    title: str | None = Field(default=None, max_length=400)
    category_key: str | None = None
    academic_year: str | None = None
    issuing_authority: str | None = Field(default=None, max_length=300)
    case_id: str | None = None
    reason: str = Field(min_length=3, max_length=2000)


@router.patch("/documents/{doc_id}")
def update_document(doc_id: str, body: DocumentPatch, auth: Auth = Depends(require_officer), db: Session = Depends(get_db)) -> dict:
    doc = get_or_404(db, Document, doc_id, "Document")
    before = ser.document(doc)
    data = body.model_dump(exclude_unset=True, exclude={"reason"})
    if "category_key" in data:
        if data["category_key"] and not db.get(DocumentCategory, data["category_key"]):
            raise HTTPException(422, "Unknown document category.")
        doc.category_source = "officer"
    if data.get("academic_year"):
        ay, _m, ok = normalize_academic_year(data["academic_year"])
        if not ok:
            raise HTTPException(422, "Academic year must be like 2026-27.")
        data["academic_year"] = ay
    if data.get("case_id"):
        c = get_or_404(db, VerificationCase, data["case_id"], "Case")
        if c.institution_id != doc.institution_id:
            raise HTTPException(422, "The case belongs to a different institution.")
    old_case = doc.case_id
    for k, v in data.items():
        setattr(doc, k, v)
    db.flush()
    audit.record(db, "document.updated", officer=auth.officer, entity_type="document", entity_id=doc.id, institution_id=doc.institution_id,
                 case_id=doc.case_id, before=before, after=ser.document(doc), reason=body.reason, ctx=auth.ctx)
    for cid in {old_case, doc.case_id} - {None}:
        enqueue(db, "evaluate_case", {"case_id": cid}, entity_type="case", entity_id=cid, institution_id=doc.institution_id,
                created_by_id=auth.officer.id, priority=50)
    db.commit()
    if "category_key" in data and doc.current_version_id:
        _revalidate(db, doc, auth)
        db.commit()
    return ser.document(doc)


class CorrectionIn(BaseModel):
    value: str = Field(min_length=1, max_length=4000)
    reason: str = Field(min_length=3, max_length=2000)


@router.post("/fields/{field_id}/corrections", status_code=201)
def correct_field(field_id: str, body: CorrectionIn, auth: Auth = Depends(require_officer), db: Session = Depends(get_db)) -> dict:
    f = get_or_404(db, ExtractedField, field_id, "Field")
    doc = db.get(Document, f.document_id)
    c = FieldCorrection(field_id=f.id, corrected_value=body.value.strip(), reason=body.reason.strip(), corrected_by_id=auth.officer.id)
    db.add(c)
    db.flush()
    audit.record(db, "field.corrected", officer=auth.officer, entity_type="extracted_field", entity_id=f.id,
                 institution_id=doc.institution_id, case_id=doc.case_id, before={"value": f.value},
                 after={"value": c.corrected_value}, reason=c.reason,
                 evidence=[{"type": "field", "field_id": f.id, "page_no": f.page_no, "bbox": f.bbox}], ctx=auth.ctx)
    db.commit()
    if f.version_id == doc.current_version_id:
        _revalidate(db, doc, auth)
        db.commit()
    return {"correction": {"id": c.id, "corrected_value": c.corrected_value, "reason": c.reason, "corrected_at": ser.iso(c.corrected_at)}}


def _revalidate(db: Session, doc: Document, auth: Auth | None) -> CheckRun:
    """Re-run document validation on stored fields (officer corrections applied). Results are a new run."""
    from ..documents.fields import normalize_value

    v = db.get(DocumentVersion, doc.current_version_id)
    if v is None or v.processing_status != "completed":
        raise HTTPException(409, "The document has not been processed yet.")
    rows = db.execute(select(ExtractedField).where(ExtractedField.version_id == v.id)).scalars().all()
    corr = {}
    for c in db.execute(select(FieldCorrection).where(FieldCorrection.field_id.in_([r.id for r in rows])).order_by(FieldCorrection.corrected_at)).scalars():
        corr[c.field_id] = c.corrected_value
    singles, field_ids, groups = {}, {}, {}
    for r in rows:
        value = corr.get(r.id, r.value) or ""
        cand = Candidate(r.field_key, value, normalize_value(r.field_key, value) if r.id in corr else r.normalized_value,
                         r.original_text or "", r.page_no, r.bbox, r.confidence, r.confidence_basis or "", r.method, r.group_index, 3)
        if r.group_index is None:
            singles[r.field_key] = cand
            field_ids[r.field_key] = r.id
        else:
            groups.setdefault(r.group_index, {})[r.field_key] = cand
    from ..documents.normalize import parse_int

    programs = [{"group_index": gi, "page_no": g["program_course"].page_no, "course": g["program_course"].value,
                 "level": g["program_level"].value if "program_level" in g else None,
                 "intake": parse_int(g["program_intake"].value) if "program_intake" in g else None,
                 "intake_raw": g["program_intake"].value if "program_intake" in g else None}
                for gi, g in sorted(groups.items()) if "program_course" in g]
    pages = db.execute(select(DocumentPage).where(DocumentPage.version_id == v.id)).scalars().all()
    tables = [TableResult(t.page_no, t.bbox, t.header, t.rows, [], t.source) for t in
              db.execute(select(ExtractedTable).where(ExtractedTable.version_id == v.id)).scalars()]
    inst = db.get(Institution, doc.institution_id)
    case = db.get(VerificationCase, doc.case_id) if doc.case_id else None
    dup = None
    if v.duplicate_of_version_id:
        ov = db.get(DocumentVersion, v.duplicate_of_version_id)
        od = db.get(Document, ov.document_id) if ov else None
        if od:
            dup = {"document_id": od.id, "version_id": ov.id, "title": od.title, "uploaded_at": ov.uploaded_at.date().isoformat(),
                   "other_institution": od.institution_id != doc.institution_id}
    ctx = ValidationContext(
        fields=singles, extraction=FieldExtraction(list(singles.values()), {}, programs),
        category=db.get(DocumentCategory, doc.category_key) if doc.category_key else None, institution=inst, case=case,
        registry_programs=db.execute(select(Program).where(Program.institution_id == inst.id)).scalars().all(),
        field_ids=field_ids,
        pages=[{"page_no": p.page_no, "is_blank": p.is_blank, "unreadable": p.unreadable, "ocr_confidence": p.ocr_confidence,
                "text_source": p.text_source} for p in pages],
        duplicate_of=dup, review_date=date.today(), low_ocr_threshold=get_settings().ocr_low_confidence,
        full_text="\n".join(t.text for t in db.execute(select(TextBlock).where(TextBlock.version_id == v.id).order_by(TextBlock.page_no, TextBlock.seq)).scalars()),
    )
    results = run_document_rules(ctx, tables)
    run = CheckRun(kind="document_validation", version_id=v.id, case_id=doc.case_id, institution_id=inst.id, status="completed",
                   triggered_by_id=auth.officer.id if auth else None, finished_at=utcnow(),
                   summary={"reason": "revalidation", "corrections_applied": len(corr)})
    db.add(run)
    db.flush()
    for r in results:
        db.add(ValidationResult(run_id=run.id, case_id=doc.case_id, document_id=doc.id, version_id=v.id, rule_key=r.rule_key,
                                rule_label=r.rule_label, category=r.category, status=r.status, message=r.message,
                                details=audit.jsonable(r.details), evidence=audit.jsonable(r.evidence)))
    db.flush()
    refresh_document_status(db, doc)
    sync_document_findings(db, doc, v)
    if auth:
        audit.record(db, "document.revalidated", officer=auth.officer, entity_type="document", entity_id=doc.id,
                     institution_id=doc.institution_id, case_id=doc.case_id, after={"status": doc.status, "run_id": run.id}, ctx=auth.ctx)
    if doc.case_id:
        enqueue(db, "evaluate_case", {"case_id": doc.case_id}, entity_type="case", entity_id=doc.case_id,
                institution_id=doc.institution_id, created_by_id=auth.officer.id if auth else None, priority=50)
    return run


@router.post("/documents/{doc_id}/revalidate")
def revalidate(doc_id: str, auth: Auth = Depends(require_officer), db: Session = Depends(get_db)) -> dict:
    doc = get_or_404(db, Document, doc_id, "Document")
    run = _revalidate(db, doc, auth)
    db.commit()
    return {"run_id": run.id, "status": doc.status}


@router.post("/documents/{doc_id}/reprocess")
def reprocess(doc_id: str, auth: Auth = Depends(require_officer), db: Session = Depends(get_db)) -> dict:
    doc = get_or_404(db, Document, doc_id, "Document")
    v = db.get(DocumentVersion, doc.current_version_id)
    if v.processing_status != "failed":
        raise HTTPException(409, "Only failed processing can be retried. Upload a new version to change the file.")
    job = db.execute(select(Job).where(Job.idempotency_key == f"process:{v.id}")).scalar_one_or_none()
    if job is None:
        job = enqueue(db, "process_document_version", {"version_id": v.id}, idempotency_key=f"process:{v.id}",
                      entity_type="document", entity_id=doc.id, institution_id=doc.institution_id, created_by_id=auth.officer.id)
    else:
        retry(db, job)
    v.processing_status = "queued"
    doc.status = "processing"
    audit.record(db, "job.retried", officer=auth.officer, entity_type="job", entity_id=job.id, institution_id=doc.institution_id,
                 case_id=doc.case_id, summary=f"Retry processing of '{doc.title}'", ctx=auth.ctx)
    db.commit()
    return {"job": ser.job(job)}


# ---------------------------------------------------------------- official verification

class OfficialRequest(BaseModel):
    method: str = Field(pattern="^(qr|integration)$")
    qr_url: str | None = Field(default=None, max_length=2000)
    integration_id: str | None = None
    claim_type: str | None = Field(default=None, max_length=60)


def _qr_route(db: Session, url: str) -> tuple[Integration | None, str]:
    """AICTE approval-status links use the AICTE parser; other official hosts use the generic issuer-page check."""
    by = {i.adapter: i for i in db.execute(select(Integration).where(Integration.adapter.in_(("aicte_qr_status", "issuer_qr_generic")))).scalars()}
    a, g = by.get("aicte_qr_status"), by.get("issuer_qr_generic")
    if a and a.enabled and qr_allowed(url, a):
        return a, "approval_record"
    if g and g.enabled and generic_qr_allowed(url, g):
        return g, "issuer_record"
    return None, "issuer_record"


@router.get("/documents/{doc_id}/official-options")
def official_options(doc_id: str, auth: Auth = Depends(require_officer), db: Session = Depends(get_db)) -> dict:
    doc = get_or_404(db, Document, doc_id, "Document")
    integs = db.execute(select(Integration).where(Integration.kind == "official_record")).scalars().all()
    links = qr_links(db, doc) if doc.current_version_id else []
    qr = []
    for u in links:
        integ, _ct = _qr_route(db, u)
        qr.append({"url": u, "host": urlsplit(u).hostname, "allowlisted": integ is not None, "source": integ.name if integ else None})
    return {
        "qr": qr,
        "integrations": [{"id": i.id, "name": i.name, "claim_types": i.claim_types, "enabled": i.enabled,
                          "connection_status": i.connection_status,
                          "configured": bool(i.base_url) and bool((i.config or {}).get("request") or (i.config or {}).get("requests")),
                          "adapter": i.adapter} for i in integs if i.adapter == "generic_rest"],
    }


@router.get("/verification-sources")
def verification_sources(category_key: str | None = None, auth: Auth = Depends(require_officer)) -> dict:
    """Public official websites for manual checks (data/reference/public_verification_sources.json)."""
    from ..services.bootstrap import REFERENCE_DIR

    data = json.loads((REFERENCE_DIR / "public_verification_sources.json").read_text(encoding="utf-8"))
    items = data["sources"]
    if category_key:
        items = [x for x in items if category_key in x["categories"]] + [x for x in items if "*" in x["categories"]]
    return {"note": data.get("note"), "items": items}


@router.post("/documents/{doc_id}/official-verification", status_code=202)
def request_official_verification(doc_id: str, body: OfficialRequest, auth: Auth = Depends(require_officer), db: Session = Depends(get_db)) -> dict:
    doc = get_or_404(db, Document, doc_id, "Document")
    v = db.get(DocumentVersion, doc.current_version_id) if doc.current_version_id else None
    if v is None or v.processing_status != "completed":
        raise HTTPException(409, "The document must finish processing first.")
    if body.method == "qr":
        links = qr_links(db, doc)
        if not body.qr_url or body.qr_url not in links:
            raise HTTPException(422, "Choose a QR link decoded from this document.")
        integ, claim_type = _qr_route(db, body.qr_url)
        if integ is None:
            a = unable_attempt(db, doc, "issuer_record", auth.officer.id,
                               "Unable to verify against official records: the QR link does not point to an allowlisted official address "
                               "(or that verification source is disabled), so it was not opened.",
                               "Verify through the issuer's official website directly. A QR code pointing to an unexpected address may itself warrant investigation.")
            db.commit()
            return {"attempt": ser.attempt(a), "job": None}
        payload = {"document_id": doc.id, "integration_id": integ.id, "claim_type": claim_type, "qr_url": body.qr_url}
    else:
        integ = db.get(Integration, body.integration_id) if body.integration_id else None
        claim_type = body.claim_type or "institution_identity"
        if integ is None or integ.kind != "official_record" or integ.adapter != "generic_rest" or not integ.enabled \
                or claim_type not in (integ.claim_types or []) or not integ.base_url:
            a = unable_attempt(db, doc, claim_type, auth.officer.id)
            db.commit()
            return {"attempt": ser.attempt(a), "job": None}
        payload = {"document_id": doc.id, "integration_id": integ.id, "claim_type": claim_type}
    job = enqueue(db, "official_verification", payload, entity_type="document", entity_id=doc.id, institution_id=doc.institution_id,
                  created_by_id=auth.officer.id, priority=40, max_attempts=2)
    audit.record(db, "official_verification.requested", officer=auth.officer, entity_type="document", entity_id=doc.id,
                 institution_id=doc.institution_id, case_id=doc.case_id, external_source=integ.name, after={"job_id": job.id}, ctx=auth.ctx)
    db.commit()
    return {"attempt": None, "job": ser.job(job)}


class OfficerCheckIn(BaseModel):
    claim_type: str = Field(min_length=3, max_length=60)
    source_name: str = Field(min_length=3, max_length=200)
    source_url: str = Field(min_length=8, max_length=500)
    outcome: str = Field(pattern="^(verified|mismatch|not_found|unable_to_verify)$")
    notes: str = Field(min_length=10, max_length=10000)
    evidence_document_id: str | None = None


@router.post("/documents/{doc_id}/official-checks", status_code=201)
def record_officer_check(doc_id: str, body: OfficerCheckIn, auth: Auth = Depends(require_officer), db: Session = Depends(get_db)) -> dict:
    """An officer records a check they performed directly against an official source (attributed, evidence-linked)."""
    doc = get_or_404(db, Document, doc_id, "Document")
    if urlsplit(body.source_url).scheme != "https":
        raise HTTPException(422, "The official source URL must use HTTPS.")
    evidence = [{"type": "source_url", "url": body.source_url}]
    if body.evidence_document_id:
        ev = get_or_404(db, Document, body.evidence_document_id, "Evidence document")
        if ev.institution_id != doc.institution_id:
            raise HTTPException(422, "Evidence must belong to the same institution.")
        evidence.append({"type": "document", "document_id": ev.id})
    from ..verification.jobs import document_claim

    claim, _ = document_claim(db, doc)
    a = ExternalVerificationAttempt(
        method="officer_recorded", institution_id=doc.institution_id, case_id=doc.case_id, document_id=doc.id,
        claim_type=body.claim_type, claim=audit.jsonable(claim), outcome=body.outcome,
        reason=f"Officer-recorded check: {body.notes}", source_name=body.source_name, source_url=body.source_url,
        evidence=evidence, attempted_by_id=auth.officer.id,
        next_action=None if body.outcome == "verified" else "Investigate the discrepancy with the institution and issuer.",
    )
    db.add(a)
    db.flush()
    audit.record(db, "official_verification.officer_recorded", officer=auth.officer, entity_type="document", entity_id=doc.id,
                 institution_id=doc.institution_id, case_id=doc.case_id, outcome=body.outcome, external_source=body.source_name,
                 reason=body.notes, evidence=evidence, ctx=auth.ctx)
    refresh_document_status(db, doc)
    if doc.case_id:
        enqueue(db, "evaluate_case", {"case_id": doc.case_id}, entity_type="case", entity_id=doc.case_id,
                institution_id=doc.institution_id, created_by_id=auth.officer.id, priority=50)
    db.commit()
    return {"attempt": ser.attempt(a)}


@router.get("/official-attempts/{attempt_id}/response")
def attempt_response(attempt_id: str, auth: Auth = Depends(require_officer), db: Session = Depends(get_db)) -> dict:
    a = get_or_404(db, ExternalVerificationAttempt, attempt_id, "Attempt")
    if not a.response_enc:
        raise HTTPException(404, "No source response was stored for this attempt.")
    data = json.loads(decrypt_secret(a.response_enc))
    audit.record(db, "official_verification.response_viewed", officer=auth.officer, entity_type="external_attempt", entity_id=a.id,
                 institution_id=a.institution_id, ctx=auth.ctx)
    db.commit()
    body = data.get("body", "")
    return {"content_type": data.get("content_type"), "sha256": a.response_sha256, "body": body[:200_000],
            "truncated": len(body) > 200_000, "official_record": (a.matched_values or {}).get("official_record")}


# ---------------------------------------------------------------- officer review decisions

class ReviewIn(BaseModel):
    outcome: str
    reason: str = Field(min_length=10, max_length=20000)
    evidence: list[dict] = Field(default_factory=list)
    supersedes_id: str | None = None


@router.post("/documents/{doc_id}/review", status_code=201)
def review_document(doc_id: str, body: ReviewIn, idempotency_key: str | None = Header(default=None, max_length=120),
                    auth: Auth = Depends(require_officer), db: Session = Depends(get_db)) -> dict:
    doc = get_or_404(db, Document, doc_id, "Document")
    if idempotency_key:
        existing = db.execute(select(Decision).where(Decision.idempotency_key == idempotency_key)).scalar_one_or_none()
        if existing:
            if existing.document_id != doc.id:
                raise HTTPException(409, "Idempotency key already used for a different record.")
            return ser.decision(existing, {auth.officer.id: auth.officer})
    if body.outcome not in REVIEW_OUTCOMES:
        raise HTTPException(422, "Unknown review outcome.")
    if body.supersedes_id:
        prev = db.get(Decision, body.supersedes_id)
        if prev is None or prev.document_id != doc.id:
            raise HTTPException(422, "The decision being corrected does not belong to this document.")
    d = Decision(institution_id=doc.institution_id, case_id=doc.case_id, document_id=doc.id, decision_type="document_review",
                 outcome=body.outcome, reason=body.reason.strip(), evidence=audit.jsonable(body.evidence),
                 supersedes_id=body.supersedes_id, idempotency_key=idempotency_key, decided_by_id=auth.officer.id,
                 evaluation_snapshot=audit.jsonable({"status": doc.status, "check_summary": doc.check_summary}))
    db.add(d)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        return ser.decision(db.execute(select(Decision).where(Decision.idempotency_key == idempotency_key)).scalar_one(),
                            {auth.officer.id: auth.officer})
    before = doc.status
    doc.review_status = "reviewed"
    doc.reviewed_by_id = auth.officer.id
    doc.reviewed_at = utcnow()
    refresh_document_status(db, doc)
    audit.record(db, "document.reviewed", officer=auth.officer, entity_type="document", entity_id=doc.id,
                 institution_id=doc.institution_id, case_id=doc.case_id, before={"status": before},
                 after={"status": doc.status, "outcome": d.outcome, "decision_id": d.id}, reason=d.reason,
                 evidence=d.evidence, outcome=d.outcome, ctx=auth.ctx)
    if doc.case_id:
        enqueue(db, "evaluate_case", {"case_id": doc.case_id}, entity_type="case", entity_id=doc.case_id,
                institution_id=doc.institution_id, created_by_id=auth.officer.id, priority=50)
    db.commit()
    return ser.decision(d, {auth.officer.id: auth.officer})
