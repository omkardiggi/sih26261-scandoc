"""Evidence-based verification engine.

Each dimension (extraction, validation, official records, cross-document consistency,
integrity, requirements, inspections, officer decisions) is computed and stored
separately. Statuses are derived by transparent precedence rules documented below
and in docs/VERIFICATION_METHODOLOGY.md. Nothing here approves or rejects anything:
final decisions are only ever recorded by an officer.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict

from rapidfuzz import fuzz
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import audit
from ..documents.normalize import normalize_name
from ..models import (
    CheckRun,
    ComparisonResult,
    CorrectiveAction,
    Decision,
    Document,
    DocumentCategory,
    DocumentRequirement,
    DocumentVersion,
    ExternalVerificationAttempt,
    ExtractedField,
    Finding,
    InspectionCase,
    Institution,
    IntegrityCheck,
    Program,
    ValidationResult,
    VerificationCase,
    utcnow,
)

DOCUMENT_STATUSES = {
    "uploaded": "Uploaded",
    "processing": "Processing",
    "pending_official_verification": "Pending official verification",
    "verified_official": "Verified against official records",
    "information_mismatch": "Information mismatch",
    "possible_tampering": "Possible tampering detected",
    "unable_to_verify": "Unable to verify",
    "requires_manual_review": "Requires manual review",
    "rejected": "Rejected by an authorized officer",
}

CASE_STATUSES = {
    "submission_incomplete": "Submission incomplete",
    "verification_in_progress": "Verification in progress",
    "pending_review": "Pending review",
    "discrepancies_identified": "Discrepancies identified",
    "awaiting_corrective_documents": "Awaiting corrective documents",
    "ready_for_decision": "Ready for authorized officer decision",
    "decision_recorded": "Decision recorded",
}

# Platform rules for the severity of *document discrepancies* (documented; not regulatory severity).
DISCREPANCY_SEVERITY = {
    "identity": "high", "expiry": "high", "contradiction": "high", "program": "medium", "academic_year": "medium",
    "date": "medium", "arithmetic": "medium", "pages": "low", "format": "low", "requirement": "high",
}
SEVERITY_BASIS = "Platform discrepancy severity rules (docs/VERIFICATION_METHODOLOGY.md); not a regulatory determination."

MISMATCH_CATEGORIES = {"identity", "program", "academic_year", "date", "expiry", "contradiction", "arithmetic", "format"}


def latest_run(db: Session, kind: str, *, version_id: str | None = None, case_id: str | None = None) -> CheckRun | None:
    stmt = select(CheckRun).where(CheckRun.kind == kind, CheckRun.status == "completed")
    if version_id:
        stmt = stmt.where(CheckRun.version_id == version_id)
    if case_id:
        stmt = stmt.where(CheckRun.case_id == case_id)
    return db.execute(stmt.order_by(CheckRun.started_at.desc()).limit(1)).scalar_one_or_none()


def latest_validation(db: Session, version_id: str) -> list[ValidationResult]:
    run = latest_run(db, "document_validation", version_id=version_id)
    if run is None:
        return []
    return db.execute(select(ValidationResult).where(ValidationResult.run_id == run.id)).scalars().all()


def latest_integrity(db: Session, version_id: str) -> list[IntegrityCheck]:
    run = latest_run(db, "integrity", version_id=version_id)
    if run is None:
        return []
    return db.execute(select(IntegrityCheck).where(IntegrityCheck.run_id == run.id)).scalars().all()


def effective_decision(db: Session, *, case_id: str | None = None, document_id: str | None = None,
                       decision_type: str | None = None) -> Decision | None:
    """Most recent decision that has not been superseded by a later correction."""
    stmt = select(Decision)
    if case_id:
        stmt = stmt.where(Decision.case_id == case_id)
    if document_id:
        stmt = stmt.where(Decision.document_id == document_id)
    if decision_type:
        stmt = stmt.where(Decision.decision_type == decision_type)
    rows = db.execute(stmt.order_by(Decision.decided_at.desc())).scalars().all()
    superseded = {d.supersedes_id for d in rows if d.supersedes_id}
    for d in rows:
        if d.id not in superseded:
            return d
    return None


def official_summary(db: Session, document_id: str) -> dict:
    attempts = db.execute(
        select(ExternalVerificationAttempt).where(ExternalVerificationAttempt.document_id == document_id)
        .order_by(ExternalVerificationAttempt.attempted_at.desc())
    ).scalars().all()
    if not attempts:
        return {"status": "not_attempted", "attempts": 0,
                "message": "Not yet checked against official records."}
    latest_by_claim: dict[str, ExternalVerificationAttempt] = {}
    for a in attempts:
        latest_by_claim.setdefault(a.claim_type, a)
    outcomes = {a.outcome for a in latest_by_claim.values()}
    if "mismatch" in outcomes:
        status = "mismatch"
    elif outcomes == {"verified"}:
        status = "verified"
    elif "verified" in outcomes:
        status = "partially_verified"
    else:
        status = "unable"
    return {
        "status": status,
        "attempts": len(attempts),
        "claims": {k: {"outcome": a.outcome, "method": a.method, "source": a.source_name, "reason": a.reason,
                       "at": a.attempted_at.isoformat()} for k, a in latest_by_claim.items()},
        "officer_recorded_only": all(a.method == "officer_recorded" for a in latest_by_claim.values()),
    }


def document_check_summary(db: Session, doc: Document) -> dict:
    v = db.get(DocumentVersion, doc.current_version_id) if doc.current_version_id else None
    summary: dict = {"version_id": v.id if v else None}
    if v is None:
        return summary
    summary["processing"] = {"status": v.processing_status, "error": v.processing_error}
    fields_count = db.execute(select(func.count()).select_from(ExtractedField).where(ExtractedField.version_id == v.id)).scalar_one()
    summary["extraction"] = {"fields": fields_count, "text_layer": v.text_layer, "ocr_used": v.ocr_used,
                             "mean_ocr_confidence": v.mean_ocr_confidence, "pages": v.page_count}
    counts = defaultdict(int)
    vres = latest_validation(db, v.id)
    for r in vres:
        counts[r.status] += 1
    summary["validation"] = dict(counts)
    integ: dict = {"tampering": {"high": 0, "medium": 0, "low": 0}}
    for c in latest_integrity(db, v.id):
        sig = (c.details or {}).get("significance", "none")
        if c.dimension == "tampering":
            if sig in integ["tampering"]:
                integ["tampering"][sig] += 1
        else:
            prev = integ.get(c.dimension)
            order = ["fail", "error", "warning", "unable", "info", "pass", "not_applicable"]
            if prev is None or order.index(c.status) < order.index(prev):
                integ[c.dimension] = c.status
        if c.status == "fail" and c.dimension in ("file_integrity", "signature"):
            integ["tampering"]["high"] += 1
    summary["integrity"] = integ
    summary["official"] = official_summary(db, doc.id)
    dec = effective_decision(db, document_id=doc.id)
    summary["officer_review"] = {
        "review_status": doc.review_status,
        "decision": dec.outcome if dec else None,
        "decided_at": dec.decided_at.isoformat() if dec else None,
    }
    summary["_validation_rows"] = [(r.category, r.status, r.rule_key) for r in vres]
    return summary


def derive_document_status(doc: Document, v: DocumentVersion | None, s: dict, decision: Decision | None) -> tuple[str, str]:
    if decision is not None and decision.outcome == "rejected":
        return "rejected", f"Rejected by an authorized officer: {decision.reason}"
    if v is None:
        return "uploaded", "No file version."
    if v.processing_status in ("queued", "processing"):
        return "processing", "Extraction and checks are running."
    if v.processing_status == "blocked":
        return "requires_manual_review", v.processing_error or "Processing is blocked."
    if v.processing_status == "failed":
        return "requires_manual_review", "Automated processing failed; the original is preserved."
    tam = s.get("integrity", {}).get("tampering", {})
    rows = s.get("_validation_rows", [])
    fails = [r for r in rows if r[1] == "fail" and r[0] in MISMATCH_CATEGORIES]
    page_fail = [r for r in rows if r[1] == "fail" and r[0] == "pages"]
    warnings = [r for r in rows if r[1] == "warning" and r[0] in {"identity", "pages", "contradiction", "expiry"}]
    official = s.get("official", {})
    if tam.get("high"):
        return "possible_tampering", "One or more high-significance integrity indicators require examination."
    if fails or official.get("status") == "mismatch":
        parts = []
        if fails:
            parts.append(f"{len(fails)} validation check(s) failed")
        if official.get("status") == "mismatch":
            parts.append("an official source returned different information")
        return "information_mismatch", "; ".join(parts) + "."
    if page_fail or tam.get("medium") or warnings:
        return "requires_manual_review", "Some checks need an officer's judgement (see warnings and indicators)."
    if official.get("status") == "verified":
        return "verified_official", "All checked claims are supported by official-source evidence."
    if official.get("status") in ("unable", "partially_verified"):
        return "unable_to_verify", "Unable to verify against official records for one or more claims."
    return "pending_official_verification", "Document checks complete; official-record verification has not been completed."


def refresh_document_status(db: Session, doc: Document) -> None:
    v = db.get(DocumentVersion, doc.current_version_id) if doc.current_version_id else None
    s = document_check_summary(db, doc)
    status, reason = derive_document_status(doc, v, s, effective_decision(db, document_id=doc.id, decision_type="document_review"))
    s.pop("_validation_rows", None)
    doc.status, doc.status_reason, doc.check_summary = status, reason, audit.jsonable(s)


def _origin(*parts: str) -> str:
    raw = ":".join(parts)
    return raw if len(raw) <= 190 else raw[:150] + ":" + hashlib.sha1(raw.encode()).hexdigest()[:16]


def _upsert_finding(db: Session, origin_key: str, **kw) -> Finding:
    existing = db.execute(select(Finding).where(Finding.origin_key == origin_key)).scalar_one_or_none()
    if existing:
        return existing
    f = Finding(origin_key=origin_key, **kw)
    db.add(f)
    db.flush()
    return f


def sync_document_findings(db: Session, doc: Document, v: DocumentVersion) -> None:
    """Record discrepancies (failed validations, significant integrity indicators) as findings for officer review."""
    current: set[str] = set()
    for r in latest_validation(db, v.id):
        if r.status != "fail":
            continue
        current.add(_origin("val", v.id, r.rule_key, hashlib.sha1(r.message.encode()).hexdigest()[:10]))
        _upsert_finding(
            db, _origin("val", v.id, r.rule_key, hashlib.sha1(r.message.encode()).hexdigest()[:10]),
            institution_id=doc.institution_id, case_id=doc.case_id, document_id=doc.id, source="document_validation",
            category=r.category, title=f"{r.rule_label}: {doc.title}"[:300], description=r.message,
            severity=DISCREPANCY_SEVERITY.get(r.category, "medium"), severity_basis=SEVERITY_BASIS,
            evidence=[{"type": "validation_result", "id": r.id}, {"type": "document", "document_id": doc.id, "version_id": v.id}] + (r.evidence or []),
        )
    for c in latest_integrity(db, v.id):
        sig = (c.details or {}).get("significance")
        if c.status == "fail" or sig in ("high", "medium"):
            _upsert_finding(
                db, _origin("int", v.id, c.check_type),
                institution_id=doc.institution_id, case_id=doc.case_id, document_id=doc.id, source="integrity",
                category="tampering_indicator" if c.dimension == "tampering" else c.dimension,
                title=f"{c.title}: {doc.title}"[:300], description=c.summary,
                severity="high" if (c.status == "fail" or sig == "high") else "medium", severity_basis=SEVERITY_BASIS,
                evidence=[{"type": "integrity_check", "id": c.id}, {"type": "document", "document_id": doc.id, "version_id": v.id}] + (c.evidence or []),
            )
            current.add(_origin("int", v.id, c.check_type))
    # Automated findings whose check no longer flags (after a correction, registry change or re-run) are resolved.
    for f in db.execute(select(Finding).where(Finding.document_id == doc.id, Finding.status == "open",
                                              Finding.source.in_(("document_validation", "integrity")))).scalars():
        if f.origin_key and f.origin_key not in current:
            f.status, f.resolved_at = "resolved", utcnow()
            f.resolution_note = "Resolved automatically: the latest checks of this document no longer report this issue."


def _case_docs(db: Session, case: VerificationCase) -> list[Document]:
    return db.execute(select(Document).where(Document.case_id == case.id).order_by(Document.created_at)).scalars().all()


def applicable_requirements(db: Session, case: VerificationCase, inst: Institution) -> list[DocumentRequirement]:
    reqs = db.execute(
        select(DocumentRequirement).where(DocumentRequirement.active.is_(True),
                                          DocumentRequirement.application_type == case.application_type)
    ).scalars().all()
    return [r for r in reqs if (not r.academic_years or case.academic_year in r.academic_years)
            and (not r.institution_category or r.institution_category == inst.category)]


COMPARE_KEYS = {
    "institution_name": ("Institution name", "fuzzy"),
    "aicte_permanent_id": ("AICTE Permanent ID", "exact"),
    "application_id": ("Application ID", "exact"),
    "academic_year": ("Academic year", "exact"),
    "management_body": ("Trust / Society / Company", "fuzzy"),
    "establishment_year": ("Year of establishment", "exact"),
    "pincode": ("PIN code", "exact"),
    "certificate_number": ("Certificate number", "exact_within_category"),
}


def _compare_values(values: list[dict], mode: str) -> tuple[str, str]:
    norms = [v["normalized"] for v in values if v["normalized"]]
    if len({v["document_id"] for v in values}) < 2:
        return "single_source", "Only one document states this value; nothing to compare."
    if len(set(norms)) == 1:
        return "consistent", "All documents state the same value."
    if mode == "fuzzy":
        base = norms[0]
        scores = [fuzz.token_sort_ratio(base, n) for n in norms[1:]]
        if min(scores) >= 92:
            return "close_match", (f"Values differ slightly (lowest similarity {min(scores):.0f}/100), likely formatting or OCR; "
                                   "an officer must confirm they refer to the same entity.")
    return "conflict", "Documents state different values: " + "; ".join(sorted({f"'{v['value']}' ({v['title']})" for v in values}))


def evaluate_case(db: Session, case: VerificationCase, triggered_by_id: str | None = None, job_id: str | None = None) -> dict:
    inst = db.get(Institution, case.institution_id)
    docs = _case_docs(db, case)
    for d in docs:
        refresh_document_status(db, d)

    # --- requirements / missing documents
    req_run = CheckRun(kind="case_requirements", case_id=case.id, institution_id=inst.id, status="completed",
                       triggered_by_id=triggered_by_id, job_id=job_id, finished_at=utcnow())
    db.add(req_run)
    db.flush()
    reqs = applicable_requirements(db, case, inst)
    missing_mandatory, req_items = [], []
    if not reqs:
        db.add(ValidationResult(run_id=req_run.id, case_id=case.id, rule_key="requirements_configured",
                                rule_label="Document requirements configured", category="requirement", status="not_applicable",
                                message=f"No document requirements are configured for '{case.application_type}' in {case.academic_year}. "
                                        "Missing-document checks cannot be performed until requirements are configured with official sources."))
    for req in reqs:
        present = [d for d in docs if d.category_key == req.category_key and d.status != "rejected"]
        ev = [{"type": "document", "document_id": d.id} for d in present] + [{"type": "requirement", "id": req.id}]
        if present:
            status, msg = "pass", f"Provided: {', '.join(d.title for d in present)}."
            if req.validity_rule.get("requires_valid_on_review_date"):
                expiry = []
                for d in present:
                    if d.current_version_id:
                        expiry += [r for r in latest_validation(db, d.current_version_id) if r.rule_key in ("not_expired", "validity_stated")]
                if any(r.status == "fail" for r in expiry):
                    status, msg = "fail", "Provided, but the document's stated validity has expired."
                elif not any(r.rule_key == "not_expired" and r.status == "pass" for r in expiry):
                    status, msg = "warning", "Provided, but validity on the review date could not be established from the document."
        elif req.mandatory:
            status, msg = "fail", "Required document not provided."
            missing_mandatory.append(req)
        else:
            status, msg = "warning", f"Conditional requirement not provided — confirm whether it applies. Condition: {req.condition or 'see source'}."
        req_items.append({"requirement_id": req.id, "title": req.title, "status": status})
        db.add(ValidationResult(run_id=req_run.id, case_id=case.id, rule_key=f"requirement:{req.id}", rule_label=req.title[:200],
                                category="requirement", status=status, message=msg,
                                details={"source_reference": req.source_reference, "source_page": req.source_page,
                                         "mandatory": req.mandatory, "condition": req.condition,
                                         "confirmed": req.confirmed_at is not None, "category_key": req.category_key},
                                evidence=ev))
        if status == "fail":
            _upsert_finding(db, _origin("req", case.id, req.id, status, ",".join(sorted(d.id for d in present))),
                            institution_id=inst.id, case_id=case.id, source="requirement_check", category="requirement",
                            title=f"{'Missing' if not present else 'Invalid'} required document: {req.title}"[:300],
                            description=f"{msg} Source: {req.source_reference}.", severity="high", severity_basis=SEVERITY_BASIS,
                            evidence=ev)

    # --- cross-document comparison
    cmp_run = CheckRun(kind="cross_document", case_id=case.id, institution_id=inst.id, status="completed",
                       triggered_by_id=triggered_by_id, job_id=job_id, finished_at=utcnow())
    db.add(cmp_run)
    db.flush()
    by_key: dict[str, list[dict]] = defaultdict(list)
    programs: dict[str, list[dict]] = defaultdict(list)
    for d in docs:
        if not d.current_version_id or d.status == "rejected":
            continue
        rows = db.execute(select(ExtractedField).where(ExtractedField.version_id == d.current_version_id)).scalars().all()
        groups: dict[int, dict] = defaultdict(dict)
        for f in rows:
            entry = {"document_id": d.id, "version_id": f.version_id, "field_id": f.id, "value": f.value,
                     "normalized": f.normalized_value, "page_no": f.page_no, "bbox": f.bbox, "title": d.title,
                     "category_key": d.category_key}
            if f.group_index is not None:
                groups[f.group_index][f.field_key] = entry
            elif f.field_key in COMPARE_KEYS:
                by_key[f.field_key].append(entry)
        for g in groups.values():
            if "program_course" in g and "program_intake" in g:
                programs[normalize_name(g["program_course"]["value"])].append(g["program_intake"] | {"course": g["program_course"]["value"]})
    comparisons = []
    for key, values in by_key.items():
        label, mode = COMPARE_KEYS[key]
        if mode == "exact_within_category":
            continue  # certificate numbers differ legitimately across document types
        status, expl = _compare_values(values, mode)
        comparisons.append((key, label, status, values, expl))
    for course_norm, values in programs.items():
        status, expl = _compare_values(values, "exact")
        comparisons.append((f"program_intake:{course_norm}", f"Intake — {values[0]['course']}", status, values, expl))
    conflicts = 0
    for key, label, status, values, expl in comparisons:
        registry_value = None
        if key == "institution_name":
            registry_value = inst.name
        elif key == "aicte_permanent_id":
            registry_value = inst.aicte_permanent_id
        vals = audit.jsonable(values) + ([{"source": "registry", "value": registry_value}] if registry_value else [])
        db.add(ComparisonResult(run_id=cmp_run.id, case_id=case.id, institution_id=inst.id, field_key=key[:120],
                                label=label[:200], status=status, values=vals, explanation=expl))
        if status == "conflict":
            conflicts += 1
            _upsert_finding(db, _origin("cmp", case.id, key, hashlib.sha1(repr(sorted(str(v["normalized"]) for v in values)).encode()).hexdigest()[:10]),
                            institution_id=inst.id, case_id=case.id, source="cross_document", category="cross_document",
                            title=f"Conflicting {label.lower()} across documents"[:300], description=expl,
                            severity="high" if key in ("institution_name", "aicte_permanent_id", "application_id") else "medium",
                            severity_basis=SEVERITY_BASIS,
                            evidence=[{"type": "field", "field_id": v["field_id"], "document_id": v["document_id"],
                                       "page_no": v["page_no"], "bbox": v["bbox"]} for v in values])

    # --- derive case status
    status = derive_case_status(db, case, docs, missing_mandatory)
    case.status = status
    case.last_evaluated_at = utcnow()
    open_findings = _open_findings_count(db, case, docs)
    case.evaluation = {
        "documents": len(docs),
        "documents_by_status": _count_by(docs, "status"),
        "requirements": {"configured": len(reqs), "missing_mandatory": len(missing_mandatory), "items": req_items},
        "comparisons": {"total": len(comparisons), "conflicts": conflicts},
        "open_findings": open_findings,
        "status_label": CASE_STATUSES[status],
    }
    inst.verification_status = status
    return case.evaluation


def _count_by(items, attr) -> dict:
    out: dict[str, int] = defaultdict(int)
    for it in items:
        out[getattr(it, attr)] += 1
    return dict(out)


def _open_findings_count(db: Session, case: VerificationCase, docs: list[Document]) -> int:
    doc_ids = [d.id for d in docs]
    stmt = select(func.count()).select_from(Finding).where(Finding.status.in_(("open", "under_review")))
    stmt = stmt.where((Finding.case_id == case.id) | (Finding.document_id.in_(doc_ids) if doc_ids else False))
    return db.execute(stmt).scalar_one()


def derive_case_status(db: Session, case: VerificationCase, docs: list[Document], missing_mandatory: list) -> str:
    final = effective_decision(db, case_id=case.id, decision_type="case_final")
    if final is not None:
        return "decision_recorded"
    if not docs:
        return "submission_incomplete"
    if any(d.status in ("uploaded", "processing") for d in docs):
        return "verification_in_progress"
    if missing_mandatory:
        return "submission_incomplete"
    open_ca = db.execute(select(func.count()).select_from(CorrectiveAction).where(
        CorrectiveAction.case_id == case.id, CorrectiveAction.status.in_(("open", "submitted", "under_review")))).scalar_one()
    if open_ca:
        return "awaiting_corrective_documents"
    if _open_findings_count(db, case, docs) or any(d.status in ("information_mismatch", "possible_tampering") for d in docs):
        return "discrepancies_identified"
    if any(d.review_status != "reviewed" for d in docs if d.status != "rejected"):
        return "pending_review"
    return "ready_for_decision"


def outstanding_tasks(db: Session, case: VerificationCase) -> list[dict]:
    docs = _case_docs(db, case)
    tasks = []
    ev = case.evaluation or {}
    for item in ev.get("requirements", {}).get("items", []):
        if item["status"] == "fail":
            tasks.append({"kind": "missing_document", "label": f"Obtain required document: {item['title']}"})
    for d in docs:
        if d.status in ("uploaded", "processing"):
            tasks.append({"kind": "processing", "label": f"Processing in progress: {d.title}", "document_id": d.id})
        if d.review_status != "reviewed" and d.status not in ("rejected", "processing", "uploaded"):
            tasks.append({"kind": "review_document", "label": f"Review document: {d.title}", "document_id": d.id})
        off = (d.check_summary or {}).get("official", {})
        if off.get("status") in (None, "not_attempted", "unable", "partially_verified"):
            tasks.append({"kind": "official_verification", "label": f"Unverified against official records: {d.title}", "document_id": d.id})
    doc_ids = [d.id for d in docs]
    findings = db.execute(select(Finding).where(Finding.status.in_(("open", "under_review")),
                                                (Finding.case_id == case.id) | (Finding.document_id.in_(doc_ids) if doc_ids else False))).scalars().all()
    for f in findings:
        tasks.append({"kind": "finding", "label": f"Resolve finding: {f.title}", "finding_id": f.id, "severity": f.severity})
    cas = db.execute(select(CorrectiveAction).where(CorrectiveAction.case_id == case.id,
                                                    CorrectiveAction.status.in_(("open", "submitted", "under_review")))).scalars().all()
    for c in cas:
        tasks.append({"kind": "corrective_action", "label": f"Corrective action pending: {c.title}", "corrective_action_id": c.id})
    if case.status == "ready_for_decision":
        tasks.append({"kind": "decision", "label": "Record the officer decision for this case"})
    return tasks


def build_explanation(db: Session, case: VerificationCase) -> dict:
    """The nine-part verification explanation for a case."""
    from .. import serializers as ser

    docs = _case_docs(db, case)
    inst = db.get(Institution, case.institution_id)
    extracted, validations, integrity, official = [], [], [], []
    for d in docs:
        if not d.current_version_id:
            continue
        fields = db.execute(select(ExtractedField).where(ExtractedField.version_id == d.current_version_id,
                                                         ExtractedField.group_index.is_(None))).scalars().all()
        extracted.append({"document_id": d.id, "title": d.title, "category_key": d.category_key,
                          "fields": [ser.field(f) for f in fields]})
        validations.append({"document_id": d.id, "title": d.title,
                            "results": [ser.validation(r) for r in latest_validation(db, d.current_version_id)]})
        integrity.append({"document_id": d.id, "title": d.title,
                          "checks": [ser.integrity(c) for c in latest_integrity(db, d.current_version_id)]})
        attempts = db.execute(select(ExternalVerificationAttempt).where(ExternalVerificationAttempt.document_id == d.id)
                              .order_by(ExternalVerificationAttempt.attempted_at.desc())).scalars().all()
        official.append({
            "document_id": d.id, "title": d.title, "attempts": [ser.attempt(a) for a in attempts],
            "summary": official_summary(db, d.id),
            "statement": None if any(a.outcome == "verified" for a in attempts) else
            "Unable to verify against official records." if attempts or True else None,
        })
    cmp_run = latest_run(db, "cross_document", case_id=case.id)
    comparisons = [ser.comparison(c) for c in db.execute(select(ComparisonResult).where(ComparisonResult.run_id == cmp_run.id)).scalars()] if cmp_run else []
    req_run = latest_run(db, "case_requirements", case_id=case.id)
    requirements = [ser.validation(r) for r in db.execute(select(ValidationResult).where(ValidationResult.run_id == req_run.id)).scalars()] if req_run else []
    insp_ids = [i.id for i in db.execute(select(InspectionCase).where(
        (InspectionCase.case_id == case.id) | ((InspectionCase.institution_id == inst.id) & InspectionCase.case_id.is_(None)))).scalars()]
    insp_findings = db.execute(select(Finding).where(Finding.inspection_id.in_(insp_ids))).scalars().all() if insp_ids else []
    decisions = db.execute(select(Decision).where(Decision.case_id == case.id).order_by(Decision.decided_at.desc())).scalars().all()
    from ..models import Officer

    officers = {o.id: o for o in db.execute(select(Officer)).scalars()}
    return {
        "case": ser.case(case, institution_name=inst.name),
        "status_label": CASE_STATUSES.get(case.status, case.status),
        "sections": {
            "extracted_details": extracted,
            "field_validation": validations,
            "official_source_checks": official,
            "cross_document_comparisons": {"run_at": ser.iso(cmp_run.started_at) if cmp_run else None, "results": comparisons},
            "integrity_analysis": integrity,
            "required_documents": {"run_at": ser.iso(req_run.started_at) if req_run else None, "results": requirements},
            "inspection_findings": [ser.finding(f) for f in insp_findings],
            "outstanding_tasks": outstanding_tasks(db, case),
            "officer_decisions": [ser.decision(d, officers) for d in decisions],
        },
        "disclaimer": ("Automated checks assist officers. They do not approve, reject or certify any institution or "
                       "document; only the recorded officer decision carries that weight."),
    }


def category_label(db: Session, key: str | None) -> str | None:
    if not key:
        return None
    c = db.get(DocumentCategory, key)
    return c.label if c else key


def registry_programs(db: Session, institution_id: str, academic_year: str | None = None) -> list[Program]:
    stmt = select(Program).where(Program.institution_id == institution_id)
    if academic_year:
        stmt = stmt.where(Program.academic_year == academic_year)
    return db.execute(stmt).scalars().all()
