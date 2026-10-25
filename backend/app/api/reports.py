"""Reports generated from database records with the applied filters (JSON preview, CSV, PDF)."""

from __future__ import annotations

import csv
import io
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import audit
from .. import serializers as ser
from ..db import get_db
from ..deps import Auth, require_officer
from ..models import (
    CorrectiveAction,
    Decision,
    Document,
    DocumentCategory,
    ExternalVerificationAttempt,
    Finding,
    InspectionCase,
    Institution,
    Integration,

    Officer,
    VerificationCase,
    utcnow,
)
from ..verification.engine import CASE_STATUSES, DOCUMENT_STATUSES, build_explanation
from .audit_api import audit_query
from .common import get_or_404, parse_date_param

router = APIRouter(prefix="/api/reports", tags=["reports"])

REPORTS = {
    "institutions": "Institution verification report",
    "documents": "Document verification report",
    "pending_cases": "Pending-case report",
    "discrepancies": "Discrepancy report",
    "inspections": "Inspection report",
    "corrective_actions": "Corrective action report",
    "decisions": "Historical decisions",
    "officer_activity": "Officer activity report",
    "integrations": "Integration status report",
    "audit": "Audit records",
}
MAX_ROWS = 5000


def _range(stmt, col, f, t):
    if (a := parse_date_param(f)):
        stmt = stmt.where(col >= a)
    if (b := parse_date_param(t, end=True)):
        stmt = stmt.where(col <= b)
    return stmt


def _names(db, ids):
    return dict(db.execute(select(Institution.id, Institution.name).where(Institution.id.in_(ids))).all()) if ids else {}


def build(db: Session, kind: str, p: dict) -> tuple[list[str], list[dict]]:
    f, t, inst, state = p.get("from"), p.get("to"), p.get("institution_id"), p.get("state")
    officers = {o.id: o.full_name for o in db.execute(select(Officer)).scalars()}
    if kind == "institutions":
        stmt = select(Institution).where(Institution.is_archived.is_(False))
        if state:
            stmt = stmt.where(Institution.state == state)
        if inst:
            stmt = stmt.where(Institution.id == inst)
        if p.get("status"):
            stmt = stmt.where(Institution.verification_status == p["status"])
        rows = db.execute(stmt.order_by(Institution.name).limit(MAX_ROWS)).scalars().all()
        cols = ["Institution", "AICTE Permanent ID", "State", "District", "Category", "Verification status",
                "Approval status (official evidence)", "Last verified", "Open findings"]
        out = []
        for i in rows:
            open_f = db.execute(select(Finding.id).where(Finding.institution_id == i.id, Finding.status.in_(("open", "under_review")))).all()
            out.append({"Institution": i.name, "AICTE Permanent ID": i.aicte_permanent_id, "State": i.state, "District": i.district,
                        "Category": i.category, "Verification status": CASE_STATUSES.get(i.verification_status, i.verification_status),
                        "Approval status (official evidence)": i.approval_status or "Not recorded",
                        "Last verified": ser.iso(i.last_verified_at), "Open findings": len(open_f)})
        return cols, out
    if kind == "documents":
        stmt = _range(select(Document), Document.created_at, f, t)
        for col, key in ((Document.institution_id, "institution_id"), (Document.status, "status"), (Document.category_key, "category_key"),
                         (Document.case_id, "case_id")):
            if p.get(key):
                stmt = stmt.where(col == p[key])
        rows = db.execute(stmt.order_by(Document.created_at.desc()).limit(MAX_ROWS)).scalars().all()
        names = _names(db, {r.institution_id for r in rows})
        cats = {c.key: c.label for c in db.execute(select(DocumentCategory)).scalars()}
        cols = ["Uploaded", "Institution", "Document", "Category", "Academic year", "Status", "Official records", "Officer review", "Status reason"]
        return cols, [{"Uploaded": ser.iso(d.created_at), "Institution": names.get(d.institution_id), "Document": d.title,
                       "Category": cats.get(d.category_key, "Unclassified"), "Academic year": d.academic_year,
                       "Status": DOCUMENT_STATUSES.get(d.status, d.status),
                       "Official records": ((d.check_summary or {}).get("official") or {}).get("status", "not_attempted"),
                       "Officer review": d.review_status, "Status reason": d.status_reason} for d in rows]
    if kind == "pending_cases":
        stmt = _range(select(VerificationCase).where(VerificationCase.status != "decision_recorded"), VerificationCase.opened_at, f, t)
        if inst:
            stmt = stmt.where(VerificationCase.institution_id == inst)
        rows = db.execute(stmt.order_by(VerificationCase.opened_at).limit(MAX_ROWS)).scalars().all()
        names = _names(db, {r.institution_id for r in rows})
        now = utcnow()
        cols = ["Reference", "Institution", "Academic year", "Status", "Priority", "Opened", "Due", "Days open", "Assigned to"]
        return cols, [{"Reference": c.reference, "Institution": names.get(c.institution_id), "Academic year": c.academic_year,
                       "Status": CASE_STATUSES.get(c.status), "Priority": c.priority, "Opened": ser.iso(c.opened_at), "Due": ser.iso(c.due_date),
                       "Days open": (now - c.opened_at).days, "Assigned to": officers.get(c.assigned_to_id)} for c in rows]
    if kind in ("discrepancies", "inspections"):
        stmt = _range(select(Finding), Finding.created_at, f, t)
        stmt = stmt.where(Finding.inspection_id.is_not(None)) if kind == "inspections" else stmt.where(Finding.inspection_id.is_(None))
        for col, key in ((Finding.institution_id, "institution_id"), (Finding.status, "status"), (Finding.severity, "severity"),
                         (Finding.inspection_id, "inspection_id")):
            if p.get(key):
                stmt = stmt.where(col == p[key])
        rows = db.execute(stmt.order_by(Finding.created_at.desc()).limit(MAX_ROWS)).scalars().all()
        names = _names(db, {r.institution_id for r in rows})
        refs = dict(db.execute(select(InspectionCase.id, InspectionCase.reference)).all()) if kind == "inspections" else {}
        cols = ["Recorded", "Institution"] + (["Inspection"] if kind == "inspections" else []) + \
               ["Finding", "Source", "Category", "Severity", "Severity basis", "Status", "Physical verification needed", "Resolution"]
        return cols, [{"Recorded": ser.iso(x.created_at), "Institution": names.get(x.institution_id), "Inspection": refs.get(x.inspection_id),
                       "Finding": x.title, "Source": x.source.replace("_", " "), "Category": x.category, "Severity": x.severity,
                       "Severity basis": x.severity_basis, "Status": x.status,
                       "Physical verification needed": "Yes" if x.requires_physical_verification else "No",
                       "Resolution": x.resolution_note} for x in rows]
    if kind == "corrective_actions":
        stmt = _range(select(CorrectiveAction), CorrectiveAction.created_at, f, t)
        if inst:
            stmt = stmt.where(CorrectiveAction.institution_id == inst)
        if p.get("status"):
            stmt = stmt.where(CorrectiveAction.status == p["status"])
        rows = db.execute(stmt.order_by(CorrectiveAction.created_at.desc()).limit(MAX_ROWS)).scalars().all()
        names = _names(db, {r.institution_id for r in rows})
        cols = ["Created", "Institution", "Action", "Status", "Due", "Closed", "Resolution note"]
        return cols, [{"Created": ser.iso(c.created_at), "Institution": names.get(c.institution_id), "Action": c.title, "Status": c.status,
                       "Due": ser.iso(c.due_date), "Closed": ser.iso(c.closed_at), "Resolution note": c.resolution_note} for c in rows]
    if kind == "decisions":
        stmt = _range(select(Decision), Decision.decided_at, f, t)
        if inst:
            stmt = stmt.where(Decision.institution_id == inst)
        rows = db.execute(stmt.order_by(Decision.decided_at.desc()).limit(MAX_ROWS)).scalars().all()
        names = _names(db, {r.institution_id for r in rows})
        superseded = {d.supersedes_id for d in rows if d.supersedes_id}
        cols = ["Decided", "Institution", "Type", "Outcome", "Officer", "Reason", "Corrects earlier decision", "Superseded"]
        return cols, [{"Decided": ser.iso(d.decided_at), "Institution": names.get(d.institution_id), "Type": d.decision_type.replace("_", " "),
                       "Outcome": d.outcome.replace("_", " "), "Officer": officers.get(d.decided_by_id), "Reason": d.reason,
                       "Corrects earlier decision": "Yes" if d.supersedes_id else "No", "Superseded": "Yes" if d.id in superseded else "No"} for d in rows]
    if kind in ("officer_activity", "audit"):
        rows = db.execute(audit_query(p.get("officer_id"), p.get("action"), None, None, inst, None, f, t).limit(MAX_ROWS)).scalars().all()
        cols = ["When", "Officer", "Action", "Entity", "Summary", "Outcome", "External source", "Reason", "Hash"]
        return cols, [{"When": ser.iso(e.occurred_at), "Officer": e.officer_email or "system", "Action": e.action,
                       "Entity": f"{e.entity_type or ''} {e.entity_id or ''}".strip(), "Summary": e.summary, "Outcome": e.outcome,
                       "External source": e.external_source, "Reason": e.reason, "Hash": e.hash[:16]} for e in rows]
    if kind == "integrations":
        rows = db.execute(select(Integration).where(Integration.kind != "vision_model_provider").order_by(Integration.kind, Integration.name)).scalars().all()
        cols = ["Integration", "Purpose", "Provider", "Enabled", "Connection status", "Last success", "Last failure", "Failure reason", "Credential"]
        from ..integrations.secrets import secret_status

        out = []
        for i in rows:
            uses = db.execute(select(ExternalVerificationAttempt.id).where(ExternalVerificationAttempt.integration_id == i.id)).all()
            out.append({"Integration": i.name, "Purpose": i.purpose, "Provider": i.provider, "Enabled": "Yes" if i.enabled else "No",
                        "Connection status": i.connection_status, "Last success": ser.iso(i.last_success_at),
                        "Last failure": ser.iso(i.last_failure_at), "Failure reason": i.last_failure_reason,
                        "Credential": secret_status(i)["source"], "Uses": len(uses)})
        return cols + ["Uses"], out
    raise HTTPException(404, "Unknown report")


def _csv(cols: list[str], rows: list[dict]) -> bytes:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
    w.writeheader()
    for r in rows:
        # Neutralise spreadsheet formula injection from untrusted text.
        w.writerow({k: (f"'{v}" if isinstance(v, str) and v[:1] in ("=", "+", "-", "@") else v) for k, v in r.items()})
    return ("﻿" + buf.getvalue()).encode("utf-8")


def _pdf(title: str, subtitle: str, cols: list[str], rows: list[dict], sections: list[tuple[str, list]] | None = None) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(A4), leftMargin=12 * mm, rightMargin=12 * mm, topMargin=12 * mm, bottomMargin=12 * mm,
                            title=title)
    ss = getSampleStyleSheet()
    cell = ParagraphStyle("cell", parent=ss["BodyText"], fontSize=7, leading=8.5)
    head = ParagraphStyle("head", parent=cell, textColor=colors.white, fontName="Helvetica-Bold")

    def esc(v):
        return ("" if v is None else str(v)).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")[:1500]

    story = [Paragraph(esc(title), ss["Title"]), Paragraph(esc(subtitle), ss["Normal"]), Spacer(1, 6)]

    def table(c, r):
        data = [[Paragraph(esc(x), head) for x in c]] + [[Paragraph(esc(row.get(x)), cell) for x in c] for row in r]
        tbl = Table(data, repeatRows=1, colWidths=[(273 * mm) / max(1, len(c))] * len(c))
        tbl.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f3a5f")),
                                 ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f3f5f8")]),
                                 ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#c9d1dc")), ("VALIGN", (0, 0), (-1, -1), "TOP")]))
        return tbl

    if sections:
        for name, items in sections:
            story.append(Paragraph(esc(name), ss["Heading3"]))
            if items and isinstance(items[0], tuple):
                story.append(table(list(items[0][0]), items[0][1]) if items[0][1] else Paragraph("No records.", ss["Normal"]))
            else:
                for line in items or ["No records."]:
                    story.append(Paragraph(esc(line), ss["BodyText"]))
            story.append(Spacer(1, 6))
    else:
        story.append(table(cols, rows) if rows else Paragraph("No records match the selected filters.", ss["Normal"]))
    story.append(Spacer(1, 8))
    story.append(Paragraph("Generated from platform records. Automated checks assist officers and are not official approvals.", cell))
    doc.build(story)
    return buf.getvalue()


def _pdf_response(pdf: bytes, filename: str, download: bool) -> Response:
    # Generated by the platform (no scripts); may be embedded in the platform's own pages.
    return Response(content=pdf, media_type="application/pdf", headers={
        "Content-Disposition": f'{"attachment" if download else "inline"}; filename="{filename}"',
        "X-Frame-Options": "SAMEORIGIN", "Content-Security-Policy": "frame-ancestors 'self'", "Cache-Control": "private, no-store"})


@router.get("/document/{doc_id}/verification", response_model=None)
def document_verification_report(doc_id: str, download: bool = False, auth: Auth = Depends(require_officer), db: Session = Depends(get_db)) -> Response:
    from ..models import Document, DocumentVersion
    from ..reports.annotated import build_document_report

    doc = get_or_404(db, Document, doc_id, "Document")
    v = db.get(DocumentVersion, doc.current_version_id) if doc.current_version_id else None
    if v is None or v.processing_status != "completed":
        raise HTTPException(409, "The report is available once the document has been analysed.")
    pdf = build_document_report(db, doc, auth.officer.full_name)
    audit.record(db, "report.exported", officer=auth.officer, entity_type="document", entity_id=doc.id, institution_id=doc.institution_id,
                 case_id=doc.case_id, after={"report": "document_verification", "download": download}, ctx=auth.ctx)
    db.commit()
    safe = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in doc.title)[:80]
    return _pdf_response(pdf, f"verification-{safe}.pdf", download)


@router.get("/institution/{inst_id}/verification", response_model=None)
def institution_verification_report(inst_id: str, download: bool = False, auth: Auth = Depends(require_officer), db: Session = Depends(get_db)) -> Response:
    from ..reports.annotated import build_institution_report

    inst = get_or_404(db, Institution, inst_id, "Institution")
    pdf = build_institution_report(db, inst, auth.officer.full_name)
    audit.record(db, "report.exported", officer=auth.officer, entity_type="institution", entity_id=inst.id, institution_id=inst.id,
                 after={"report": "institution_verification", "download": download}, ctx=auth.ctx)
    db.commit()
    safe = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in inst.name)[:80]
    return _pdf_response(pdf, f"verification-{safe}.pdf", download)


@router.get("/types")
def report_types(auth: Auth = Depends(require_officer)) -> dict:
    return {"items": [{"key": k, "label": v} for k, v in REPORTS.items()]}


@router.get("/case/{case_id}", response_model=None)
def case_report(case_id: str, format: str = Query("pdf", pattern="^(pdf|json)$"), auth: Auth = Depends(require_officer),
                db: Session = Depends(get_db)) -> Response:
    """Institution verification report for one case (the nine-part explanation)."""
    c = get_or_404(db, VerificationCase, case_id, "Case")
    ex = build_explanation(db, c)
    s = ex["sections"]
    if format == "json":
        return Response(content=__import__("json").dumps(ex), media_type="application/json")
    secs = [
        ("1. Extracted details", [(["Document", "Field", "Value", "Page", "Method"],
                                  [{"Document": d["title"], "Field": f["label"], "Value": f["effective_value"], "Page": f["page_no"], "Method": f["method"]}
                                   for d in s["extracted_details"] for f in d["fields"]])]),
        ("2. Field validation", [(["Document", "Check", "Result", "Explanation"],
                                  [{"Document": d["title"], "Check": r["rule_label"], "Result": r["status"], "Explanation": r["message"]}
                                   for d in s["field_validation"] for r in d["results"]])]),
        ("3. Official source checks", [(["Document", "Source", "Method", "Outcome", "Reason", "When"],
                                        [{"Document": d["title"], "Source": a["source_name"] or "None configured", "Method": a["method"],
                                          "Outcome": a["outcome"], "Reason": a["reason"], "When": a["attempted_at"]} for d in s["official_source_checks"] for a in d["attempts"]]
                                        + [{"Document": d["title"], "Source": "—", "Method": "—", "Outcome": "not attempted",
                                            "Reason": "Unable to verify against official records (no check performed).", "When": ""}
                                           for d in s["official_source_checks"] if not d["attempts"]])]),
        ("4. Cross-document comparisons", [(["Field", "Result", "Explanation"],
                                            [{"Field": r["label"], "Result": r["status"], "Explanation": r["explanation"]} for r in s["cross_document_comparisons"]["results"]])]),
        ("5. Integrity and tampering analysis", [(["Document", "Dimension", "Check", "Result", "Summary"],
                                                  [{"Document": d["title"], "Dimension": x["dimension"], "Check": x["title"], "Result": x["status"], "Summary": x["summary"]}
                                                   for d in s["integrity_analysis"] for x in d["checks"]])]),
        ("6. Required documents", [(["Requirement", "Result", "Explanation", "Source"],
                                    [{"Requirement": r["rule_label"], "Result": r["status"], "Explanation": r["message"],
                                      "Source": (r["details"] or {}).get("source_reference")} for r in s["required_documents"]["results"]])]),
        ("7. Inspection findings", [(["Finding", "Severity", "Status"], [{"Finding": x["title"], "Severity": x["severity"], "Status": x["status"]} for x in s["inspection_findings"]])]),
        ("8. Outstanding tasks", [t["label"] for t in s["outstanding_tasks"]]),
        ("9. Officer decisions", [(["When", "Outcome", "Officer", "Justification"],
                                   [{"When": d["decided_at"], "Outcome": d["outcome"], "Officer": d["decided_by_name"], "Justification": d["reason"]}
                                    for d in s["officer_decisions"]])]),
    ]
    pdf = _pdf(f"Verification report — {c.reference}", f"{ex['case']['institution_name']} · {c.academic_year} · Status: {ex['status_label']} · "
               f"Generated {datetime.now().strftime('%Y-%m-%d %H:%M')} by {auth.officer.full_name}", [], [], secs)
    audit.record(db, "report.exported", officer=auth.officer, entity_type="case", entity_id=c.id, institution_id=c.institution_id,
                 case_id=c.id, after={"report": "case_verification", "format": "pdf"}, ctx=auth.ctx)
    db.commit()
    return Response(content=pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{c.reference}-verification-report.pdf"'})


@router.get("/{kind}", response_model=None)
def run_report(kind: str, format: str = Query("json", pattern="^(json|csv|pdf)$"), date_from: str | None = Query(None, alias="from"),
               date_to: str | None = Query(None, alias="to"), institution_id: str | None = None, state: str | None = None,
               status: str | None = None, severity: str | None = None, category_key: str | None = None, officer_id: str | None = None,
               action: str | None = None, inspection_id: str | None = None, case_id: str | None = None,
               auth: Auth = Depends(require_officer), db: Session = Depends(get_db)) -> Response | dict:
    if kind not in REPORTS:
        raise HTTPException(404, "Unknown report")
    params = {"from": date_from, "to": date_to, "institution_id": institution_id, "state": state, "status": status, "severity": severity,
              "category_key": category_key, "officer_id": officer_id, "action": action, "inspection_id": inspection_id, "case_id": case_id}
    cols, rows = build(db, kind, params)
    applied = {k: v for k, v in params.items() if v}
    if format == "json":
        return {"title": REPORTS[kind], "columns": cols, "rows": rows[:500], "total": len(rows), "truncated": len(rows) > 500,
                "filters": applied, "generated_at": ser.iso(utcnow())}
    audit.record(db, "report.exported", officer=auth.officer, entity_type="report", entity_id=kind,
                 after={"report": kind, "format": format, "filters": applied, "rows": len(rows)}, ctx=auth.ctx)
    db.commit()
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    if format == "csv":
        return Response(content=_csv(cols, rows), media_type="text/csv",
                        headers={"Content-Disposition": f'attachment; filename="{kind}-{stamp}.csv"'})
    subtitle = f"Filters: {applied or 'none'} · {len(rows)} record(s) · Generated {stamp} by {auth.officer.full_name}"
    return Response(content=_pdf(REPORTS[kind], subtitle, cols, rows), media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{kind}-{stamp}.pdf"'})


