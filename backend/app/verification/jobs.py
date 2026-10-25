"""Background jobs for case evaluation and official-record verification."""

from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import audit
from ..db import session_scope
from ..jobs import PermanentJobError
from ..jobs.worker import JobContext, handler
from ..models import (
    Document,
    DocumentVersion,
    ExternalVerificationAttempt,
    ExtractedField,
    Integration,
    Officer,
    VerificationCase,
    utcnow,
)
from ..security.crypto import encrypt_secret, sha256_hex
from ..services.notify import notify
from .engine import evaluate_case, latest_integrity, refresh_document_status, sync_document_findings

NO_SOURCE_REASON = "Unable to verify against official records: no authorised official source is configured and operational for this claim."
NO_SOURCE_NEXT = ("Configure an authorised integration in Institution Verifier → Official sources, or consult the official source "
                  "directly and record the check with its evidence.")


@handler("evaluate_case")
def evaluate_case_job(ctx: JobContext) -> dict:
    with session_scope() as db:
        case = db.get(VerificationCase, ctx.payload["case_id"])
        if case is None:
            raise PermanentJobError("Case no longer exists.")
        before = case.status
        result = evaluate_case(db, case, triggered_by_id=ctx.created_by_id, job_id=ctx.job_id)
        audit.record(db, "case.evaluated", entity_type="case", entity_id=case.id, institution_id=case.institution_id,
                     case_id=case.id, summary=f"Case {case.reference} evaluated",
                     before={"status": before}, after={"status": case.status, **{k: result[k] for k in ("documents", "open_findings")}})
        return {"status": case.status}


def document_claim(db: Session, doc: Document) -> tuple[dict, list[dict]]:
    """Values extracted from the document's current version (officer corrections take precedence)."""
    from ..models import FieldCorrection

    rows = db.execute(select(ExtractedField).where(ExtractedField.version_id == doc.current_version_id)).scalars().all()
    corr = {}
    for c in db.execute(select(FieldCorrection).where(FieldCorrection.field_id.in_([r.id for r in rows])).order_by(FieldCorrection.corrected_at)).scalars():
        corr[c.field_id] = c.corrected_value
    claim, groups = {}, {}
    for r in rows:
        value = corr.get(r.id, r.value)
        if r.group_index is None:
            claim[r.field_key] = value
        else:
            groups.setdefault(r.group_index, {})[r.field_key] = value
    programs = []
    from ..documents.normalize import parse_int

    for g in groups.values():
        if "program_course" in g:
            programs.append({"course": g["program_course"], "level": g.get("program_level"), "intake": parse_int(g.get("program_intake"))})
    return claim, programs


def qr_links(db: Session, doc: Document) -> list[str]:
    links = []
    for c in latest_integrity(db, doc.current_version_id):
        if c.check_type == "qr_barcode":
            for code in (c.details or {}).get("codes", []):
                links += code.get("urls", [])
    return links


def record_attempt(db: Session, *, doc: Document, integration: Integration | None, claim_type: str, claim: dict,
                   outcome, officer_id: str | None, job_id: str | None) -> ExternalVerificationAttempt:
    body = outcome.response_body
    a = ExternalVerificationAttempt(
        integration_id=integration.id if integration else None, method="automated", institution_id=doc.institution_id,
        case_id=doc.case_id, document_id=doc.id, claim_type=claim_type, claim=audit.jsonable(claim),
        request_summary=audit.jsonable(outcome.request_summary), outcome=outcome.outcome, reason=outcome.reason,
        next_action=outcome.next_action, http_status=outcome.http_status, duration_ms=outcome.duration_ms,
        source_name=outcome.source_name, source_url=outcome.source_url,
        response_sha256=sha256_hex(body) if body else None,
        response_enc=encrypt_secret(json.dumps({"content_type": outcome.response_content_type,
                                                "body": body.decode("utf-8", "replace")})) if body else None,
        matched_values=audit.jsonable({"comparisons": outcome.matched_values, "official_record": outcome.official_record}) if (outcome.matched_values or outcome.official_record) else None,
        attempted_by_id=officer_id, job_id=job_id,
    )
    db.add(a)
    db.flush()
    if integration is not None and outcome.connection_ok is not None:
        now = utcnow()
        if outcome.connection_ok:
            integration.connection_status = "operational"
            integration.last_success_at = now
        else:
            integration.connection_status = "auth_failed" if outcome.auth_failed else "failed"
            integration.last_failure_at = now
            integration.last_failure_reason = outcome.reason[:500]
    officer = db.get(Officer, officer_id) if officer_id else None
    audit.record(db, "official_verification.attempted", officer=officer, entity_type="document", entity_id=doc.id,
                 institution_id=doc.institution_id, case_id=doc.case_id, outcome=outcome.outcome,
                 external_source=outcome.source_name, summary=outcome.reason[:500],
                 after={"attempt_id": a.id, "http_status": outcome.http_status, "response_sha256": a.response_sha256})
    refresh_document_status(db, doc)
    if outcome.outcome in ("mismatch", "not_found"):
        from .engine import _upsert_finding, _origin

        _upsert_finding(db, _origin("ext", a.id), institution_id=doc.institution_id, case_id=doc.case_id, document_id=doc.id,
                        source="official_check", category="official_record",
                        title=f"Official record {'differs from' if outcome.outcome == 'mismatch' else 'not found for'}: {doc.title}"[:300],
                        description=outcome.reason, severity="high", severity_basis="Official source response",
                        evidence=[{"type": "external_attempt", "id": a.id}, {"type": "document", "document_id": doc.id}])
    return a


def unable_attempt(db: Session, doc: Document, claim_type: str, officer_id: str | None, reason: str = NO_SOURCE_REASON,
                   next_action: str = NO_SOURCE_NEXT) -> ExternalVerificationAttempt:
    from ..integrations.official import VerificationOutcome

    claim, _ = document_claim(db, doc)
    return record_attempt(db, doc=doc, integration=None, claim_type=claim_type, claim=claim,
                          outcome=VerificationOutcome("unable_to_verify", reason, next_action), officer_id=officer_id, job_id=None)


@handler("official_verification")
def official_verification_job(ctx: JobContext) -> dict:
    from ..integrations.official import verify_generic_qr, verify_rest, verify_via_qr

    p = ctx.payload
    with session_scope() as db:
        doc = db.get(Document, p["document_id"])
        integration = db.get(Integration, p["integration_id"])
        if doc is None or integration is None:
            raise PermanentJobError("Document or integration no longer exists.")
        claim, programs = document_claim(db, doc)
        adapter = integration.adapter
        integration_snapshot = (integration.id, adapter)
    # Network call happens outside any open transaction.
    with session_scope() as db:
        integration = db.get(Integration, integration_snapshot[0])
        if adapter == "aicte_qr_status":
            outcome = verify_via_qr(integration, p["qr_url"], claim, programs)
        elif adapter == "issuer_qr_generic":
            outcome = verify_generic_qr(integration, p["qr_url"], claim)
        else:
            outcome = verify_rest(integration, p["claim_type"], claim)
        doc = db.get(Document, p["document_id"])
        a = record_attempt(db, doc=doc, integration=integration, claim_type=p["claim_type"], claim=claim,
                           outcome=outcome, officer_id=ctx.created_by_id, job_id=ctx.job_id)
        sync_document_findings(db, doc, db.get(DocumentVersion, doc.current_version_id))
        if ctx.created_by_id:
            notify(db, ctx.created_by_id, "official_check", f"Official check: {outcome.outcome.replace('_', ' ')} — {doc.title}",
                   outcome.reason[:300], f"/app/verifier/documents/{doc.id}")
        if doc.case_id:
            from ..jobs import enqueue

            enqueue(db, "evaluate_case", {"case_id": doc.case_id}, entity_type="case", entity_id=doc.case_id,
                    institution_id=doc.institution_id, created_by_id=ctx.created_by_id, priority=50)
        return {"attempt_id": a.id, "outcome": outcome.outcome}


def _on_official_failure(ctx: JobContext, message: str) -> None:
    with session_scope() as db:
        doc = db.get(Document, ctx.payload.get("document_id"))
        if doc is None:
            return
        integration = db.get(Integration, ctx.payload.get("integration_id"))
        from ..integrations.official import VerificationOutcome

        record_attempt(db, doc=doc, integration=integration, claim_type=ctx.payload.get("claim_type", "unknown"), claim={},
                       outcome=VerificationOutcome("error", f"The verification job failed: {message[:300]}", "Retry the check."),
                       officer_id=ctx.created_by_id, job_id=ctx.job_id)


official_verification_job.on_final_failure = _on_official_failure  # type: ignore[attr-defined]


@handler("revalidate_institution")
def revalidate_institution_job(ctx: JobContext) -> dict:
    """Re-check an institution's processed documents against its (changed) registry record, then re-evaluate its cases."""
    from ..api.documents import _revalidate

    inst_id = ctx.payload["institution_id"]
    with session_scope() as db:
        docs = db.execute(select(Document).where(Document.institution_id == inst_id)).scalars().all()
        n = 0
        for d in docs:
            v = db.get(DocumentVersion, d.current_version_id) if d.current_version_id else None
            if v is not None and v.processing_status == "completed":
                _revalidate(db, d, None)
                n += 1
        for c in db.execute(select(VerificationCase).where(VerificationCase.institution_id == inst_id)).scalars():
            evaluate_case(db, c, triggered_by_id=ctx.created_by_id, job_id=ctx.job_id)
        audit.record(db, "institution.dependent_checks_refreshed", entity_type="institution", entity_id=inst_id, institution_id=inst_id,
                     summary=f"Re-validated {n} document(s) after a registry change", after={"documents": n})
        return {"documents": n}
