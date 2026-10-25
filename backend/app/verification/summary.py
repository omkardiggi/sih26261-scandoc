"""Plain-language summaries: is each document correct, and what is the overall picture for an institution."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Document, DocumentCategory, Institution, VerificationCase
from .engine import applicable_requirements, latest_integrity, latest_validation, official_summary

SIMPLE = {
    "verified": ("Verified", "Confirmed against an official source"),
    "looks_correct": ("Checks passed", "All automatic checks passed; not yet confirmed with an official source"),
    "review": ("Needs review", "Some checks need an officer's judgement"),
    "problem": ("Problem found", "One or more checks failed"),
    "checking": ("Checking…", "Being read and checked"),
}


def simple_status(doc: Document) -> str:
    s = doc.status
    if s in ("uploaded", "processing"):
        return "checking"
    if s in ("information_mismatch", "possible_tampering", "rejected"):
        return "problem"
    if s == "requires_manual_review":
        return "review"
    if s == "verified_official":
        return "verified"
    return "looks_correct"


def _state(results, keys: set[str] | None = None, categories: set[str] | None = None) -> tuple[str, str | None]:
    rows = [r for r in results if (keys is None or r.rule_key in keys) and (categories is None or r.category in categories)]
    if not rows:
        return "unknown", None
    for want in ("fail", "warning"):
        hit = next((r for r in rows if r.status == want), None)
        if hit:
            return ("fail" if want == "fail" else "warn"), hit.message
    if any(r.status == "pass" for r in rows):
        return "pass", None
    return "unknown", rows[0].message


def document_checks(db: Session, doc: Document) -> dict:
    """The handful of checks an officer needs to judge whether a document is correct."""
    v = doc.current_version_id
    status = simple_status(doc)
    label, meaning = SIMPLE[status]
    if not v or status == "checking":
        return {"status": status, "label": label, "meaning": meaning, "checks": [], "reasons": []}
    val = latest_validation(db, v)
    integ = latest_integrity(db, v)
    off = official_summary(db, doc.id)
    checks = []

    def add(key, title, state, detail=None):
        checks.append({"key": key, "title": title, "state": state, "detail": detail})

    st, msg = _state(val, keys={"pages_readable"})
    add("readable", "Document is readable", st, msg)
    st, msg = _state(val, categories={"identity"})
    add("identity", "Matches the institution's record", st, msg)
    st, msg = _state(val, categories={"program"})
    if st != "unknown":
        add("programs", "Programs and intake match", st, msg)
    st, msg = _state(val, categories={"date", "expiry", "academic_year"})
    add("dates", "Dates, validity and academic year", st, msg)
    o = off.get("status")
    if o in ("verified", "mismatch", "partially_verified"):
        add("official", "Matches the issuer's record (QR)", {"verified": "pass", "mismatch": "fail"}.get(o, "warn"),
            next(iter((off.get("claims") or {}).values()), {}).get("reason"))
    ai = next((c for c in integ if c.check_type in ("ai_reading", "render_text_match") and c.status in ("pass", "warning")), None)
    if ai is not None:
        add("ai_reading", "AI reading confirms the text" if ai.check_type == "ai_reading" else "Second reading confirms the text",
            "pass" if ai.status == "pass" else "warn", ai.summary)
    elif any((c.details or {}).get("pending") for c in integ):
        add("ai_reading", "AI reading confirms the text", "unknown", "AI analysis of the pages is still running; this updates automatically.")
    tam = [c for c in integ if c.dimension in ("tampering", "file_integrity") and (c.status == "fail" or (c.details or {}).get("significance") in ("high", "medium"))]
    high = [c for c in tam if c.status == "fail" or (c.details or {}).get("significance") == "high"]
    add("tampering", "No signs of tampering", "fail" if high else ("warn" if tam else ("pass" if integ else "unknown")),
        (high or tam)[0].summary if tam else None)
    sig = next((c for c in integ if c.check_type == "digital_signature"), None)
    if sig is not None and sig.status in ("pass", "fail", "warning"):
        add("signature", "Digital signature", {"pass": "pass", "fail": "fail", "warning": "warn"}.get(sig.status, "unknown"), sig.summary)
    reasons = [c["detail"] for c in checks if c["state"] == "fail" and c["detail"]] + \
              [c["detail"] for c in checks if c["state"] == "warn" and c["detail"]]
    return {"status": status, "label": label, "meaning": meaning, "checks": checks, "reasons": reasons[:3]}


MATRIX_COLUMNS = [("name", "Name"), ("id", "Permanent ID"), ("address", "Address / PIN"), ("trust", "Trust"),
                  ("dates", "Dates & validity"), ("ai", "AI reading"), ("tampering", "Tampering"), ("qr", "Issuer QR")]


def _cell(results, keys) -> str:
    rows = [r for r in results if r.rule_key in keys]
    if any(r.status == "fail" for r in rows):
        return "fail"
    if any(r.status == "warning" for r in rows):
        return "warn"
    return "pass" if any(r.status == "pass" for r in rows) else "na"


def document_matrix_row(db: Session, doc: Document) -> dict:
    v = doc.current_version_id
    if not v or doc.status in ("uploaded", "processing"):
        return {k: "pending" for k, _ in MATRIX_COLUMNS}
    val = latest_validation(db, v)
    integ = latest_integrity(db, v)
    ai = next((c for c in integ if c.check_type in ("ai_reading", "render_text_match")), None)
    tam = [c for c in integ if c.dimension in ("tampering", "file_integrity") and c.check_type not in ("ai_reading", "render_text_match")]
    sev = [(c.details or {}).get("significance") for c in tam] + ["high" if c.status == "fail" else None for c in tam]
    off = official_summary(db, doc.id).get("status")
    dates_rows = [r for r in val if r.category in ("date", "expiry", "academic_year")]
    return {
        "name": _cell(val, {"institution_name_match", "institution_name_in_document"}),
        "id": _cell(val, {"permanent_id_match"}),
        "address": _cell(val, {"pincode_match", "state_match"}),
        "trust": _cell(val, {"management_body_match"}),
        "dates": _cell(dates_rows, {r.rule_key for r in dates_rows}),
        "ai": "na" if ai is None else ("pass" if ai.status == "pass" else "warn" if ai.status == "warning" else "na"),
        "tampering": "fail" if "high" in sev else ("warn" if "medium" in sev else "pass" if tam else "na"),
        "qr": {"verified": "pass", "mismatch": "fail", "partially_verified": "warn"}.get(off, "na"),
    }


def institution_summary(db: Session, inst: Institution) -> dict:
    case = db.execute(select(VerificationCase).where(VerificationCase.institution_id == inst.id)
                      .order_by(VerificationCase.opened_at.desc()).limit(1)).scalar_one_or_none()
    required: dict[str, str] = {}
    if case is not None:
        for r in applicable_requirements(db, case, inst):
            if r.mandatory:
                required.setdefault(r.category_key, r.title)
    docs = db.execute(select(Document).where(Document.institution_id == inst.id).order_by(Document.created_at.desc())).scalars().all()
    cats = db.execute(select(DocumentCategory).where(DocumentCategory.active.is_(True))
                      .order_by(DocumentCategory.sort_order)).scalars().all()
    by_cat: dict[str, list[Document]] = {}
    for d in docs:
        by_cat.setdefault(d.category_key or "other", []).append(d)
    counts = {k: 0 for k in SIMPLE} | {"missing_required": 0}
    groups: dict[str, dict] = {}
    for c in cats:
        g = groups.setdefault(c.group_name or "Disclosures & other", {"name": c.group_name or "Disclosures & other", "icon": c.icon, "slots": []})
        slot_docs = []
        for d in by_cat.get(c.key, []):
            chk = document_checks(db, d)
            counts[chk["status"]] += 1
            slot_docs.append({"id": d.id, "title": d.title, "academic_year": d.academic_year, "status": chk["status"], "label": chk["label"],
                              "reasons": chk["reasons"], "uploaded_at": d.created_at.isoformat() + "Z", "is_historical": d.is_historical})
        is_req = c.key in required
        if is_req and not slot_docs:
            counts["missing_required"] += 1
        g["slots"].append({"category_key": c.key, "label": c.label, "required": is_req, "requirement": required.get(c.key), "documents": slot_docs})
    if counts["problem"]:
        verdict = ("bad", f"Problems found in {counts['problem']} document(s)", "Open the documents marked in red to see what failed.")
    elif counts["review"]:
        verdict = ("warn", f"{counts['review']} document(s) need review", "Automatic checks could not decide; an officer should look at them.")
    elif counts["checking"]:
        verdict = ("info", "Documents are still being checked", "Results appear automatically when processing finishes.")
    elif not docs:
        verdict = ("neutral", "No documents uploaded yet", "Upload documents into the slots below to start verification.")
    else:
        verdict = ("ok", "Uploaded documents look correct",
                   f"{counts['verified']} confirmed with official sources, {counts['looks_correct']} passed all automatic checks.")
    if counts["missing_required"] and verdict[0] in ("ok", "neutral"):
        verdict = ("warn", verdict[1], verdict[2] + f" {counts['missing_required']} required document(s) are still missing.")
    cat_labels = {c.key: c.label for c in cats}
    matrix = []
    for d in docs:
        chk = document_checks(db, d)
        matrix.append({"id": d.id, "title": d.title, "category": cat_labels.get(d.category_key or "", "Unclassified"), "status": chk["status"],
                       "label": chk["label"], "reasons": chk["reasons"], "cells": document_matrix_row(db, d)})
    order = {"problem": 0, "review": 1, "checking": 2, "looks_correct": 3, "verified": 4}
    matrix.sort(key=lambda r: order.get(r["status"], 5))
    decision = None
    if case is not None:
        from .engine import effective_decision
        from ..models import Officer

        dec = effective_decision(db, case_id=case.id, decision_type="case_final")
        if dec is not None:
            by = db.get(Officer, dec.decided_by_id)
            decision = {"id": dec.id, "outcome": dec.outcome, "reason": dec.reason, "decided_at": dec.decided_at.isoformat() + "Z",
                        "decided_by": by.full_name if by else None}
    return {"verdict": {"tone": verdict[0], "title": verdict[1], "detail": verdict[2]}, "counts": counts,
            "matrix": matrix, "columns": [{"key": k, "label": l} for k, l in MATRIX_COLUMNS], "decision": decision,
            "ready_for_decision": bool(docs) and counts["checking"] == 0,
            "case": {"id": case.id, "reference": case.reference, "academic_year": case.academic_year,
                     "application_type": case.application_type} if case else None,
            "groups": list(groups.values()), "total_documents": len(docs)}
