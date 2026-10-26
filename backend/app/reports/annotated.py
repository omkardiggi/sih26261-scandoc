"""Verification report PDFs: summary + the document's own pages annotated with every finding + OCR/technical appendix.

Everything an officer needs is in one PDF: what was read, where it was found on the page,
whether it matches the institution's record, and why a document passed or failed.
"""

from __future__ import annotations

import html
import io
from datetime import datetime

import pymupdf
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import storage
from ..models import (
    ComparisonResult,
    Document,
    DocumentCategory,
    DocumentPage,
    DocumentVersion,
    ExternalVerificationAttempt,
    ExtractedField,
    FieldCorrection,
    Institution,
    TextBlock,
)
from ..verification.engine import latest_integrity, latest_run, latest_validation
from ..verification.summary import MATRIX_COLUMNS, document_checks, document_matrix_row, institution_summary

COLORS = {"valid": (0.02, 0.6, 0.25), "invalid": (0.86, 0.15, 0.15), "warning": (0.85, 0.47, 0.02),
          "neutral": (0.42, 0.47, 0.55), "qr": (0.45, 0.25, 0.85), "tamper": (0.86, 0.15, 0.15)}
HEX = {"pass": "#059669", "fail": "#dc2626", "warn": "#d97706", "unknown": "#64748b", "na": "#94a3b8", "pending": "#7c3aed"}
WORD = {"pass": "PASS", "fail": "FAIL", "warn": "CHECK", "unknown": "N/A", "na": "-", "pending": "..."}
STATUS_HEX = {"verified": "#059669", "looks_correct": "#0d9488", "review": "#d97706", "problem": "#dc2626", "checking": "#7c3aed"}

CSS = """
* { font-family: sans-serif; }
body { font-size: 9.5pt; color: #10201c; }
h1 { font-size: 17pt; color: #0f766e; margin: 0 0 2pt 0; }
h2 { font-size: 12pt; color: #0f766e; margin: 12pt 0 4pt 0; border-bottom: 1px solid #cbd9d4; }
h3 { font-size: 10.5pt; margin: 8pt 0 3pt 0; }
p { margin: 2pt 0; }
.muted { color: #6b7f7a; font-size: 8.5pt; }
table { border-collapse: collapse; width: 100%; margin: 3pt 0; }
th { background-color: #e6f6f3; color: #0f766e; font-size: 8.5pt; text-align: left; padding: 3pt; border: 1px solid #cbd9d4; }
td { font-size: 8.5pt; padding: 3pt; border: 1px solid #e2ebe8; vertical-align: top; }
.verdict { color: #ffffff; padding: 8pt; font-size: 12pt; font-weight: bold; }
.small { font-size: 8pt; }
"""


def _e(v) -> str:
    return html.escape("" if v is None else str(v)).replace("—", "-").replace("–", "-")


def _pill(state: str, text: str | None = None) -> str:
    return f'<b style="color:{HEX.get(state, "#64748b")}">{_e(text or WORD.get(state, state))}</b>'


def _html_pdf(body: str) -> pymupdf.Document:
    story = pymupdf.Story(html=f"<body>{body}</body>", user_css=CSS)
    buf = io.BytesIO()
    writer = pymupdf.DocumentWriter(buf)
    box = pymupdf.paper_rect("a4")
    where = box + (36, 36, -36, -40)
    more = True
    while more:
        dev = writer.begin_page(box)
        more, _ = story.place(where)
        story.draw(dev)
        writer.end_page()
    writer.close()
    return pymupdf.open("pdf", buf.getvalue())


def _fields(db: Session, version_id: str) -> list[dict]:
    rows = db.execute(select(ExtractedField).where(ExtractedField.version_id == version_id)).scalars().all()
    corr = {}
    if rows:
        for c in db.execute(select(FieldCorrection).where(FieldCorrection.field_id.in_([r.id for r in rows]))
                            .order_by(FieldCorrection.corrected_at)).scalars():
            corr[c.field_id] = c.corrected_value
    return [{"key": r.field_key, "label": r.label, "value": corr.get(r.id, r.value), "page": r.page_no, "bbox": r.bbox,
             "status": r.validation_status, "conf": r.confidence, "group": r.group_index, "method": r.method} for r in rows]


def build_marks(fields: list[dict], integrity, validation=()) -> list[dict]:
    state: dict[str, str] = {}
    rank = {"invalid": 3, "warning": 2, "valid": 1}
    for r in validation:
        t = {"fail": "invalid", "warning": "warning", "pass": "valid"}.get(r.status)
        for ev in r.evidence or []:
            k = ev.get("field_key")
            if t and k and ev.get("type") == "field" and ev.get("group_index") is None and rank[t] > rank.get(state.get(k, ""), 0):
                state[k] = t
    marks = []
    for f in fields:
        if f["group"] is None and f["bbox"] and f["page"] and f["key"] not in ("document_title", "pincode"):
            marks.append({"label": f["label"], "value": f["value"], "page": f["page"], "boxes": [f["bbox"]], "tone": state.get(f["key"], "neutral")})
    groups: dict[int, dict] = {}
    for f in fields:
        if f["group"] is not None:
            groups.setdefault(f["group"], {})[f["key"]] = f
    for g in groups.values():
        c = g.get("program_course")
        if c and c["page"]:
            boxes = [b for b in (c["bbox"], (g.get("program_intake") or {}).get("bbox")) if b]
            if boxes:
                lvl = (g.get("program_level") or {}).get("value")
                marks.append({"label": f"Course{f' ({lvl})' if lvl else ''}", "value": f"{c['value']} - intake {(g.get('program_intake') or {}).get('value', '?')}",
                              "page": c["page"], "boxes": boxes, "tone": "neutral"})
    for c in integrity:
        sig = (c.details or {}).get("significance")
        for ev in c.evidence or []:
            if not (ev.get("bbox") and ev.get("page_no")):
                continue
            if c.check_type == "qr_barcode":
                marks.append({"label": "QR code", "value": "Links to the issuer's record", "page": ev["page_no"], "boxes": [ev["bbox"]], "tone": "qr"})
            elif c.dimension == "tampering" and (c.status == "fail" or sig in ("high", "medium")):
                marks.append({"label": "Tampering indicator", "value": c.title, "page": ev["page_no"], "boxes": [ev["bbox"]], "tone": "tamper"})
    marks.sort(key=lambda m: (m["page"], m["boxes"][0][1], m["boxes"][0][0]))
    for i, m in enumerate(marks, 1):
        m["n"] = i
    return marks


def _annotated_pages(db: Session, doc: Document, v: DocumentVersion, marks: list[dict], out: pymupdf.Document) -> None:
    pages = db.execute(select(DocumentPage).where(DocumentPage.version_id == v.id).order_by(DocumentPage.page_no)).scalars().all()
    src = None
    if v.detected_type == "pdf":
        src = pymupdf.open(stream=storage.get(v.processing_key or v.storage_key), filetype="pdf")
    PANEL = 220
    try:
        for p in pages:
            if src is not None:
                sp = src.load_page(p.page_no - 1)
                W, H = sp.rect.width, sp.rect.height
            else:
                W = 595.0
                H = W * (p.render_height or 842) / max(1, p.render_width or 595)
            page = out.new_page(width=W + PANEL, height=max(H, 500))
            target = pymupdf.Rect(0, 0, W, H)
            if src is not None:
                page.show_pdf_page(target, src, p.page_no - 1)
            elif p.render_key:
                page.insert_image(target, stream=storage.get(p.render_key))
            page.draw_rect(target, color=(0.8, 0.85, 0.83), width=0.6)
            here = [m for m in marks if m["page"] == p.page_no]
            for m in here:
                col = COLORS.get(m["tone"], COLORS["neutral"])
                for j, b in enumerate(m["boxes"]):
                    r = pymupdf.Rect(b[0] * W - 2, b[1] * H - 1.5, b[2] * W + 2, b[3] * H + 1.5)
                    page.draw_rect(r, color=col, fill=col, fill_opacity=0.13, width=1.4)
                    if j == 0:
                        txt = str(m["n"])
                        w = 6 + 4.2 * len(txt)
                        x0 = max(0, r.x0 - w - 1)
                        lab = pymupdf.Rect(x0, r.y0, x0 + w, r.y0 + 11)
                        page.draw_rect(lab, color=col, fill=col, width=0)
                        page.insert_text((lab.x0 + 3, lab.y0 + 8.3), txt, fontsize=7.5, fontname="hebo", color=(1, 1, 1))
            # Side panel: what was found on this page.
            panel = pymupdf.Rect(W + 10, 14, W + PANEL - 10, max(H, 500) - 14)
            rows = "".join(
                f'<tr><td style="color:#ffffff;background-color:{_hexcol(m["tone"])};text-align:center"><b>{m["n"]}</b></td>'
                f'<td><b>{_e(m["label"])}</b><br/>{_e(str(m["value"])[:80])}</td></tr>' for m in here)
            src_txt = {"embedded": "digital text", "ocr": f"OCR (read quality {round((p.ocr_confidence or 0) * 100)}%)", "none": "no text"}.get(p.text_source, p.text_source)
            panel_html = (f"<h3>Page {p.page_no} of {len(pages)}</h3><p class='muted'>Read from: {_e(src_txt)}"
                          f"{' - UNREADABLE' if p.unreadable else ''}</p>"
                          + (f"<table>{rows}</table>" if here else "<p class='muted'>No details located on this page.</p>")
                          + "<p class='muted'>Green = matches the institution record, red = does not match, amber = check, grey = read, purple = QR code.</p>")
            page.insert_htmlbox(panel, panel_html, css=CSS)
    finally:
        if src is not None:
            src.close()


def _hexcol(tone: str) -> str:
    r, g, b = COLORS.get(tone, COLORS["neutral"])
    return "#%02x%02x%02x" % (int(r * 255), int(g * 255), int(b * 255))


def _doc_summary_html(db: Session, doc: Document, v: DocumentVersion, inst: Institution, marks: list[dict], heading_level: str = "h1") -> str:
    chk = document_checks(db, doc)
    val = latest_validation(db, v.id)
    integ = latest_integrity(db, v.id)
    cat = db.get(DocumentCategory, doc.category_key) if doc.category_key else None
    verdict_text = {"verified": "CORRECT - matches the issuer's record", "looks_correct": "CORRECT - all checks passed",
                    "review": "NEEDS REVIEW", "problem": "PROBLEMS FOUND", "checking": "STILL BEING CHECKED"}.get(chk["status"], chk["label"])
    parts = [f"<{heading_level}>{_e(doc.title)}</{heading_level}>",
             f"<p class='muted'>{_e(inst.name)} | {_e(cat.label if cat else 'Unclassified')} | {_e(doc.academic_year or 'year not stated')} | "
             f"file {_e(v.original_filename)} | {v.page_count or '?'} page(s) | uploaded {v.uploaded_at:%d %b %Y}</p>",
             f"<p class='verdict' style='background-color:{STATUS_HEX.get(chk['status'], '#64748b')}'>{_e(verdict_text)}</p>"]
    if chk["reasons"]:
        parts.append("<p><b>Why:</b></p><ul>" + "".join(f"<li>{_e(r)}</li>" for r in chk["reasons"]) + "</ul>")
    if chk["checks"]:
        parts.append("<h2>Is this document correct?</h2><table><tr><th>Check</th><th>Result</th><th>Detail</th></tr>"
                     + "".join(f"<tr><td>{_e(c['title'])}</td><td>{_pill(c['state'])}</td><td>{_e(c['detail'] or '')}</td></tr>" for c in chk["checks"])
                     + "</table>")
    # Detail-by-detail comparison with the institution record.
    cmp_rows = []
    for r in val:
        d = r.details or {}
        if r.category == "identity" and r.status in ("pass", "fail", "warning"):
            doc_v = d.get("document_original") or d.get("document")
            reg_v = d.get("registry_original") or d.get("registry")
            cmp_rows.append(f"<tr><td>{_e(r.rule_label)}</td><td>{_e(doc_v)}</td><td>{_e(reg_v)}</td><td>{_pill({'pass': 'pass', 'fail': 'fail'}.get(r.status, 'warn'))}</td></tr>")
    if cmp_rows:
        parts.append("<h2>Matched against the institution record</h2><table><tr><th>Detail</th><th>In this document</th><th>Institution record</th><th>Result</th></tr>"
                     + "".join(cmp_rows) + "</table>")
    attempt = db.execute(select(ExternalVerificationAttempt).where(ExternalVerificationAttempt.document_id == doc.id,
                                                                   ExternalVerificationAttempt.method == "automated")
                         .order_by(ExternalVerificationAttempt.attempted_at.desc())).scalars().first()
    comps = ((attempt.matched_values or {}).get("comparisons") if attempt else None) or {}
    if comps:
        parts.append(f"<h2>Issuer record (from the QR code)</h2><p class='muted'>{_e(attempt.source_name)} - checked {attempt.attempted_at:%d %b %Y %H:%M}</p>"
                     "<table><tr><th>Detail</th><th>Document</th><th>Issuer record</th><th>Result</th></tr>"
                     + "".join(f"<tr><td>{_e(k.replace('_', ' '))}</td><td>{_e(x.get('document'))}</td><td>{_e(x.get('official'))}</td>"
                               f"<td>{_pill('pass' if x.get('match') else 'fail')}</td></tr>" for k, x in comps.items())
                     + "</table>")
    if marks:
        parts.append("<h2>Details read from the document</h2><p class='muted'>The numbers match the boxes drawn on the annotated pages that follow.</p>"
                     "<table><tr><th>#</th><th>Detail</th><th>Value</th><th>Page</th></tr>"
                     + "".join(f"<tr><td style='color:#ffffff;background-color:{_hexcol(m['tone'])};text-align:center'><b>{m['n']}</b></td>"
                               f"<td>{_e(m['label'])}</td><td>{_e(m['value'])}</td><td>{m['page']}</td></tr>" for m in marks)
                     + "</table>")
    flags = [c for c in integ if c.status in ("fail", "warning") and c.dimension in ("tampering", "file_integrity", "signature", "content_security")]
    if flags:
        parts.append("<h2>Points to look at</h2><ul>" + "".join(f"<li><b>{_e(c.title)}:</b> {_e(c.summary)}</li>" for c in flags) + "</ul>")
    return "".join(parts)


def _appendix_html(db: Session, v: DocumentVersion) -> str:
    pages = db.execute(select(DocumentPage).where(DocumentPage.version_id == v.id).order_by(DocumentPage.page_no)).scalars().all()
    blocks = db.execute(select(TextBlock).where(TextBlock.version_id == v.id).order_by(TextBlock.page_no, TextBlock.seq)).scalars().all()
    out = ["<h1>Appendix - text read from the document</h1><p class='muted'>Exactly what was read from each page. OCR read quality shows how clearly "
           "text could be read; it is not a measure of authenticity.</p>"]
    for p in pages:
        lines = [b for b in blocks if b.page_no == p.page_no]
        how = "digital text layer" if p.text_source == "embedded" else f"OCR, read quality {round((p.ocr_confidence or 0) * 100)}%" if p.text_source == "ocr" else "no text"
        text = "<br/>".join(_e(b.text) for b in lines)[:12000]
        out.append(f"<h3>Page {p.page_no} ({_e(how)})</h3><p class='small'>{text or '<i>No text found.</i>'}</p>")
    out.append("<h1>Appendix - all technical checks</h1><table><tr><th>Check</th><th>Result</th><th>Explanation</th></tr>")
    for r in latest_validation(db, v.id):
        out.append(f"<tr><td>{_e(r.rule_label)}</td><td>{_pill({'pass': 'pass', 'fail': 'fail', 'warning': 'warn'}.get(r.status, 'na'))}</td><td>{_e(r.message)}</td></tr>")
    for c in latest_integrity(db, v.id):
        out.append(f"<tr><td>{_e(c.title)}</td><td>{_pill({'pass': 'pass', 'fail': 'fail', 'warning': 'warn'}.get(c.status, 'na'))}</td><td>{_e(c.summary)}</td></tr>")
    out.append("</table>")
    out.append(f"<p class='muted'>File SHA-256: {_e(v.sha256)}</p>")
    return "".join(out)


def build_document_report(db: Session, doc: Document, generated_by: str | None = None) -> bytes:
    v = db.get(DocumentVersion, doc.current_version_id)
    inst = db.get(Institution, doc.institution_id)
    marks = build_marks(_fields(db, v.id), latest_integrity(db, v.id), latest_validation(db, v.id))
    out = pymupdf.open()
    head = _doc_summary_html(db, doc, v, inst, marks)
    head += f"<p class='muted'>Report generated {datetime.now():%d %b %Y %H:%M}{' by ' + _e(generated_by) if generated_by else ''}.</p>"
    out.insert_pdf(_html_pdf(head))
    _annotated_pages(db, doc, v, marks, out)
    out.insert_pdf(_html_pdf(_appendix_html(db, v)))
    _footer(out, f"{inst.name} - {doc.title}")
    out.set_metadata({"title": f"Verification report - {doc.title}", "creator": "ScanDoc"})
    return out.tobytes(garbage=3, deflate=True)


def build_institution_report(db: Session, inst: Institution, generated_by: str | None = None) -> bytes:
    s = institution_summary(db, inst)
    c = s["counts"]
    out = pymupdf.open()
    rows = "".join(
        f"<tr><td><b>{_e(r['title'])}</b><br/><span class='muted'>{_e(r['category'])}</span></td>"
        f"<td style='color:{STATUS_HEX.get(r['status'], '#64748b')}'><b>{_e(r['label'])}</b></td>"
        + "".join(f"<td style='text-align:center'>{_pill(r['cells'][k])}</td>" for k, _ in MATRIX_COLUMNS) + "</tr>"
        for r in s["matrix"])
    missing = [sl["label"] for g in s["groups"] for sl in g["slots"] if sl["required"] and not sl["documents"]]
    tone_hex = {"ok": "#059669", "bad": "#dc2626", "warn": "#d97706", "info": "#2563eb", "neutral": "#64748b"}[s["verdict"]["tone"]]
    dec = s["decision"]
    cover = (f"<h1>Institution verification report</h1><p><b>{_e(inst.name)}</b></p>"
             f"<p class='muted'>Permanent ID {_e(inst.aicte_permanent_id or '-')} | {_e(', '.join(x for x in (inst.district, inst.state) if x))} | "
             f"{_e(inst.institution_type or '')} | AICTE status: {_e(inst.approval_status or '-')}</p>"
             f"<p class='verdict' style='background-color:{tone_hex}'>{_e(s['verdict']['title'])}</p><p>{_e(s['verdict']['detail'])}</p>"
             f"<p><b>Documents:</b> {s['total_documents']} | verified {c['verified']} | checks passed {c['looks_correct']} | needs review {c['review']} | "
             f"problems {c['problem']} | required missing {c['missing_required']}</p>"
             "<h2>Document check summary</h2><table><tr><th>Document</th><th>Result</th>"
             + "".join(f"<th>{_e(lbl)}</th>" for _, lbl in MATRIX_COLUMNS) + f"</tr>{rows}</table>"
             + ("<h2>Required documents not uploaded</h2><ul>" + "".join(f"<li>{_e(m)}</li>" for m in missing) + "</ul>" if missing else "")
             + _cross_document_html(db, inst)
             + ("<h2>Final decision</h2>" + (f"<p><b>{_e(dec['outcome'].replace('_', ' '))}</b> by {_e(dec['decided_by'])} on {_e(dec['decided_at'][:10])}</p><p>{_e(dec['reason'])}</p>"
                                              if dec else "<p class='muted'>No decision recorded yet.</p>"))
             + f"<p class='muted'>Generated {datetime.now():%d %b %Y %H:%M}{' by ' + _e(generated_by) if generated_by else ''}. "
               "Each document follows: its summary, then its pages annotated with every detail found.</p>")
    out.insert_pdf(_html_pdf(cover))
    docs = db.execute(select(Document).where(Document.institution_id == inst.id).order_by(Document.created_at)).scalars().all()
    for doc in docs:
        v = db.get(DocumentVersion, doc.current_version_id) if doc.current_version_id else None
        if v is None or v.processing_status != "completed":
            continue
        marks = build_marks(_fields(db, v.id), latest_integrity(db, v.id), latest_validation(db, v.id))
        out.insert_pdf(_html_pdf(_doc_summary_html(db, doc, v, inst, marks)))
        _annotated_pages(db, doc, v, marks, out)
    _footer(out, inst.name)
    out.set_metadata({"title": f"Verification report - {inst.name}", "creator": "ScanDoc"})
    return out.tobytes(garbage=3, deflate=True)


def _cross_document_html(db: Session, inst: Institution) -> str:
    from ..models import VerificationCase

    case = db.execute(select(VerificationCase).where(VerificationCase.institution_id == inst.id).order_by(VerificationCase.opened_at.desc())).scalars().first()
    run = latest_run(db, "cross_document", case_id=case.id) if case else None
    if run is None:
        return ""
    rows = db.execute(select(ComparisonResult).where(ComparisonResult.run_id == run.id)).scalars().all()
    rows = [r for r in rows if r.status in ("conflict", "close_match", "consistent")]
    if not rows:
        return ""
    state = {"consistent": "pass", "conflict": "fail", "close_match": "warn"}
    return ("<h2>Consistency across documents</h2><table><tr><th>Detail</th><th>Result</th><th>Explanation</th></tr>"
            + "".join(f"<tr><td>{_e(r.label)}</td><td>{_pill(state[r.status])}</td><td>{_e(r.explanation)}</td></tr>" for r in rows) + "</table>")


def _footer(doc: pymupdf.Document, label: str) -> None:
    n = doc.page_count
    for i, page in enumerate(doc):
        r = page.rect
        x0 = r.width - 210 if r.width > 700 else 30  # annotated pages: footer sits under the side panel
        page.insert_textbox(pymupdf.Rect(x0, r.height - 22, r.width - 10, r.height - 8),
                            (f"Verification report - page {i + 1} of {n}" if r.width > 700 else f"{label[:90]}  |  Verification report  |  page {i + 1} of {n}"),
                            fontsize=7, color=(0.45, 0.52, 0.5), align=1)
