"""Background processing of an uploaded document version."""

from __future__ import annotations

import logging
from datetime import date

from sqlalchemy import delete, select

from .. import audit, storage
from ..config import get_settings
from ..db import session_scope
from ..jobs import PermanentJobError, enqueue
from ..jobs.worker import JobContext, handler
from ..models import (
    CheckRun,
    Document,
    DocumentCategory,
    DocumentPage,
    DocumentVersion,
    EvidenceArtifact,
    ExtractedField,
    ExtractedTable,
    Institution,
    IntegrityCheck,
    Program,
    TextBlock,
    ValidationResult,
    VerificationCase,
    utcnow,
)
from ..services.notify import notify
from .classify import classify
from .extract import extract_image, extract_pdf
from .fields import extract_fields
from .integrity import run_ai_checks, run_integrity_checks
from .validation import ValidationContext, run_document_rules

log = logging.getLogger("app.pipeline")


def _clear_partial_outputs(db, version_id: str) -> None:
    """Remove outputs of an earlier *incomplete* attempt (completed outputs are never rerun)."""
    field_ids = select(ExtractedField.id).where(ExtractedField.version_id == version_id)
    from ..models import FieldCorrection

    db.execute(delete(FieldCorrection).where(FieldCorrection.field_id.in_(field_ids)))
    for model in (TextBlock, ExtractedField, ExtractedTable, DocumentPage):
        db.execute(delete(model).where(model.version_id == version_id))


def _text_reader():
    """Return reader(jpeg)->text backed by the configured text-recognition workflow, or None."""
    wf = get_settings().document_text_workflow
    if not wf:
        return None
    from ..integrations.roboflow import infer
    from ..integrations.vision_report import summarize
    from ..models import Integration, ModelConfig

    with session_scope() as db:
        mc = db.execute(select(ModelConfig).where(ModelConfig.model_version == wf, ModelConfig.task_type == "workflow")).scalar_one_or_none()
        integ = db.get(Integration, mc.integration_id) if mc else None
        if mc is None or integ is None or not integ.enabled:
            return None
        db.expunge(mc)
        db.expunge(integ)

    def read(jpeg: bytes) -> dict:
        res = infer(integ, mc, jpeg)
        if not res.ok and res.status != "auth_failed":
            log.info("AI page analysis retry after: %s", res.message)
            res = infer(integ, mc, jpeg)  # one retry: hosted workflows occasionally fail transiently
        if not res.ok:
            raise RuntimeError(res.message)
        a = summarize(res.predictions)
        if not (a["text"] or a["header"] or a["footer"]):  # single-output text workflow
            a["text"] = "\n".join(p.geometry.get("text", "") for p in res.predictions if p.geometry_type == "text")
        return a

    return read


@handler("process_document_version")
def process_document_version(ctx: JobContext) -> dict:
    version_id = ctx.payload["version_id"]
    password = None  # passwords are never persisted; protected PDFs are blocked before queuing
    with session_scope() as db:
        v = db.get(DocumentVersion, version_id)
        if v is None:
            raise PermanentJobError("Document version no longer exists.")
        if v.processing_status == "completed":
            return {"skipped": "already processed"}
        if v.processing_status == "blocked":
            return {"skipped": "blocked", "reason": v.processing_error}
        doc = db.get(Document, v.document_id)
        v.processing_status = "processing"
        v.processing_error = None
        doc.status = "processing"
        _clear_partial_outputs(db, version_id)
        storage_key, processing_key, detected_type, sha = v.storage_key, v.processing_key, v.detected_type, v.sha256
        encrypted, active, meta = v.pdf_encrypted, list(v.active_content or []), dict(v.metadata_info or {})

    ctx.progress(0.02, "Loading original file", force=True)
    original = storage.get(storage_key)
    data = storage.get(processing_key) if processing_key else original

    if detected_type == "pdf":
        ex = extract_pdf(data, password, progress=ctx.progress)
    else:
        ex = extract_image(data, progress=ctx.progress)
    ctx.progress(0.62, "Extracting structured fields", force=True)
    fx = extract_fields(ex)

    ctx.progress(0.66, "Running integrity checks", force=True)
    checks, _codes = run_integrity_checks(
        data=data, detected_type=detected_type, sha256=sha, encrypted=encrypted, active_content=active,
        metadata=meta, ex=ex, fields=fx.fields, password=password, progress=ctx.progress, defer_ai=True,
    )
    if processing_key:
        # File-integrity and signature checks must use the untouched original.
        from .integrity import check_file_hash

        checks[0] = check_file_hash(original, sha)
        for c in checks:
            if c.dimension == "signature":
                c.status, c.summary = "unable", ("The original is password-protected; signatures were not validated on the "
                                                 "decrypted processing copy.")
    ctx.progress(0.93, "Saving results", force=True)

    # Persist page renders before the DB transaction (storage writes are not transactional).
    page_keys = {p.page_no: storage.put(p.preview_png, "renders") for p in ex.pages}
    artifact_keys = []
    for c in checks:
        for a in c.artifacts:
            artifact_keys.append((c, a, storage.put(a.png, "evidence")))

    with session_scope() as db:
        v = db.get(DocumentVersion, version_id)
        doc = db.get(Document, v.document_id)
        inst = db.get(Institution, doc.institution_id)
        case = db.get(VerificationCase, doc.case_id) if doc.case_id else None

        for p in ex.pages:
            db.add(DocumentPage(version_id=v.id, page_no=p.page_no, width_pt=p.width_pt, height_pt=p.height_pt,
                                render_key=page_keys[p.page_no], render_width=p.preview_w, render_height=p.preview_h,
                                text_source=p.text_source, char_count=p.char_count, ocr_confidence=p.ocr_confidence,
                                is_blank=p.is_blank, unreadable=p.unreadable))
        for ln in ex.all_lines():
            db.add(TextBlock(version_id=v.id, page_no=ln.page_no, seq=ln.seq, text=ln.text[:4000], bbox=ln.bbox,
                             confidence=ln.confidence, source=ln.source))
        for t in ex.tables:
            is_prog = any("intake" in h.lower() for h in t.header)
            db.add(ExtractedTable(version_id=v.id, page_no=t.page_no, bbox=t.bbox, header=t.header, rows=t.rows,
                                  table_type="programs" if is_prog else "generic", source=t.source))

        field_ids: dict[str, str] = {}
        for c in fx.fields:
            f = ExtractedField(version_id=v.id, document_id=doc.id, field_key=c.field_key, label=c.label,
                               value=(c.value or "")[:4000], normalized_value=c.normalized, original_text=(c.original_text or "")[:4000],
                               page_no=c.page_no, bbox=c.bbox, confidence=c.confidence, confidence_basis=c.basis,
                               method=c.method, group_index=c.group_index)
            db.add(f)
            db.flush()
            if c.group_index is None:
                field_ids[c.field_key] = f.id

        # Classification (officer-assigned categories are never overridden).
        categories = db.execute(select(DocumentCategory)).scalars().all()
        result = classify(ex.full_text(), categories)
        if doc.category_source != "officer":
            doc.category_key = result["category_key"]
            doc.category_source = "auto"
        doc.classification = result
        fmap = {c.field_key: c for c in fx.fields if c.group_index is None}
        if not doc.academic_year and "academic_year" in fmap:
            doc.academic_year = fmap["academic_year"].normalized
        if not doc.issuing_authority and "issuing_authority" in fmap:
            doc.issuing_authority = fmap["issuing_authority"].value[:300]

        v.page_count = len(ex.pages)
        v.text_layer = ex.text_layer
        v.ocr_used = ex.ocr_used
        v.mean_ocr_confidence = ex.mean_ocr_confidence

        dup = None
        if v.duplicate_of_version_id:
            ov = db.get(DocumentVersion, v.duplicate_of_version_id)
            od = db.get(Document, ov.document_id) if ov else None
            if od:
                dup = {"document_id": od.id, "version_id": ov.id, "title": od.title,
                       "uploaded_at": ov.uploaded_at.date().isoformat(), "other_institution": od.institution_id != doc.institution_id}

        category = db.get(DocumentCategory, doc.category_key) if doc.category_key else None
        registry_programs = db.execute(select(Program).where(Program.institution_id == inst.id)).scalars().all()
        vctx = ValidationContext(
            fields=fmap, extraction=fx, category=category, institution=inst, case=case,
            registry_programs=registry_programs, field_ids=field_ids,
            pages=[{"page_no": p.page_no, "is_blank": p.is_blank, "unreadable": p.unreadable,
                    "ocr_confidence": p.ocr_confidence, "text_source": p.text_source} for p in ex.pages],
            duplicate_of=dup, review_date=date.today(), low_ocr_threshold=get_settings().ocr_low_confidence,
            full_text=ex.full_text(),
        )
        rules = run_document_rules(vctx, ex.tables)

        vrun = CheckRun(kind="document_validation", version_id=v.id, case_id=doc.case_id, institution_id=inst.id,
                        status="completed", job_id=ctx.job_id, finished_at=utcnow(), triggered_by_id=ctx.created_by_id)
        irun = CheckRun(kind="integrity", version_id=v.id, case_id=doc.case_id, institution_id=inst.id,
                        status="completed", job_id=ctx.job_id, finished_at=utcnow(), triggered_by_id=ctx.created_by_id)
        db.add_all([vrun, irun])
        db.flush()
        for r in rules:
            db.add(ValidationResult(run_id=vrun.id, case_id=doc.case_id, document_id=doc.id, version_id=v.id,
                                    rule_key=r.rule_key, rule_label=r.rule_label, category=r.category, status=r.status,
                                    message=r.message, details=audit.jsonable(r.details), evidence=audit.jsonable(r.evidence)))
            if r.status == "fail" and r.evidence:
                for e in r.evidence:
                    if e.get("type") == "field" and e.get("field_key") in field_ids:
                        f = db.get(ExtractedField, field_ids[e["field_key"]])
                        f.validation_status = "invalid"
                        f.validation_message = r.message[:2000]
            elif r.status == "pass":
                for e in r.evidence:
                    if e.get("type") == "field" and e.get("field_key") in field_ids:
                        f = db.get(ExtractedField, field_ids[e["field_key"]])
                        if f.validation_status == "unchecked":
                            f.validation_status = "valid"
            elif r.status == "warning":
                for e in r.evidence:
                    if e.get("type") == "field" and e.get("field_key") in field_ids:
                        f = db.get(ExtractedField, field_ids[e["field_key"]])
                        if f.validation_status in ("unchecked", "valid"):
                            f.validation_status = "warning"
                            f.validation_message = r.message[:2000]
        artifact_ids: dict[int, list[str]] = {}
        for c, a, key in artifact_keys:
            ea = EvidenceArtifact(version_id=v.id, kind=a.kind, page_no=a.page_no, storage_key=key, width=a.width,
                                  height=a.height, meta=audit.jsonable(a.meta))
            db.add(ea)
            db.flush()
            artifact_ids.setdefault(id(c), []).append(ea.id)
        for c in checks:
            details = dict(c.details)
            if id(c) in artifact_ids:
                details["artifact_ids"] = artifact_ids[id(c)]
            db.add(IntegrityCheck(run_id=irun.id, version_id=v.id, check_type=c.check_type, dimension=c.dimension,
                                  status=c.status, title=c.title, summary=c.summary, details=audit.jsonable(details),
                                  evidence=audit.jsonable(c.evidence)))
        v.processing_status = "completed"
        v.processed_at = utcnow()
        db.flush()

        from ..verification.engine import refresh_document_status, sync_document_findings

        refresh_document_status(db, doc)
        sync_document_findings(db, doc, v)
        audit.record(db, "document.processed", entity_type="document", entity_id=doc.id, institution_id=inst.id,
                     case_id=doc.case_id, summary=f"Processed '{doc.title}' (version {v.version_no})",
                     after={"status": doc.status, "category": doc.category_key, "pages": v.page_count,
                            "fields": len(fx.fields), "ocr_used": v.ocr_used},
                     evidence=[{"type": "document_version", "id": v.id, "sha256": v.sha256}])
        # Verify automatically through the issuer page linked by the document's QR code (official hosts only).
        from ..api.documents import _qr_route
        from ..verification.jobs import qr_links

        for url in qr_links(db, doc):
            integ, claim_type = _qr_route(db, url)
            if integ is not None:
                enqueue(db, "official_verification", {"document_id": doc.id, "integration_id": integ.id, "claim_type": claim_type, "qr_url": url},
                        idempotency_key=f"qr:{v.id}", entity_type="document", entity_id=doc.id, institution_id=inst.id,
                        created_by_id=ctx.created_by_id, priority=40, max_attempts=2)
                break
        if doc.case_id:
            enqueue(db, "evaluate_case", {"case_id": doc.case_id}, entity_type="case", entity_id=doc.case_id,
                    institution_id=inst.id, created_by_id=ctx.created_by_id, priority=50)
        # Slow page re-reading (AI) runs next, so the document's results are available meanwhile.
        enqueue(db, "analyse_document_pages", {"version_id": v.id}, idempotency_key=f"ai:{v.id}", entity_type="document",
                entity_id=doc.id, institution_id=inst.id, created_by_id=ctx.created_by_id, priority=60, max_attempts=2)
        if v.uploaded_by_id:
            notify(db, v.uploaded_by_id, "document_processed", f"Processing complete: {doc.title}",
                   f"Status: {doc.status.replace('_', ' ')}.", f"/app/verifier/documents/{doc.id}")
        return {"document_id": doc.id, "status": doc.status, "pages": len(ex.pages), "fields": len(fx.fields)}


def _on_failure(ctx: JobContext, message: str) -> None:
    with session_scope() as db:
        v = db.get(DocumentVersion, ctx.payload.get("version_id"))
        if v is None:
            return
        v.processing_status = "failed"
        v.processing_error = message[:2000]
        doc = db.get(Document, v.document_id)
        doc.status = "requires_manual_review"
        doc.status_reason = "Automated processing failed; the original file is preserved. Retry processing or review manually."
        audit.record(db, "document.processing_failed", entity_type="document", entity_id=doc.id,
                     institution_id=doc.institution_id, case_id=doc.case_id, outcome="failed", summary=message[:500])
        if v.uploaded_by_id:
            notify(db, v.uploaded_by_id, "job_failed", f"Processing failed: {doc.title}", message[:300],
                   f"/app/verifier/documents/{doc.id}")


process_document_version.on_final_failure = _on_failure  # type: ignore[attr-defined]


@handler("analyse_document_pages")
def analyse_document_pages(ctx: JobContext) -> dict:
    """Second stage: re-read every page with the AI reader, then update the document's integrity results and status."""
    from ..verification.engine import latest_run, refresh_document_status, sync_document_findings

    vid = ctx.payload["version_id"]
    with session_scope() as db:
        v = db.get(DocumentVersion, vid)
        if v is None or v.processing_status != "completed":
            return {"skipped": "not processed"}
        run = latest_run(db, "integrity", version_id=v.id)
        pending = run and db.execute(select(IntegrityCheck).where(IntegrityCheck.run_id == run.id, IntegrityCheck.check_type == "ai_reading")).scalar_one_or_none()
        if not pending or not (pending.details or {}).get("pending"):
            return {"skipped": "already analysed"}
        key, detected_type = v.processing_key or v.storage_key, v.detected_type
    data = storage.get(key)
    ctx.progress(0.1, "Preparing pages", force=True)
    ex = extract_pdf(data) if detected_type == "pdf" else extract_image(data)
    fields = extract_fields(ex).fields
    ctx.progress(0.25, "AI analysis of every page (reading, signatures, stamps, photographs)", force=True)
    checks = run_ai_checks(data=data, detected_type=detected_type, ex=ex, fields=fields, password=None, text_reader=_text_reader())
    with session_scope() as db:
        v = db.get(DocumentVersion, vid)
        doc = db.get(Document, v.document_id)
        run = latest_run(db, "integrity", version_id=v.id)
        row = db.execute(select(IntegrityCheck).where(IntegrityCheck.run_id == run.id, IntegrityCheck.check_type == "ai_reading")).scalar_one()
        first, rest = checks[0], checks[1:]
        row.check_type, row.dimension, row.status, row.title, row.summary = first.check_type, first.dimension, first.status, first.title, first.summary
        row.details, row.evidence = audit.jsonable(first.details), audit.jsonable(first.evidence)
        for c in rest:
            db.add(IntegrityCheck(run_id=run.id, version_id=v.id, check_type=c.check_type, dimension=c.dimension, status=c.status,
                                  title=c.title, summary=c.summary, details=audit.jsonable(c.details), evidence=audit.jsonable(c.evidence)))
        db.flush()
        if doc.current_version_id == v.id:
            refresh_document_status(db, doc)
            sync_document_findings(db, doc, v)
            if doc.case_id:
                enqueue(db, "evaluate_case", {"case_id": doc.case_id}, entity_type="case", entity_id=doc.case_id,
                        institution_id=doc.institution_id, created_by_id=ctx.created_by_id, priority=50)
        audit.record(db, "document.pages_analysed", entity_type="document", entity_id=doc.id, institution_id=doc.institution_id,
                     case_id=doc.case_id, summary=f"Page re-reading completed for '{doc.title}': {first.status}",
                     after={"checks": [c.check_type for c in checks], "status": doc.status})
    return {"checks": len(checks), "status": first.status}


def _on_ai_failure(ctx: JobContext, message: str) -> None:
    with session_scope() as db:
        from ..verification.engine import latest_run

        run = latest_run(db, "integrity", version_id=ctx.payload.get("version_id"))
        row = run and db.execute(select(IntegrityCheck).where(IntegrityCheck.run_id == run.id, IntegrityCheck.check_type == "ai_reading")).scalar_one_or_none()
        if row is not None and (row.details or {}).get("pending"):
            row.status, row.summary, row.details = "unable", "The AI analysis of the pages could not be completed; review the pages directly.", {"error": message[:300]}


analyse_document_pages.on_final_failure = _on_ai_failure  # type: ignore[attr-defined]
