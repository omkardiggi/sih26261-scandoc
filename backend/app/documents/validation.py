"""Document-level validation rules. Each rule returns explainable results with evidence links."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from rapidfuzz import fuzz

from ..models import DocumentCategory, Institution, Program, VerificationCase
from .fields import FIELD_LABELS, Candidate, FieldExtraction
from .normalize import compare_names, find_name_in_text, normalize_academic_year, normalize_name, parse_int

NAME_EXACT = 100.0
NAME_CLOSE = 90.0


@dataclass
class RuleResult:
    rule_key: str
    rule_label: str
    category: str
    status: str  # pass | fail | warning | not_applicable | unable
    message: str
    details: dict = field(default_factory=dict)
    evidence: list = field(default_factory=list)


def _ev(c: Candidate | None, field_id: str | None = None) -> list[dict]:
    if c is None:
        return []
    return [{"type": "field", "field_key": c.field_key, "field_id": field_id, "page_no": c.page_no, "bbox": c.bbox}]


@dataclass
class ValidationContext:
    fields: dict[str, Candidate]
    extraction: FieldExtraction
    category: DocumentCategory | None
    institution: Institution
    case: VerificationCase | None
    registry_programs: list[Program]
    field_ids: dict[str, str]
    pages: list[dict]  # [{"page_no", "is_blank", "unreadable", "ocr_confidence", "text_source"}]
    duplicate_of: dict | None
    review_date: date
    low_ocr_threshold: float
    full_text: str = ""

    def fid(self, key: str) -> str | None:
        return self.field_ids.get(key)


def rule_expected_fields(ctx: ValidationContext) -> list[RuleResult]:
    if ctx.category is None or not ctx.category.expected_fields:
        return [RuleResult("expected_fields", "Expected fields present", "field", "not_applicable",
                           "No expected fields are configured for this document category.")]
    missing = [k for k in ctx.category.expected_fields if k not in ctx.fields]
    if not missing:
        return [RuleResult("expected_fields", "Expected fields present", "field", "pass",
                           f"All {len(ctx.category.expected_fields)} expected fields were located.",
                           {"expected": ctx.category.expected_fields})]
    return [RuleResult(
        "expected_fields", "Expected fields present", "field", "warning",
        "Not located in the document: " + ", ".join(FIELD_LABELS.get(k, k) for k in missing)
        + ". The field may be absent, unreadable, or in a layout the extractor does not recognise.",
        {"expected": ctx.category.expected_fields, "missing": missing,
         "basis": "Platform default expectations for this category (configurable); not an official checklist."},
    )]


def rule_identifier_formats(ctx: ValidationContext) -> list[RuleResult]:
    out = []
    for key in ("aicte_permanent_id", "application_id"):
        c = ctx.fields.get(key)
        if c is None:
            continue
        ok = c.normalized is not None and re.fullmatch(r"1-\d{6,12}", c.normalized or "") is not None
        out.append(RuleResult(
            f"format_{key}", f"{FIELD_LABELS[key]} format", "format", "pass" if ok else "fail",
            f"'{c.value}' {'matches' if ok else 'does not match'} the AICTE identifier pattern '1-' followed by 6–12 digits "
            "(pattern observed on AICTE-issued letters).",
            {"original": c.value, "normalized": c.normalized}, _ev(c, ctx.fid(key))))
    pin = ctx.fields.get("pincode")
    if pin is not None:
        ok = bool(re.fullmatch(r"[1-9]\d{5}", pin.normalized or ""))
        out.append(RuleResult("format_pincode", "PIN code format", "format", "pass" if ok else "fail",
                              f"PIN code '{pin.value}' {'is' if ok else 'is not'} a 6-digit Indian postal code.",
                              {"original": pin.value}, _ev(pin, ctx.fid("pincode"))))
    return out


def rule_academic_year(ctx: ValidationContext) -> list[RuleResult]:
    out = []
    c = ctx.fields.get("academic_year")
    if c is None:
        return out
    ay, matched, consecutive = normalize_academic_year(c.value)
    out.append(RuleResult(
        "academic_year_format", "Academic year format", "academic_year", "pass" if consecutive else "fail",
        f"Academic year '{c.value}' " + ("covers consecutive years." if consecutive else "does not cover two consecutive years."),
        {"original": c.value, "normalized": ay}, _ev(c, ctx.fid("academic_year"))))
    if ctx.case is not None and ay:
        same = ay == ctx.case.academic_year
        out.append(RuleResult(
            "academic_year_matches_case", "Academic year matches the case", "academic_year", "pass" if same else "fail",
            f"Document academic year {ay} {'matches' if same else 'differs from'} the case academic year {ctx.case.academic_year}.",
            {"document": ay, "case": ctx.case.academic_year}, _ev(c, ctx.fid("academic_year"))))
    return out


def _date(c: Candidate | None) -> date | None:
    if c is None or not c.normalized:
        return None
    try:
        return date.fromisoformat(c.normalized)
    except ValueError:
        return None


def rule_dates(ctx: ValidationContext) -> list[RuleResult]:
    out = []
    issue_c, until_c, from_c = ctx.fields.get("issue_date"), ctx.fields.get("valid_until"), ctx.fields.get("valid_from")
    issue, until, start = _date(issue_c), _date(until_c), _date(from_c)
    if issue_c is not None:
        future = issue is not None and issue > ctx.review_date
        out.append(RuleResult(
            "issue_date_not_future", "Issue date is not in the future", "date", "fail" if future else "pass",
            f"Issue date {issue.isoformat() if issue else issue_c.value} is "
            + ("after the review date — the date is inconsistent." if future else "on or before the review date."),
            {"original": issue_c.value, "normalized": issue_c.normalized, "review_date": ctx.review_date.isoformat()},
            _ev(issue_c, ctx.fid("issue_date"))))
    if until and issue:
        ok = until >= issue
        out.append(RuleResult(
            "validity_after_issue", "Validity ends after issue", "date", "pass" if ok else "fail",
            f"Validity end {until.isoformat()} is {'after' if ok else 'before'} the issue date {issue.isoformat()}.",
            {"issue_date": issue.isoformat(), "valid_until": until.isoformat()},
            _ev(issue_c, ctx.fid("issue_date")) + _ev(until_c, ctx.fid("valid_until"))))
    if until and start and until < start:
        out.append(RuleResult("validity_range", "Validity range", "date", "fail",
                              f"Validity end {until} precedes validity start {start}.", {},
                              _ev(from_c, ctx.fid("valid_from")) + _ev(until_c, ctx.fid("valid_until"))))
    applicable = bool(ctx.category and ctx.category.validity_applicable)
    if until is not None:
        expired = until < ctx.review_date
        out.append(RuleResult(
            "not_expired", "Document is within its stated validity", "expiry", "fail" if expired else "pass",
            (f"The document states validity until {until.isoformat()}, which is before the review date "
             f"{ctx.review_date.isoformat()}." if expired else f"Stated validity runs until {until.isoformat()}."),
            {"valid_until": until.isoformat(), "review_date": ctx.review_date.isoformat(),
             "basis": "Validity period stated on the document itself."},
            _ev(until_c, ctx.fid("valid_until"))))
    elif applicable:
        out.append(RuleResult(
            "validity_stated", "Validity period stated", "expiry", "warning",
            "No validity end date was located. The applicable requirement refers to a valid certificate; confirm validity manually.",
            {}))
    return out


def rule_institution_identity(ctx: ValidationContext) -> list[RuleResult]:
    out = []
    inst = ctx.institution
    c = ctx.fields.get("institution_name")
    places = (inst.district, inst.state)
    if c is not None:
        cmp = compare_names(c.value, inst.name, places=places)
        out.append(RuleResult(
            "institution_name_match", "Institution name matches registry", "identity", cmp.status, cmp.reason,
            {"document_original": c.value, "registry_original": inst.name, "registry_name_compared": cmp.registry_name,
             "similarity": cmp.score, "words_matched": cmp.matched, "words_missing": cmp.missing, "words_extra": cmp.extra,
             "notes": cmp.notes, "method": "word-by-word comparison ignoring case, punctuation, spelling variants and OCR slips"},
            _ev(c, ctx.fid("institution_name"))))
    if c is None and ctx.full_text and inst.name:
        # No labelled name field: the institution must still be named somewhere in the document.
        cmp = find_name_in_text(ctx.full_text, inst.name, places)
        out.append(RuleResult(
            "institution_name_in_document", "Institution name appears in the document", "identity", cmp.status, cmp.reason,
            {"document_original": cmp.document_name or None, "registry_original": inst.name, "registry_name_compared": cmp.registry_name,
             "similarity": cmp.score, "words_found": cmp.matched, "words_missing": cmp.missing, "words_extra": cmp.extra,
             "words_expected": cmp.matched + cmp.missing, "notes": cmp.notes}))
    pin = ctx.fields.get("pincode")
    if pin is not None and inst.pincode:
        same = pin.normalized == inst.pincode
        out.append(RuleResult("pincode_match", "PIN code matches registry", "identity", "pass" if same else "fail",
                              f"Document PIN code {pin.normalized} {'matches' if same else 'differs from'} the registry ({inst.pincode}).",
                              {"document": pin.normalized, "registry": inst.pincode}, _ev(pin, ctx.fid("pincode"))))
    addr = ctx.fields.get("institution_address")
    if addr is not None and inst.state:
        ok = normalize_name(inst.state) in normalize_name(addr.value)
        out.append(RuleResult("state_match", "State matches registry", "identity", "pass" if ok else "warning",
                              f"The address {'mentions' if ok else 'does not mention'} {inst.state}.",
                              {"document": addr.value, "registry": inst.state}, _ev(addr, ctx.fid("institution_address"))))
    mb = ctx.fields.get("management_body")
    if mb is not None and inst.management_body:
        score = float(fuzz.token_sort_ratio(normalize_name(mb.value), normalize_name(inst.management_body)))
        status = "pass" if score >= NAME_CLOSE else ("warning" if score >= 75 else "fail")
        out.append(RuleResult("management_body_match", "Trust / society matches registry", "identity", status,
                              f"Document names '{mb.value}'; registry records '{inst.management_body}' (similarity {score:.0f}/100).",
                              {"document_original": mb.value, "registry_original": inst.management_body, "similarity": round(score, 1)},
                              _ev(mb, ctx.fid("management_body"))))
    pid = ctx.fields.get("aicte_permanent_id")
    if pid is not None:
        if not inst.aicte_permanent_id:
            out.append(RuleResult("permanent_id_match", "Permanent ID matches registry", "identity", "not_applicable",
                                  "The registry record has no AICTE Permanent ID to compare against.",
                                  {"document": pid.normalized}, _ev(pid, ctx.fid("aicte_permanent_id"))))
        else:
            same = pid.normalized == inst.aicte_permanent_id
            out.append(RuleResult(
                "permanent_id_match", "Permanent ID matches registry", "identity", "pass" if same else "fail",
                f"Document Permanent ID {pid.normalized} {'matches' if same else 'does not match'} the registry ({inst.aicte_permanent_id}). Exact match required.",
                {"document": pid.normalized, "registry": inst.aicte_permanent_id, "method": "exact"},
                _ev(pid, ctx.fid("aicte_permanent_id"))))
    est = ctx.fields.get("establishment_year")
    if est is not None and inst.establishment_year:
        same = est.normalized == str(inst.establishment_year)
        out.append(RuleResult("establishment_year_match", "Year of establishment matches registry", "identity",
                              "pass" if same else "fail",
                              f"Document states {est.normalized}; registry records {inst.establishment_year}.",
                              {"document": est.normalized, "registry": inst.establishment_year},
                              _ev(est, ctx.fid("establishment_year"))))
    return out


def rule_programs(ctx: ValidationContext) -> list[RuleResult]:
    progs = ctx.extraction.programs
    if not progs:
        return []
    doc_ay = ctx.fields["academic_year"].normalized if "academic_year" in ctx.fields else None
    registry = [p for p in ctx.registry_programs if doc_ay is None or p.academic_year == doc_ay]
    if not registry:
        return [RuleResult("programs_vs_registry", "Programs and intake match registry", "program", "not_applicable",
                           f"No registry programs are recorded for academic year {doc_ay or '(unknown)'} to compare against.",
                           {"document_programs": len(progs)})]
    out = []
    unmatched_registry = {p.id for p in registry}

    def lvl(v):
        return normalize_name(v or "").replace(" ", "")

    for entry in progs:
        # Same course names can appear at several levels (UG/PG) and course types (regular/online): match on level,
        # prefer an unused registry row with the same intake, and never reuse a registry row.
        scored = []
        for p in registry:
            score = float(fuzz.token_sort_ratio(normalize_name(entry["course"]), normalize_name(p.course)))
            if score < 85:
                continue
            if entry.get("level") and p.level and lvl(entry["level"]) != lvl(p.level):
                continue
            scored.append((p.id in unmatched_registry, entry["intake"] is not None and p.approved_intake == entry["intake"], score, p))
        scored.sort(key=lambda t: (t[0], t[1], t[2]), reverse=True)
        best, best_score = (scored[0][3], scored[0][2]) if scored else (None, 0.0)
        evidence = [{"type": "field", "field_key": "program_course", "group_index": entry["group_index"], "page_no": entry["page_no"]}]
        if best is None:
            out.append(RuleResult("program_in_registry", "Program present in registry", "program", "fail",
                                  f"Course '{entry['course']}' has no matching program in the registry for {doc_ay}.",
                                  {"course": entry["course"]}, evidence))
            continue
        unmatched_registry.discard(best.id)
        if entry["intake"] is None or best.approved_intake is None:
            out.append(RuleResult("program_intake_match", "Approved intake matches registry", "program", "unable",
                                  f"Intake for '{entry['course']}' could not be compared (missing value).",
                                  {"course": entry["course"], "document_intake": entry["intake_raw"], "registry_intake": best.approved_intake}, evidence))
            continue
        same = entry["intake"] == best.approved_intake
        out.append(RuleResult(
            "program_intake_match", "Approved intake matches registry", "program", "pass" if same else "fail",
            f"'{entry['course']}': document intake {entry['intake']} vs registry {best.approved_intake}"
            + ("" if best_score == 100 else f" (course name similarity {best_score:.0f}/100)") + ".",
            {"course": entry["course"], "registry_course": best.course, "document_intake": entry["intake"],
             "registry_intake": best.approved_intake, "name_similarity": round(best_score, 1)}, evidence))
    for pid in unmatched_registry:
        p = next(x for x in registry if x.id == pid)
        out.append(RuleResult("registry_program_in_document", "Registry program present in document", "program", "warning",
                              f"Registry program '{p.course}' ({p.academic_year}) was not found in this document.",
                              {"registry_course": p.course}))
    return out


def rule_table_totals(ctx: ValidationContext, tables: list) -> list[RuleResult]:
    out = []
    for t in tables:
        total_rows = [r for r in t.rows[1:] if r and str(r[0]).strip().lower().startswith("total")]
        if not total_rows:
            continue
        body = [r for r in t.rows[1:] if r not in total_rows]
        for tr in total_rows:
            for ci in range(1, len(tr)):
                declared = parse_int(tr[ci])
                if declared is None:
                    continue
                vals = [parse_int(r[ci]) for r in body if ci < len(r)]
                nums = [v for v in vals if v is not None]
                if len(nums) < 2:
                    continue
                computed = sum(nums)
                ok = computed == declared
                col = t.header[ci] if ci < len(t.header) else f"column {ci + 1}"
                out.append(RuleResult(
                    "table_total", "Table totals are arithmetically correct", "arithmetic", "pass" if ok else "fail",
                    f"Column '{col}' on page {t.page_no}: stated total {declared}, sum of rows {computed}.",
                    {"column": col, "declared": declared, "computed": computed, "rows_summed": len(nums)},
                    [{"type": "page", "page_no": t.page_no, "bbox": t.bbox}]))
    return out


def rule_pages(ctx: ValidationContext) -> list[RuleResult]:
    out = []
    unreadable = [p["page_no"] for p in ctx.pages if p["unreadable"]]
    blank = [p["page_no"] for p in ctx.pages if p["is_blank"]]
    low = [p["page_no"] for p in ctx.pages if p["ocr_confidence"] is not None and p["ocr_confidence"] < ctx.low_ocr_threshold and not p["unreadable"]]
    if unreadable:
        out.append(RuleResult("pages_readable", "All pages are readable", "pages", "fail",
                              f"Page(s) {', '.join(map(str, unreadable))} could not be read reliably. Request a clearer copy.",
                              {"pages": unreadable}, [{"type": "page", "page_no": p} for p in unreadable]))
    else:
        out.append(RuleResult("pages_readable", "All pages are readable", "pages", "pass",
                              f"Text was read from all {len(ctx.pages) - len(blank)} non-blank page(s)."))
    if blank:
        out.append(RuleResult("blank_pages", "Blank pages", "pages", "warning",
                              f"Page(s) {', '.join(map(str, blank))} appear blank. Confirm no content is missing.",
                              {"pages": blank}, [{"type": "page", "page_no": p} for p in blank]))
    if low:
        out.append(RuleResult("ocr_confidence", "Text recognition confidence", "pages", "warning",
                              f"Page(s) {', '.join(map(str, low))} have low text-recognition confidence; extracted values need manual checking. "
                              "(Recognition confidence reflects reading quality, not authenticity.)",
                              {"pages": low, "threshold": ctx.low_ocr_threshold}, [{"type": "page", "page_no": p} for p in low]))
    return out


def rule_duplicate(ctx: ValidationContext) -> list[RuleResult]:
    if not ctx.duplicate_of:
        return [RuleResult("duplicate_file", "File is not a duplicate", "duplicate", "pass",
                           "No previously uploaded file has the same SHA-256 hash.")]
    d = ctx.duplicate_of
    return [RuleResult("duplicate_file", "File is not a duplicate", "duplicate", "warning",
                       f"An identical file (same SHA-256) was already uploaded as '{d['title']}' on {d['uploaded_at']}"
                       + (" for a different institution." if d.get("other_institution") else "."),
                       d, [{"type": "document", "document_id": d["document_id"]}])]


def rule_internal_consistency(ctx: ValidationContext) -> list[RuleResult]:
    out = []
    for key in ("aicte_permanent_id", "application_id", "institution_name", "establishment_year"):
        primary = ctx.fields.get(key)
        if primary is None:
            continue
        conflicting = [a for a in ctx.extraction.alternates.get(key, []) if a.priority >= 2 and a.normalized and a.normalized != primary.normalized]
        if key == "institution_name":
            conflicting = [a for a in conflicting if fuzz.token_sort_ratio(a.normalized, primary.normalized) < NAME_CLOSE]
        if key == "establishment_year":
            conflicting = [a for a in conflicting if a.method == "table_key_value"]  # free-text years are usually other dates
        if conflicting:
            vals = sorted({a.normalized for a in conflicting})
            out.append(RuleResult(
                f"consistent_{key}", f"{FIELD_LABELS[key]} is consistent within the document", "contradiction", "fail",
                f"The document states {FIELD_LABELS[key]} as '{primary.normalized}' and also as {', '.join(repr(v) for v in vals)}.",
                {"primary": primary.normalized, "others": vals},
                _ev(primary, ctx.fid(key)) + [{"type": "page", "page_no": a.page_no, "bbox": a.bbox} for a in conflicting]))
    return out


def run_document_rules(ctx: ValidationContext, tables: list) -> list[RuleResult]:
    results: list[RuleResult] = []
    for rule in (rule_pages, rule_expected_fields, rule_identifier_formats, rule_academic_year, rule_dates,
                 rule_institution_identity, rule_programs, rule_duplicate, rule_internal_consistency):
        results.extend(rule(ctx))
    results.extend(rule_table_totals(ctx, tables))
    return results
