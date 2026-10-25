"""Dashboard: summary statistics, charts and activity — all computed from database records."""

from __future__ import annotations

import statistics
from collections import defaultdict
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from .. import serializers as ser
from ..db import get_db
from ..deps import Auth, require_officer
from ..models import (
    CorrectiveAction,
    Decision,
    Document,
    DocumentCategory,
    DocumentVersion,
    ExternalVerificationAttempt,
    Finding,
    InspectionCase,
    Institution,
    Integration,
    Job,
    MediaAsset,
    Officer,
    VerificationCase,
    utcnow,
)
from ..verification.engine import CASE_STATUSES, DOCUMENT_STATUSES
from .common import page_params, paginate, parse_date_param

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])

DISCREPANCY_SOURCES = ("document_validation", "official_check", "cross_document", "requirement_check", "integrity")
OPEN = ("open", "under_review")


def _count(db: Session, model, *conds) -> int:
    return db.execute(select(func.count()).select_from(model).where(*conds)).scalar_one()


@router.get("/summary")
def summary(auth: Auth = Depends(require_officer), db: Session = Depends(get_db)) -> dict:
    total = _count(db, Institution, Institution.is_archived.is_(False))
    verified = db.execute(select(func.count(func.distinct(Document.institution_id))).where(Document.status == "verified_official")).scalar_one()
    pending_cases = _count(db, VerificationCase, VerificationCase.status != "decision_recorded")
    awaiting = _count(db, Document, Document.review_status != "reviewed", Document.status.not_in(("rejected", "uploaded", "processing")))
    processing = _count(db, Document, Document.status.in_(("uploaded", "processing")))
    discrepancies = _count(db, Finding, Finding.status.in_(OPEN), Finding.source.in_(DISCREPANCY_SOURCES))
    pending_insp = _count(db, InspectionCase, InspectionCase.status.in_(("open", "in_progress", "under_review")))
    attention_ids = {r for (r,) in db.execute(select(Finding.inspection_id).where(Finding.inspection_id.is_not(None), Finding.status.in_(OPEN)).distinct())}
    attention_ids |= {r for (r,) in db.execute(select(MediaAsset.inspection_id).where(MediaAsset.processing_status.in_(("failed", "partial"))).distinct())}
    integ_rows = db.execute(select(Integration).where(Integration.kind != "vision_model_provider")).scalars().all()
    failed_integ = [i for i in integ_rows if i.connection_status in ("failed", "auth_failed")]
    failed_jobs = _count(db, Job, Job.status == "failed")
    return {
        "cards": {
            "total_institutions": total,
            "institutions_with_verified_records": verified,
            "pending_verification_cases": pending_cases,
            "documents_awaiting_review": awaiting,
            "documents_processing": processing,
            "unresolved_discrepancies": discrepancies,
            "pending_inspections": pending_insp,
            "inspections_requiring_attention": len(attention_ids),
            "failed_integrations": len(failed_integ),
            "failed_jobs": failed_jobs,
        },
        "integrations": [{"id": i.id, "name": i.name, "status": i.connection_status, "enabled": i.enabled,
                          "last_failure_reason": i.last_failure_reason} for i in integ_rows],
        "definitions": {
            "institutions_with_verified_records": "Institutions with at least one document whose claims were supported by an official source.",
            "documents_awaiting_review": "Processed documents without a recorded officer review.",
            "unresolved_discrepancies": "Open findings from validation, official checks, cross-document comparison, requirements and integrity checks.",
            "inspections_requiring_attention": "Inspections with open findings or media that failed processing.",
            "failed_integrations": "Integrations whose most recent test or use failed.",
        },
        "generated_at": ser.iso(utcnow()),
    }


def _bucket(dt: datetime, grain: str) -> str:
    if grain == "month":
        return dt.strftime("%Y-%m")
    start = dt - timedelta(days=dt.weekday())
    return start.strftime("%Y-%m-%d")


def _buckets(start: datetime, end: datetime, grain: str) -> list[str]:
    out, cur = [], start
    while cur <= end:
        b = _bucket(cur, grain)
        if not out or out[-1] != b:
            out.append(b)
        cur += timedelta(days=1 if grain == "week" else 28)
    if _bucket(end, grain) not in out:
        out.append(_bucket(end, grain))
    return out


@router.get("/charts")
def charts(date_from: str | None = Query(None, alias="from"), date_to: str | None = Query(None, alias="to"),
           grain: str = Query("week", pattern="^(week|month)$"), auth: Auth = Depends(require_officer), db: Session = Depends(get_db)) -> dict:
    end = parse_date_param(date_to, end=True) or utcnow()
    start = parse_date_param(date_from) or (end - timedelta(days=90))
    keys = _buckets(start, end, grain)

    # 1. Document outcomes by upload period (current status of each document)
    docs = db.execute(select(Document.created_at, Document.status, Document.category_key, Document.review_status)
                      .where(Document.created_at.between(start, end))).all()
    outcomes = {k: defaultdict(int) for k in keys}
    for created, status, _c, _r in docs:
        outcomes.setdefault(_bucket(created, grain), defaultdict(int))[status] += 1
    statuses_seen = sorted({s for _, s, _c, _r in docs})

    # 2. Cases opened vs decided per period
    opened = db.execute(select(VerificationCase.opened_at).where(VerificationCase.opened_at.between(start, end))).scalars().all()
    decided = db.execute(select(Decision.decided_at, Decision.case_id).where(Decision.decision_type == "case_final",
                                                                              Decision.decided_at.between(start, end))).all()
    cases_series = {k: {"opened": 0, "decided": 0} for k in keys}
    for t in opened:
        cases_series.setdefault(_bucket(t, grain), {"opened": 0, "decided": 0})["opened"] += 1
    for t, _cid in decided:
        cases_series.setdefault(_bucket(t, grain), {"opened": 0, "decided": 0})["decided"] += 1

    # 3. Workload by state (open cases + documents awaiting review)
    workload = defaultdict(lambda: {"open_cases": 0, "documents_awaiting_review": 0})
    for state, n in db.execute(select(Institution.state, func.count()).join(VerificationCase, VerificationCase.institution_id == Institution.id)
                               .where(VerificationCase.status != "decision_recorded").group_by(Institution.state)).all():
        workload[state or "Not recorded"]["open_cases"] = n
    for state, n in db.execute(select(Institution.state, func.count()).join(Document, Document.institution_id == Institution.id)
                               .where(Document.review_status != "reviewed", Document.status.not_in(("rejected", "uploaded", "processing")))
                               .group_by(Institution.state)).all():
        workload[state or "Not recorded"]["documents_awaiting_review"] = n
    workload_rows = sorted(({"state": k, **v} for k, v in workload.items()),
                           key=lambda r: -(r["open_cases"] + r["documents_awaiting_review"]))[:12]

    # 4. Categories by review status
    cats = {c.key: c.label for c in db.execute(select(DocumentCategory)).scalars()}
    by_cat = defaultdict(lambda: defaultdict(int))
    for _t, status, cat, review in docs:
        bucket = "reviewed" if review == "reviewed" else ("processing" if status in ("uploaded", "processing") else "awaiting_review")
        by_cat[cat or "unclassified"][bucket] += 1
    cat_rows = sorted(({"category_key": k, "label": cats.get(k, "Unclassified"), **v} for k, v in by_cat.items()),
                      key=lambda r: -sum(v for kk, v in r.items() if isinstance(v, int)))[:12]

    # 5. Inspection findings by severity
    sev = defaultdict(lambda: {"open": 0, "closed": 0})
    for severity, status in db.execute(select(Finding.severity, Finding.status).where(Finding.inspection_id.is_not(None),
                                                                                       Finding.created_at.between(start, end))).all():
        sev[severity]["open" if status in OPEN else "closed"] += 1
    order = ["critical", "high", "medium", "low", "info", "not_assessed"]
    sev_rows = [{"severity": s, **sev[s]} for s in order if s in sev]

    # 6. Turnaround: days from case opening to first final decision, by decision period
    first_decision: dict[str, datetime] = {}
    for t, cid in sorted(decided):
        first_decision.setdefault(cid, t)
    case_open = dict(db.execute(select(VerificationCase.id, VerificationCase.opened_at).where(VerificationCase.id.in_(list(first_decision)))).all()) if first_decision else {}
    ta = defaultdict(list)
    for cid, t in first_decision.items():
        if cid in case_open:
            ta[_bucket(t, grain)].append((t - case_open[cid]).total_seconds() / 86400)
    turnaround = [{"period": k, "cases": len(ta.get(k, [])),
                   "median_days": round(statistics.median(ta[k]), 1) if ta.get(k) else None,
                   "mean_days": round(statistics.fmean(ta[k]), 1) if ta.get(k) else None} for k in keys]

    # 7. Recurring discrepancies by category over time
    disc = db.execute(select(Finding.created_at, Finding.category).where(Finding.source.in_(DISCREPANCY_SOURCES),
                                                                         Finding.created_at.between(start, end))).all()
    disc_cats = sorted({c for _, c in disc})
    disc_series = {k: defaultdict(int) for k in keys}
    for t, c in disc:
        disc_series.setdefault(_bucket(t, grain), defaultdict(int))[c] += 1

    return {
        "range": {"from": start.date().isoformat(), "to": end.date().isoformat(), "grain": grain},
        "document_outcomes": {"series": statuses_seen, "labels": DOCUMENT_STATUSES,
                              "rows": [{"period": k, **outcomes.get(k, {})} for k in keys]},
        "cases_flow": [{"period": k, **cases_series.get(k, {"opened": 0, "decided": 0})} for k in keys],
        "workload_by_state": workload_rows,
        "categories_by_status": cat_rows,
        "inspection_findings_by_severity": sev_rows,
        "turnaround": turnaround,
        "discrepancy_trends": {"series": disc_cats, "rows": [{"period": k, **disc_series.get(k, {})} for k in keys]},
        "case_status_labels": CASE_STATUSES,
    }


ACTIVITY_KINDS = ("uploads", "pending_tasks", "institutions_updated", "high_priority_discrepancies", "inspection_findings",
                  "corrective_awaiting", "failed_jobs", "decisions", "external_failures")


@router.get("/activity")
def activity(kind: str = Query("uploads"), q: str | None = None, pg: tuple[int, int] = Depends(page_params),
             auth: Auth = Depends(require_officer), db: Session = Depends(get_db)) -> dict:
    page, size = pg
    names = lambda ids: dict(db.execute(select(Institution.id, Institution.name).where(Institution.id.in_(ids))).all()) if ids else {}  # noqa: E731
    if kind == "uploads":
        stmt = select(DocumentVersion, Document).join(Document, Document.id == DocumentVersion.document_id)
        if q:
            stmt = stmt.where(Document.title.ilike(f"%{q}%"))
        total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
        rows = db.execute(stmt.order_by(DocumentVersion.uploaded_at.desc()).limit(size).offset((page - 1) * size)).all()
        n = names({d.institution_id for _v, d in rows})
        items = [{"type": "document", "id": d.id, "title": d.title, "institution_id": d.institution_id, "institution_name": n.get(d.institution_id),
                  "status": d.status, "status_label": DOCUMENT_STATUSES.get(d.status), "at": ser.iso(v.uploaded_at),
                  "detail": f"Version {v.version_no} · {v.original_filename}", "link": f"/app/verifier/documents/{d.id}"} for v, d in rows]
        return ser.page_of(items, total, page, size)
    if kind == "pending_tasks":
        stmt = select(VerificationCase).where(VerificationCase.status != "decision_recorded")
        rows, total = paginate(db, stmt.order_by(VerificationCase.due_date.is_(None), VerificationCase.due_date, VerificationCase.opened_at), page, size)
        n = names({r.institution_id for r in rows})
        items = [{"type": "case", "id": c.id, "title": f"{c.reference} · {c.title}", "institution_name": n.get(c.institution_id),
                  "status": c.status, "status_label": CASE_STATUSES.get(c.status), "priority": c.priority, "due": ser.iso(c.due_date),
                  "at": ser.iso(c.updated_at), "link": f"/app/verifier/cases/{c.id}"} for c in rows]
        return ser.page_of(items, total, page, size)
    if kind == "institutions_updated":
        rows, total = paginate(db, select(Institution).order_by(Institution.updated_at.desc()), page, size)
        items = [{"type": "institution", "id": i.id, "title": i.name, "status": i.verification_status,
                  "status_label": CASE_STATUSES.get(i.verification_status), "at": ser.iso(i.updated_at),
                  "detail": ", ".join(x for x in (i.district, i.state) if x), "link": f"/app/institutions/{i.id}"} for i in rows]
        return ser.page_of(items, total, page, size)
    if kind in ("high_priority_discrepancies", "inspection_findings"):
        stmt = select(Finding).where(Finding.status.in_(OPEN))
        stmt = stmt.where(Finding.severity.in_(("high", "critical")), Finding.source.in_(DISCREPANCY_SOURCES)) if kind == "high_priority_discrepancies" \
            else stmt.where(Finding.inspection_id.is_not(None))
        rows, total = paginate(db, stmt.order_by(Finding.created_at.desc()), page, size)
        n = names({r.institution_id for r in rows})
        items = [{"type": "finding", "id": f.id, "title": f.title, "institution_name": n.get(f.institution_id), "status": f.status,
                  "severity": f.severity, "source": f.source, "at": ser.iso(f.created_at), "detail": f.description[:200],
                  "link": (f"/app/inspections/{f.inspection_id}" if f.inspection_id else
                           f"/app/verifier/documents/{f.document_id}" if f.document_id else
                           f"/app/verifier/cases/{f.case_id}" if f.case_id else f"/app/institutions/{f.institution_id}")} for f in rows]
        return ser.page_of(items, total, page, size)
    if kind == "corrective_awaiting":
        rows, total = paginate(db, select(CorrectiveAction).where(CorrectiveAction.status.in_(("submitted", "under_review", "open")))
                               .order_by(CorrectiveAction.due_date.is_(None), CorrectiveAction.due_date), page, size)
        n = names({r.institution_id for r in rows})
        items = [{"type": "corrective_action", "id": c.id, "title": c.title, "institution_name": n.get(c.institution_id), "status": c.status,
                  "due": ser.iso(c.due_date), "at": ser.iso(c.updated_at), "link": f"/app/institutions/{c.institution_id}?tab=compliance"} for c in rows]
        return ser.page_of(items, total, page, size)
    if kind == "failed_jobs":
        rows, total = paginate(db, select(Job).where(Job.status == "failed").order_by(Job.finished_at.desc()), page, size)
        items = [{"type": "job", "id": j.id, "title": j.type.replace("_", " ").capitalize(), "status": j.status, "detail": (j.error or "")[:200],
                  "at": ser.iso(j.finished_at), "entity_type": j.entity_type, "entity_id": j.entity_id,
                  "link": "/app/reports?tab=jobs"} for j in rows]
        return ser.page_of(items, total, page, size)
    if kind == "decisions":
        rows, total = paginate(db, select(Decision).order_by(Decision.decided_at.desc()), page, size)
        n = names({r.institution_id for r in rows})
        officers = {o.id: o.full_name for o in db.execute(select(Officer)).scalars()}
        items = [{"type": "decision", "id": d.id, "title": d.outcome.replace("_", " ").capitalize(), "institution_name": n.get(d.institution_id),
                  "detail": f"{d.decision_type.replace('_', ' ')} by {officers.get(d.decided_by_id, 'unknown')}", "at": ser.iso(d.decided_at),
                  "link": f"/app/verifier/cases/{d.case_id}" if d.case_id and d.decision_type == "case_final" else
                          f"/app/verifier/documents/{d.document_id}" if d.document_id else "/app/reports?tab=decisions"} for d in rows]
        return ser.page_of(items, total, page, size)
    if kind == "external_failures":
        rows, total = paginate(db, select(ExternalVerificationAttempt).where(ExternalVerificationAttempt.outcome.in_(("error", "unable_to_verify", "not_found")))
                               .order_by(ExternalVerificationAttempt.attempted_at.desc()), page, size)
        n = names({r.institution_id for r in rows})
        items = [{"type": "external_attempt", "id": a.id, "title": f"{a.source_name or 'No official source'} — {a.outcome.replace('_', ' ')}",
                  "institution_name": n.get(a.institution_id), "status": a.outcome, "detail": a.reason[:200], "at": ser.iso(a.attempted_at),
                  "link": f"/app/verifier/documents/{a.document_id}" if a.document_id else None} for a in rows]
        return ser.page_of(items, total, page, size)
    return ser.page_of([], 0, page, size)


@router.get("/priorities")
def priorities(auth: Auth = Depends(require_officer), db: Session = Depends(get_db)) -> dict:
    now = utcnow()
    items = []
    for c in db.execute(select(VerificationCase).where(VerificationCase.status != "decision_recorded", VerificationCase.due_date.is_not(None),
                                                       VerificationCase.due_date < now + timedelta(days=7))
                        .order_by(VerificationCase.due_date).limit(5)).scalars():
        items.append({"kind": "case_due", "label": f"{c.reference} due {c.due_date.date().isoformat()}" + (" (overdue)" if c.due_date < now else ""),
                      "link": f"/app/verifier/cases/{c.id}", "urgency": "high" if c.due_date < now else "medium"})
    for f in db.execute(select(Finding).where(Finding.status.in_(OPEN), Finding.severity.in_(("critical", "high")))
                        .order_by(Finding.created_at.desc()).limit(5)).scalars():
        items.append({"kind": "finding", "label": f.title, "link": f"/app/verifier/documents/{f.document_id}" if f.document_id else
                      (f"/app/inspections/{f.inspection_id}" if f.inspection_id else f"/app/institutions/{f.institution_id}"), "urgency": "high"})
    for d in db.execute(select(Document).where(Document.review_status != "reviewed", Document.status.in_(("possible_tampering", "information_mismatch")))
                        .order_by(Document.created_at).limit(5)).scalars():
        items.append({"kind": "document", "label": f"Review flagged document: {d.title}", "link": f"/app/verifier/documents/{d.id}", "urgency": "high"})
    n_failed = _count(db, Job, Job.status == "failed")
    if n_failed:
        items.append({"kind": "jobs", "label": f"{n_failed} background job(s) failed and can be retried", "link": "/app/reports?tab=jobs", "urgency": "medium"})
    return {"items": items[:15]}


@router.get("/overview")
def overview(auth: Auth = Depends(require_officer), db: Session = Depends(get_db)) -> dict:
    """Officer-focused overview: is each institution's paperwork correct, and what needs a decision."""
    from ..verification.summary import institution_summary, simple_status

    docs = db.execute(select(Document)).scalars().all()
    doc_counts = {"verified": 0, "looks_correct": 0, "review": 0, "problem": 0, "checking": 0}
    for d in docs:
        doc_counts[simple_status(d)] += 1
    rows = []
    for inst in db.execute(select(Institution).where(Institution.is_archived.is_(False)).order_by(Institution.updated_at.desc()).limit(60)).scalars():
        s = institution_summary(db, inst)
        state = ("decided" if s["decision"] else "problem" if s["counts"]["problem"] else "review" if s["counts"]["review"]
                 else "checking" if s["counts"]["checking"] else "missing" if s["counts"]["missing_required"] else "empty" if not s["total_documents"] else "ready")
        rows.append({"id": inst.id, "name": inst.name, "location": ", ".join(x for x in (inst.district, inst.state) if x),
                     "documents": s["total_documents"], "counts": s["counts"], "verdict": s["verdict"], "state": state,
                     "decision": s["decision"]["outcome"] if s["decision"] else None})
    rank = {"problem": 0, "review": 1, "ready": 2, "missing": 3, "checking": 4, "empty": 5, "decided": 6}
    rows.sort(key=lambda r: rank[r["state"]])
    cards = {
        "institutions": len(rows),
        "documents_correct": doc_counts["verified"] + doc_counts["looks_correct"],
        "documents_problem": doc_counts["problem"] + doc_counts["review"],
        "awaiting_decision": sum(1 for r in rows if r["state"] in ("ready", "missing", "problem", "review") and r["documents"]),
        "decided": sum(1 for r in rows if r["state"] == "decided"),
        "analysing": doc_counts["checking"],
    }
    return {"cards": cards, "document_results": doc_counts, "institutions": rows}
