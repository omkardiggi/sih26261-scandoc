"""Structured field extraction with provenance (page, bounding box, source text, method)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .extract import ExtractionResult, Line, TableResult
from .normalize import (
    AICTE_ID_RE,
    AY_RE,
    clean_text,
    normalize_academic_year,
    normalize_aicte_id,
    normalize_name,
    parse_date,
    parse_int,
)

FIELD_LABELS: dict[str, str] = {
    "aicte_permanent_id": "AICTE Permanent ID",
    "application_id": "Application ID",
    "institution_name": "Institution name",
    "management_body": "Trust / Society / Company",
    "institution_address": "Institution address",
    "institution_type": "Institution type",
    "region": "Region",
    "establishment_year": "Year of establishment",
    "reference_number": "Reference / letter number",
    "issue_date": "Issue / approval date",
    "valid_from": "Valid from",
    "valid_until": "Valid until",
    "academic_year": "Academic year",
    "issuing_authority": "Issuing authority",
    "certificate_number": "Certificate / licence number",
    "document_title": "Document title",
    "faculty_count": "Total faculty",
    "financial_year": "Financial year",
    "coordinates": "Latitude / longitude",
    "pincode": "PIN code",
    "program_level": "Program level",
    "program_name": "Program",
    "program_course": "Course",
    "program_affiliating_body": "Affiliating body",
    "program_intake": "Approved intake",
    "program_intake_previous": "Intake (previous year)",
    "qr_content": "QR / barcode content",
}

LABEL_SYNONYMS: dict[str, list[str]] = {
    "aicte_permanent_id": ["aicte permanent id", "permanent id", "permanent institute id"],
    "application_id": ["application id", "application no", "application number"],
    "institution_name": ["name of the institution", "name of institution", "institution name", "name of the institute",
                         "institute name", "name of the college", "name of college"],
    "management_body": ["name of the society/trust", "name of the trust/society", "name of the society", "name of the trust",
                        "name of the company", "society/trust name"],
    "institution_address": ["institution address", "address of the institution", "address of institution"],
    "institution_type": ["institution type", "type of institution"],
    "region": ["region"],
    "establishment_year": ["year of establishment", "establishment year", "established in"],
    "issue_date": ["date of approval", "date of issue", "issue date", "issued on", "date of issuance", "dated"],
    "valid_until": ["valid up to", "valid upto", "valid till", "valid until", "validity up to", "validity upto",
                    "date of expiry", "expiry date", "valid through", "valid to"],
    "valid_from": ["valid from", "validity from", "effective from"],
    "certificate_number": ["certificate no", "certificate number", "noc no", "license no", "licence no", "certificate id"],
    "faculty_count": ["total faculty", "total number of faculty", "number of faculty", "no. of faculty", "total teaching staff"],
    "financial_year": ["financial year", "for the year ended", "year ended"],
}

ID_FIELDS = {"aicte_permanent_id", "application_id"}
DATE_FIELDS = {"issue_date", "valid_until", "valid_from"}
SEPARATORS = " :-–—.\t"


@dataclass
class Candidate:
    field_key: str
    value: str
    normalized: str | None
    original_text: str
    page_no: int | None
    bbox: list[float] | None
    confidence: float | None
    basis: str
    method: str
    group_index: int | None = None
    priority: int = 0

    @property
    def label(self) -> str:
        return FIELD_LABELS.get(self.field_key, self.field_key)


@dataclass
class FieldExtraction:
    fields: list[Candidate]
    alternates: dict[str, list[Candidate]] = field(default_factory=dict)
    programs: list[dict] = field(default_factory=list)


def _basis(source: str) -> str:
    return "Embedded text layer (no OCR)" if source == "embedded" else "OCR recognition confidence"


def normalize_value(key: str, value: str) -> str | None:
    if key in ID_FIELDS:
        return normalize_aicte_id(value)
    if key in DATE_FIELDS:
        d, _ = parse_date(value)
        return d.isoformat() if d else None
    if key == "academic_year":
        ay, _, _ = normalize_academic_year(value)
        return ay
    if key in {"establishment_year"}:
        m = re.search(r"\b(1[89]\d{2}|20\d{2})\b", value)
        return m.group(1) if m else None
    if key in {"faculty_count", "program_intake", "program_intake_previous"}:
        n = parse_int(value)
        return str(n) if n is not None else None
    if key in {"institution_name", "management_body", "program_course", "program_affiliating_body", "issuing_authority"}:
        return normalize_name(value) or None
    if key == "pincode":
        m = re.search(r"\b[1-9]\d{5}\b", value)
        return m.group(0) if m else None
    return clean_text(value) or None


def _label_match(text: str, synonyms: list[str]) -> tuple[str, str] | None:
    """If text starts with a label, return (label, remainder)."""
    low = text.lower()
    for syn in sorted(synonyms, key=len, reverse=True):
        if low.startswith(syn):
            rest = text[len(syn):]
            if rest and rest[0].isalnum() and not rest[0].isdigit():
                continue  # label is a prefix of a longer word
            return syn, rest.lstrip(SEPARATORS).strip()
    return None


def _looks_like_label(text: str) -> bool:
    low = text.lower()
    return any(low.startswith(s) for syns in LABEL_SYNONYMS.values() for s in syns)


def _union(boxes: list[list[float]]) -> list[float]:
    return [min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes)]


def _from_kv_tables(tables: list[TableResult]) -> list[Candidate]:
    out: list[Candidate] = []
    for t in tables:
        for r_idx, row in enumerate(t.rows):
            for c_idx in range(len(row) - 1):
                label_cell = row[c_idx]
                value = row[c_idx + 1]
                if not label_cell or not value:
                    continue
                for key, syns in LABEL_SYNONYMS.items():
                    if label_cell.lower().strip(SEPARATORS) in syns:
                        box = t.cell_boxes[r_idx][c_idx + 1] if r_idx < len(t.cell_boxes) and c_idx + 1 < len(t.cell_boxes[r_idx]) else None
                        out.append(Candidate(key, value, normalize_value(key, value), f"{label_cell}: {value}",
                                             t.page_no, box, 1.0, _basis(t.source), "table_key_value", priority=3))
                        break
    return out


def _from_labelled_lines(lines: list[Line]) -> list[Candidate]:
    out: list[Candidate] = []
    by_page: dict[int, list[Line]] = {}
    for ln in lines:
        by_page.setdefault(ln.page_no, []).append(ln)
    for page_lines in by_page.values():
        for i, ln in enumerate(page_lines):
            for key, syns in LABEL_SYNONYMS.items():
                m = _label_match(ln.text, syns)
                if not m:
                    continue
                label, rest = m
                value_lines = [ln] if rest else []
                if not rest:
                    # value on the following line(s) of the same column, vertically adjacent
                    line_h = max(ln.bbox[3] - ln.bbox[1], 0.005)
                    prev = ln
                    for nxt in page_lines[i + 1 : i + 3]:
                        if (_looks_like_label(nxt.text) or abs(nxt.bbox[0] - ln.bbox[0]) > 0.35
                                or nxt.bbox[1] - prev.bbox[3] > 2.5 * line_h):
                            break
                        value_lines.append(nxt)
                        prev = nxt
                        if key not in {"institution_name", "institution_address", "management_body"}:
                            break
                    rest = " ".join(v.text for v in value_lines)
                if not rest:
                    continue
                if key in DATE_FIELDS and parse_date(rest)[0] is None:
                    continue
                confs = [v.confidence for v in value_lines if v.confidence is not None]
                out.append(Candidate(key, rest, normalize_value(key, rest), " / ".join([ln.text] + [v.text for v in value_lines if v is not ln]),
                                     ln.page_no, _union([v.bbox for v in value_lines]),
                                     min(confs) if confs else None, _basis(ln.source), "labelled_line", priority=2))
                break
    return out


_FNO_RE = re.compile(r"\bF\.?\s?No\.?\s*[:\-]?\s*([A-Za-z\-]+/\s?1-\d{6,12}/\d{4}/[A-Za-z]+)", re.I)
_REF_RE = re.compile(r"\b(?:Ref(?:erence)?|Letter|File|Memo)\.?\s*No\.?\s*[:\-]?\s*([A-Za-z0-9][A-Za-z0-9/\-\.\(\)]{3,60})", re.I)
_VALID_RE = re.compile(r"valid(?:ity)?\s+(?:up\s?to|till|until|through|to)\s*[:\-]?\s*(.{6,30})", re.I)
_AY_CTX_RE = re.compile(r"(?:academic\s+year|approval\s+process|a\.\s?y\.?)\s*[:\-]?\s*((?:19|20)\d{2}\s*[\-–/]\s*(?:19|20)?\d{2})", re.I)
_DATE_LABEL_RE = re.compile(r"^\s*(?:date|dated)\s*[:\-]\s*(.+)$", re.I)
_COORD_RE = re.compile(r"(?:lat(?:itude)?)[^\d\-]{0,12}(-?\d{1,2}\.\d{3,})[^\d\-]{1,30}(?:lon(?:g(?:itude)?)?)[^\d\-]{0,12}(-?\d{1,3}\.\d{3,})", re.I)
_AUTHORITY_PATTERNS = [
    re.compile(r"all india council for technical education", re.I),
    re.compile(r"\b[A-Z][A-Za-z\.\s&]{2,80}\buniversity\b", re.I),
    re.compile(r"\b(?:directorate|department|office|ministry|commissionerate)\s+of\s+[A-Za-z&\s]{3,80}", re.I),
    re.compile(r"[A-Za-z\s]{0,40}fire\s+(?:and|&)\s+(?:emergency|rescue)\s+services?", re.I),
    re.compile(r"[A-Za-z\s]{0,40}\bfire\s+services?\b", re.I),
    re.compile(r"[A-Za-z\s]{2,40}\bmunicipal\s+(?:corporation|council)\b", re.I),
    re.compile(r"[A-Za-z\s]{2,40}\bdevelopment\s+authority\b", re.I),
    re.compile(r"[A-Za-z\s]{2,40}\bgram\s+panchayat\b", re.I),
]


def _from_patterns(lines: list[Line]) -> list[Candidate]:
    out: list[Candidate] = []

    def add(key, value, ln: Line, original, method, priority=1, conf=None):
        out.append(Candidate(key, value, normalize_value(key, value), original, ln.page_no, ln.bbox,
                             conf if conf is not None else ln.confidence, _basis(ln.source), method, priority=priority))

    for idx, ln in enumerate(lines):
        t = ln.text
        low = t.lower()
        if m := _FNO_RE.search(t):
            add("reference_number", re.sub(r"\s+", "", m.group(1)), ln, t, "pattern:file_number", 2)
        elif m := _REF_RE.search(t):
            add("reference_number", m.group(1).rstrip(".,"), ln, t, "pattern:reference_number")
        for m in AICTE_ID_RE.finditer(t.replace(" ", "")):
            if "permanent" in low and "application" not in low:
                add("aicte_permanent_id", m.group(0), ln, t, "pattern:aicte_id_with_context")
            elif "application" in low:
                add("application_id", m.group(0), ln, t, "pattern:aicte_id_with_context")
        if m := _AY_CTX_RE.search(t):
            add("academic_year", m.group(1), ln, t, "pattern:academic_year_context", 2)
        if m := _VALID_RE.search(t):
            if parse_date(m.group(1))[0]:
                add("valid_until", m.group(1), ln, t, "pattern:validity_phrase", 2)
        if m := _DATE_LABEL_RE.match(t):
            if parse_date(m.group(1))[0]:
                add("issue_date", m.group(1), ln, t, "pattern:date_label")
        if m := _COORD_RE.search(t):
            add("coordinates", f"{m.group(1)}, {m.group(2)}", ln, t, "pattern:coordinates")
        # Issuing authority: header region of the first page only.
        if ln.page_no == 1 and ln.bbox[1] < 0.3:
            for rx in _AUTHORITY_PATTERNS:
                if am := rx.search(t):
                    value = clean_text(am.group(0))
                    if len(value) >= 6:
                        add("issuing_authority", value, ln, t, "pattern:header_authority", 1)
                        break
    # Generic academic-year fallback (lowest priority).
    for ln in lines:
        if m := AY_RE.search(ln.text):
            add("academic_year", m.group(0), ln, ln.text, "pattern:academic_year_any", 0)
            break
    # Document title: the largest early line is hard to know without font sizes; use first substantial header line.
    for ln in lines[:12]:
        if ln.page_no == 1 and len(ln.text) >= 8 and re.search(r"(certificate|approval|letter|affidavit|resolution|report|statement|deed|licen[cs]e|plan|undertaking|declaration)", ln.text, re.I):
            add("document_title", ln.text, ln, ln.text, "pattern:title_keyword", 0)
            break
    return out


_YEAR_COL_RE = re.compile(r"((?:19|20)\d{2})\s*[\-–]\s*(\d{2,4})")


def _program_tables(tables: list[TableResult], doc_academic_year: str | None) -> tuple[list[Candidate], list[dict]]:
    """Detect program/intake tables and extract one row group per course."""
    cands: list[Candidate] = []
    programs: list[dict] = []
    group = 0
    last: dict | None = None
    for t in tables:
        header = [h.lower() for h in t.header]
        is_prog = any("course" in h or "program" in h for h in header) and any("intake" in h for h in header)
        if not is_prog:
            # A table continuing on the next page without repeating its header: same column count as the previous program table.
            if last and t.page_no == last["page_no"] + 1 and len(t.header) == last["ncols"] and t.rows:
                t = TableResult(t.page_no, t.bbox, last["header"], [last["header"]] + t.rows, [[None] * len(t.header)] + t.cell_boxes, t.source)
                header = [h.lower() for h in t.header]
            else:
                continue
        col = {}
        intake_cols: list[tuple[int, str | None]] = []
        for i, h in enumerate(header):
            if "level" in h and "level" not in col:
                col["level"] = i
            elif "course" in h and "course" not in col:
                col["course"] = i
            elif "program" in h and "program" not in col:
                col["program"] = i
            elif "affiliat" in h and "affiliating" not in col:
                col["affiliating"] = i
            elif "intake" in h:
                m = _YEAR_COL_RE.search(h)
                ay = normalize_academic_year(m.group(0))[0] if m else None
                intake_cols.append((i, ay))
        if "course" not in col and "program" in col:
            col["course"] = col.pop("program")
        if "course" not in col or not intake_cols:
            continue
        last = {"page_no": t.page_no, "ncols": len(t.header), "header": t.header}
        current_idx = intake_cols[-1][0]
        previous_idx = None
        if doc_academic_year:
            for i, ay in intake_cols:
                if ay == doc_academic_year:
                    current_idx = i
            others = [i for i, ay in intake_cols if i != current_idx]
            previous_idx = others[-1] if others else None
        elif len(intake_cols) > 1:
            previous_idx = intake_cols[-2][0]
        for r_idx, row in enumerate(t.rows[1:], start=1):
            if len(row) <= max(current_idx, col["course"]):
                continue
            course = row[col["course"]]
            if not course or course.lower() in header or course.lower().startswith("total"):
                continue
            intake_raw = row[current_idx]
            other = [c for i, c in enumerate(row) if i != col["course"] and c]
            if not intake_raw and not other and programs:
                # A course name wrapped onto the next page: append it to the previous row.
                prev = programs[-1]
                prev["course"] = f"{prev['course']} {course}".strip()
                for cand in cands:
                    if cand.group_index == prev["group_index"] and cand.field_key == "program_course":
                        cand.value = prev["course"]
                        cand.normalized = normalize_value("program_course", prev["course"])
                continue
            boxes = t.cell_boxes[r_idx] if r_idx < len(t.cell_boxes) else []

            def box(i):
                return boxes[i] if i is not None and i < len(boxes) else None

            entry = {"group_index": group, "page_no": t.page_no, "course": course,
                     "level": row[col["level"]] if "level" in col and col["level"] < len(row) else None,
                     "program": row[col["program"]] if "program" in col and col["program"] < len(row) else None,
                     "affiliating_body": row[col["affiliating"]] if "affiliating" in col and col["affiliating"] < len(row) else None,
                     "intake": parse_int(intake_raw), "intake_raw": intake_raw,
                     "intake_previous": parse_int(row[previous_idx]) if previous_idx is not None and previous_idx < len(row) else None}
            programs.append(entry)
            orig = " | ".join(c for c in row if c)
            cands.append(Candidate("program_course", course, normalize_value("program_course", course), orig, t.page_no, box(col["course"]), 1.0, _basis(t.source), "table_program_row", group, 3))
            if intake_raw:
                cands.append(Candidate("program_intake", intake_raw, normalize_value("program_intake", intake_raw), orig, t.page_no, box(current_idx), 1.0, _basis(t.source), "table_program_row", group, 3))
            if entry["intake_previous"] is not None:
                cands.append(Candidate("program_intake_previous", str(entry["intake_previous"]), str(entry["intake_previous"]), orig, t.page_no, box(previous_idx), 1.0, _basis(t.source), "table_program_row", group, 3))
            for key, ck in (("program_level", "level"), ("program_name", "program"), ("program_affiliating_body", "affiliating")):
                if ck in col and col[ck] < len(row) and row[col[ck]]:
                    v = row[col[ck]]
                    cands.append(Candidate(key, v, normalize_value(key, v), orig, t.page_no, box(col[ck]), 1.0, _basis(t.source), "table_program_row", group, 3))
            group += 1
    return cands, programs


def _inside_table(c: Candidate, tables: list[TableResult]) -> bool:
    if c.bbox is None:
        return False
    cx, cy = (c.bbox[0] + c.bbox[2]) / 2, c.bbox[1]
    return any(
        t.page_no == c.page_no and t.bbox and t.bbox[0] <= cx <= t.bbox[2] and t.bbox[1] <= cy <= t.bbox[3]
        for t in tables
    )


def extract_fields(ex: ExtractionResult) -> FieldExtraction:
    lines = ex.all_lines()
    # Table cells are handled by the key-value pass; labelled-line reads inside tables cross columns.
    labelled = [c for c in _from_labelled_lines(lines) if not _inside_table(c, ex.tables)]
    candidates = _from_kv_tables(ex.tables) + labelled + _from_patterns(lines)
    singles = [c for c in candidates if c.group_index is None]

    best: dict[str, Candidate] = {}
    alternates: dict[str, list[Candidate]] = {}
    for c in sorted(singles, key=lambda c: (-c.priority, c.page_no or 0)):
        if c.normalized is None and c.field_key in ID_FIELDS | DATE_FIELDS | {"academic_year"}:
            continue  # value did not parse; not usable as this field
        if c.field_key not in best:
            best[c.field_key] = c
        else:
            alternates.setdefault(c.field_key, []).append(c)

    doc_ay = best["academic_year"].normalized if "academic_year" in best else None
    prog_cands, programs = _program_tables(ex.tables, doc_ay)
    if "pincode" not in best and "institution_address" in best:
        pin = normalize_value("pincode", best["institution_address"].value)
        if pin:
            a = best["institution_address"]
            best["pincode"] = Candidate("pincode", pin, pin, a.original_text, a.page_no, a.bbox, a.confidence, a.basis, "derived:address")
    return FieldExtraction(list(best.values()) + prog_cands, alternates, programs)
