"""Official-record verification adapters.

* ``aicte_qr_status`` — AICTE's issuer-hosted approval-status page linked from the QR code
  printed on AICTE approval letters (observed on 2026-27 EoA letters). Only URLs whose host
  is exactly in the configured allowlist are fetched, and only when an officer requests it.
* ``generic_rest`` — a configurable JSON API adapter for authorised services whose
  documentation you provide. Nothing is assumed about endpoints that are not configured.

A missing, failed or unparseable response is never treated as a successful verification.
"""

from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import quote, urlsplit

from rapidfuzz import fuzz

from ..documents.normalize import normalize_academic_year, normalize_aicte_id, normalize_name, parse_int
from ..models import Integration
from . import http
from .secrets import resolve_secret

DEFAULT_QR_HOSTS = ["approvalstatus.aicte.gov.in"]
QR_PATH_PREFIX = "/api/getInstituteDetails/"


@dataclass
class VerificationOutcome:
    outcome: str  # verified | mismatch | not_found | unable_to_verify | error
    reason: str
    next_action: str | None = None
    http_status: int | None = None
    duration_ms: int | None = None
    source_name: str | None = None
    source_url: str | None = None
    response_body: bytes | None = None
    response_content_type: str | None = None
    matched_values: dict = field(default_factory=dict)
    request_summary: dict = field(default_factory=dict)
    official_record: dict = field(default_factory=dict)
    connection_ok: bool | None = None
    auth_failed: bool = False


# ---------------------------------------------------------------- HTML table parsing

class _TableParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows: list[list[str]] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1
        elif tag == "tr":
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self._skip = max(0, self._skip - 1)
        elif tag in ("td", "th") and self._row is not None and self._cell is not None:
            self._row.append(re.sub(r"\s+", " ", "".join(self._cell)).strip())
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if any(self._row):
                self.rows.append(self._row)
            self._row = None

    def handle_data(self, data):
        if self._cell is not None and not self._skip:
            self._cell.append(data)


QR_FIELD_MAP = {
    "name of the institute": "institution_name",
    "name of the institution": "institution_name",
    "academic year": "academic_year",
    "application id": "application_id",
    "permanent id": "aicte_permanent_id",
    "status": "approval_status",
    "sub status": "approval_sub_status",
    "address of the institute": "institution_address",
    "application type": "application_type",
    "institution type": "institution_type",
    "region": "region",
    "city/village": "city",
    "district": "district",
    "state": "state",
}


def parse_aicte_status_page(html_text: str) -> dict:
    p = _TableParser()
    p.feed(html_text)
    fields: dict[str, str] = {}
    courses: list[dict] = []
    header: list[str] | None = None
    title = None
    for row in p.rows:
        low = [c.lower() for c in row]
        if title is None and len(row) <= 2 and "approval process" in " ".join(low):
            title = " ".join(c for c in row if c)
            continue
        if "course" in low and any("intake" in c for c in low):
            header = row
            continue
        if header and len(row) == len(header):
            courses.append(dict(zip(header, row)))
            continue
        for i in range(0, len(row) - 1, 2):
            key = QR_FIELD_MAP.get(row[i].strip().lower())
            if key and row[i + 1]:
                fields[key] = row[i + 1]
    intake_col = next((h for h in (header or []) if "intake" in h.lower()), None)
    course_col = next((h for h in (header or []) if h.lower() == "course"), None)
    norm_courses = [
        {"course": c.get(course_col, ""), "level": c.get("Level"), "program": c.get("Program"),
         "affiliating_body": next((v for k, v in c.items() if "affiliating" in k.lower()), None),
         "intake": parse_int(c.get(intake_col, "")), "course_type": c.get("Course Type")}
        for c in courses if course_col
    ]
    return {"title": title, "fields": fields, "courses": norm_courses, "intake_column": intake_col}


def qr_allowed(url: str, integration: Integration) -> bool:
    hosts = (integration.config or {}).get("allowed_hosts") or DEFAULT_QR_HOSTS
    parts = urlsplit(url)
    return (parts.scheme == "https" and parts.hostname in hosts and not parts.username and not parts.port
            and parts.path.startswith((integration.config or {}).get("path_prefix", QR_PATH_PREFIX)))


def _compare(doc_fields: dict[str, str | None], official: dict, doc_programs: list[dict]) -> tuple[dict, list[str]]:
    matched: dict[str, dict] = {}
    differences: list[str] = []
    of = official["fields"]

    def put(key, doc_v, off_v, ok, note=None):
        matched[key] = {"document": doc_v, "official": off_v, "match": ok, **({"note": note} if note else {})}
        if not ok:
            differences.append(f"{key.replace('_', ' ')}: document '{doc_v}' vs official '{off_v}'")

    for key in ("application_id", "aicte_permanent_id"):
        if doc_fields.get(key) and of.get(key):
            put(key, doc_fields[key], of[key], normalize_aicte_id(doc_fields[key]) == normalize_aicte_id(of[key]))
    if doc_fields.get("academic_year") and of.get("academic_year"):
        a, b = normalize_academic_year(doc_fields["academic_year"])[0], normalize_academic_year(of["academic_year"])[0]
        put("academic_year", doc_fields["academic_year"], of["academic_year"], a is not None and a == b)
    if doc_fields.get("institution_name") and of.get("institution_name"):
        na, nb = normalize_name(doc_fields["institution_name"]), normalize_name(of["institution_name"])
        score = fuzz.token_sort_ratio(na, nb)
        put("institution_name", doc_fields["institution_name"], of["institution_name"], na == nb,
            None if na == nb else f"similarity {score:.0f}/100 — exact match after normalisation is required")
    if doc_programs and official["courses"]:
        def ckey(c: dict) -> tuple[str, str]:
            # Same course names can exist at different levels (UG/PG) and course types (regular/online), so compare
            # the multiset of intakes per (level, course). An intake of 0 means "not offered" on either side.
            return (normalize_name(c.get("level") or "").replace(" ", ""), normalize_name(c["course"]).replace(" ", ""))

        def bucket(items):
            out: dict[tuple[str, str], list] = {}
            for c in items:
                if c.get("intake") == 0:
                    continue
                out.setdefault(ckey(c), []).append(c)
            return out

        doc_b, off_b = bucket(doc_programs), bucket(official["courses"])
        for k in sorted(set(doc_b) | set(off_b)):
            d_items, o_items = doc_b.get(k, []), off_b.get(k, [])
            ref = (d_items or o_items)[0]
            label = f"intake: {ref['course']}" + (f" ({ref['level']})" if ref.get("level") else "")
            d_vals = sorted((x.get("intake") for x in d_items), key=lambda v: (v is None, v))
            o_vals = sorted((x.get("intake") for x in o_items), key=lambda v: (v is None, v))
            fmt = lambda vals: (None if not vals else vals[0] if len(vals) == 1 else ", ".join(str(v) for v in vals))  # noqa: E731
            if not o_items:
                put(label, fmt(d_vals), None, False, "course not listed in the official record")
            elif not d_items:
                put(label, None, fmt(o_vals), False, "official course missing from the document")
            else:
                put(label, fmt(d_vals), fmt(o_vals), d_vals == o_vals and None not in d_vals)
    return matched, differences


def verify_via_qr(integration: Integration, url: str, doc_fields: dict, doc_programs: list[dict]) -> VerificationOutcome:
    name = "AICTE approval-status page (issuer QR link)"
    if not qr_allowed(url, integration):
        return VerificationOutcome("unable_to_verify", "The QR link does not point to an allowlisted official AICTE address, so it was not opened.",
                                   "Verify the document through another official channel; a QR code pointing elsewhere may itself be a concern.",
                                   source_name=name, source_url=None, request_summary={"url_host": urlsplit(url).hostname})
    try:
        resp = http.request("GET", url, headers={"Accept": "text/html,application/json", "User-Agent": "Docura/1.0"})
    except http.OutboundBlocked as exc:
        return VerificationOutcome("unable_to_verify", f"Request blocked by outbound safety rules: {exc}", source_name=name, source_url=url)
    except http.OutboundError as exc:
        return VerificationOutcome("error", f"The official page could not be reached: {exc}",
                                   "Retry later; if the problem persists, verify through the AICTE portal manually.",
                                   source_name=name, source_url=url, connection_ok=False)
    base = dict(http_status=resp.status_code, duration_ms=resp.duration_ms, source_name=name, source_url=url,
                response_body=resp.body, response_content_type=resp.headers.get("content-type"),
                request_summary={"method": "GET", "url": url})
    if resp.status_code != 200:
        return VerificationOutcome("error", f"The official page returned HTTP {resp.status_code}.", "Retry later or verify manually.",
                                   connection_ok=False, **base)
    parsed = parse_aicte_status_page(resp.body.decode("utf-8", "replace"))
    if not parsed["fields"]:
        return VerificationOutcome("not_found", "The official page did not contain institution details for this QR token.",
                                   "Treat as unverified. Ask the institution for the official letter or check the AICTE portal.",
                                   connection_ok=True, official_record=parsed, **base)
    matched, differences = _compare(doc_fields, parsed, doc_programs)
    if not matched:
        return VerificationOutcome("unable_to_verify", "The official record was retrieved but no document fields could be compared with it.",
                                   "Review the extracted fields and the official record manually.", connection_ok=True,
                                   official_record=parsed, matched_values=matched, **base)
    if differences:
        return VerificationOutcome("mismatch", "The official record differs from the document: " + "; ".join(differences[:8]),
                                   "Investigate the differences with the institution and the issuing office.", connection_ok=True,
                                   official_record=parsed, matched_values=matched, **base)
    return VerificationOutcome("verified", f"All {len(matched)} compared values match the official AICTE record "
                               f"(status shown by AICTE: {parsed['fields'].get('approval_status', 'not stated')}).",
                               connection_ok=True, official_record=parsed, matched_values=matched, **base)


# ---------------------------------------------------------------- configurable REST adapter

def _get_path(obj, path: str):
    cur = obj
    for part in [p for p in path.split(".") if p]:
        if isinstance(cur, list) and part.isdigit():
            idx = int(part)
            cur = cur[idx] if idx < len(cur) else None
        elif isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
        if cur is None:
            return None
    return cur


def _fill(template: str, claim: dict) -> str:
    def rep(m):
        v = claim.get(m.group(1))
        if v is None:
            raise KeyError(m.group(1))
        return quote(str(v), safe="")
    return re.sub(r"\{([a-z_]+)\}", rep, template)


def _auth(integration: Integration) -> tuple[dict, dict]:
    headers, params = {}, {}
    if integration.auth_method == "none":
        return headers, params
    secret = resolve_secret(integration)
    if not secret:
        raise PermissionError("The credential for this integration is not configured.")
    if integration.auth_method == "bearer":
        headers["Authorization"] = f"Bearer {secret}"
    elif integration.auth_method == "api_key_header":
        headers[integration.auth_header_name or "X-API-Key"] = secret
    elif integration.auth_method == "basic":
        headers["Authorization"] = "Basic " + base64.b64encode(secret.encode()).decode()
    elif integration.auth_method == "api_key_query":
        params[(integration.config or {}).get("api_key_param", "api_key")] = secret
    return headers, params


def _rest_call(integration: Integration, spec: dict, claim: dict):
    if not integration.base_url:
        raise ValueError("No base URL is configured.")
    headers, params = _auth(integration)
    url = integration.base_url.rstrip("/") + "/" + _fill(spec.get("path", ""), claim).lstrip("/")
    query = {k: _fill(str(v), claim) for k, v in (spec.get("query") or {}).items()}
    body = spec.get("body")
    if body is not None:
        body = json.loads(_fill(json.dumps(body), claim))
    headers.update({k: str(v) for k, v in (spec.get("headers") or {}).items()})
    headers.setdefault("Accept", "application/json")
    resp = http.request(spec.get("method", "GET").upper(), url, headers=headers, params=query | params, json_body=body)
    return resp, {"method": spec.get("method", "GET").upper(), "url": url, "query_keys": sorted(query)}


def verify_rest(integration: Integration, claim_type: str, claim: dict) -> VerificationOutcome:
    cfg = integration.config or {}
    spec = (cfg.get("requests") or {}).get(claim_type) or cfg.get("request")
    name = integration.name
    if not spec:
        return VerificationOutcome("unable_to_verify", f"No request is configured for claim type '{claim_type}'.",
                                   "Add the request mapping from the provider's documentation.", source_name=name)
    try:
        resp, summary = _rest_call(integration, spec, claim)
    except KeyError as exc:
        return VerificationOutcome("unable_to_verify", f"The document does not provide the value required for this request ({exc.args[0]}).",
                                   "Correct or supply the missing field, then retry.", source_name=name)
    except PermissionError as exc:
        return VerificationOutcome("unable_to_verify", str(exc), "Configure the credential (environment variable or encrypted setting).",
                                   source_name=name, auth_failed=True)
    except (http.OutboundBlocked, ValueError) as exc:
        return VerificationOutcome("unable_to_verify", f"Request not sent: {exc}", source_name=name)
    except http.OutboundError as exc:
        return VerificationOutcome("error", f"The service could not be reached: {exc}", "Retry later.", source_name=name, connection_ok=False)
    base = dict(http_status=resp.status_code, duration_ms=resp.duration_ms, source_name=name, source_url=summary["url"],
                response_body=resp.body, response_content_type=resp.headers.get("content-type"), request_summary=summary)
    if resp.status_code in (401, 403):
        return VerificationOutcome("error", f"Authentication was rejected (HTTP {resp.status_code}).", "Check the credential and access scope.",
                                   connection_ok=False, auth_failed=True, **base)
    if resp.status_code == 404:
        return VerificationOutcome("not_found", "The service reported no matching record.", "Treat as unverified and investigate.",
                                   connection_ok=True, **base)
    if resp.status_code >= 400:
        return VerificationOutcome("error", f"The service returned HTTP {resp.status_code}.", "Retry later.", connection_ok=False, **base)
    try:
        payload = resp.json()
    except Exception:  # noqa: BLE001
        return VerificationOutcome("unable_to_verify", "The response was not valid JSON.", connection_ok=True, **base)
    rmap = cfg.get("response") or {}
    found_path = rmap.get("found_path")
    if found_path and _get_path(payload, found_path) in (None, [], {}, ""):
        return VerificationOutcome("not_found", "The response contains no record for the submitted identifiers.", connection_ok=True, **base)
    matched, differences = {}, []
    for key, path in (rmap.get("fields") or {}).items():
        off = _get_path(payload, path)
        doc_v = claim.get(key)
        if off is None or doc_v is None:
            continue
        mode = (cfg.get("match") or {}).get(key, "exact")
        if mode == "name":
            ok = normalize_name(str(doc_v)) == normalize_name(str(off))
        elif mode == "number":
            ok = parse_int(str(doc_v)) == parse_int(str(off))
        else:
            ok = str(doc_v).strip().lower() == str(off).strip().lower()
        matched[key] = {"document": doc_v, "official": off, "match": ok}
        if not ok:
            differences.append(f"{key}: document '{doc_v}' vs official '{off}'")
    if not matched:
        return VerificationOutcome("unable_to_verify", "The response was received but no mapped fields could be compared.",
                                   "Check the response mapping against the provider documentation.", connection_ok=True, **base)
    if differences:
        return VerificationOutcome("mismatch", "Official record differs: " + "; ".join(differences), connection_ok=True,
                                   matched_values=matched, **base)
    return VerificationOutcome("verified", f"All {len(matched)} compared values match the official response.", connection_ok=True,
                               matched_values=matched, **base)


def test_rest(integration: Integration) -> tuple[bool, str, int | None, int | None, bool]:
    """Perform the configured test request. Returns (ok, message, http_status, duration_ms, auth_failed)."""
    spec = (integration.config or {}).get("test_request")
    if not spec:
        return False, "No test request is defined. Add 'test_request' from the provider documentation.", None, None, False
    try:
        resp, _ = _rest_call(integration, spec, spec.get("sample_claim", {}))
    except PermissionError as exc:
        return False, str(exc), None, None, True
    except KeyError as exc:
        return False, f"Test request template needs a sample value for '{exc.args[0]}'.", None, None, False
    except (http.OutboundBlocked, http.OutboundError, ValueError) as exc:
        return False, str(exc), None, None, False
    if resp.status_code in (401, 403):
        return False, f"Authentication rejected (HTTP {resp.status_code}).", resp.status_code, resp.duration_ms, True
    expected = int(spec.get("expect_status", 200))
    if resp.status_code != expected:
        return False, f"Unexpected HTTP {resp.status_code} (expected {expected}).", resp.status_code, resp.duration_ms, False
    if spec.get("expect_json_path"):
        try:
            if _get_path(resp.json(), spec["expect_json_path"]) is None:
                return False, f"Response lacks '{spec['expect_json_path']}'.", resp.status_code, resp.duration_ms, False
        except Exception:  # noqa: BLE001
            return False, "Response is not valid JSON.", resp.status_code, resp.duration_ms, False
    return True, "Test request succeeded and the response matched the expected format.", resp.status_code, resp.duration_ms, False


# ---------------------------------------------------------------- generic issuer QR pages (official government hosts)

DEFAULT_GOV_SUFFIXES = [".gov.in", ".nic.in"]
IDENTIFIER_FIELDS = ("certificate_number", "reference_number", "application_id", "aicte_permanent_id")


def generic_qr_allowed(url: str, integration: Integration) -> bool:
    cfg = integration.config or {}
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or not host or parts.username or parts.port not in (None, 443):
        return False
    if host in [h.lower() for h in cfg.get("allowed_hosts", [])]:
        return True
    return any(host.endswith(sfx) and len(host) > len(sfx) for sfx in cfg.get("allowed_host_suffixes", DEFAULT_GOV_SUFFIXES))


def _page_text(body: bytes, content_type: str | None) -> str:
    ct = (content_type or "").lower()
    if "pdf" in ct or body[:5] == b"%PDF-":
        import pymupdf

        with pymupdf.open(stream=body, filetype="pdf") as doc:
            return "\n".join(p.get_text() for p in doc)[:500_000]
    text = body.decode("utf-8", "replace")
    if "json" in ct:
        return text
    text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    import html as _html

    return re.sub(r"\s+", " ", _html.unescape(text))[:500_000]


def _compact(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def verify_generic_qr(integration: Integration, url: str, doc_fields: dict) -> VerificationOutcome:
    """Fetch an issuer verification page linked by the document's QR code and check which document values it shows.

    This proves presence, not absence: values missing from the page lead to 'unable to verify', never to a mismatch,
    because the page layout is unknown (it may need JavaScript, a captcha or show only part of the record).
    """
    from ..documents.normalize import parse_date, DATE_ANY_RE

    host = urlsplit(url).hostname
    name = f"Issuer verification page ({host})"
    if not generic_qr_allowed(url, integration):
        return VerificationOutcome("unable_to_verify", f"The QR link points to {host}, which is not an allowlisted official government address, so it was not opened.",
                                   "Verify through the issuer's official website directly. A QR code pointing to an unexpected address may itself be a concern.",
                                   source_name=name, request_summary={"url_host": host})
    try:
        resp = http.request("GET", url, headers={"Accept": "text/html,application/json,application/pdf", "User-Agent": "Docura/1.0"})
    except http.OutboundBlocked as exc:
        return VerificationOutcome("unable_to_verify", f"Request blocked by outbound safety rules: {exc}", source_name=name, source_url=url)
    except http.OutboundError as exc:
        return VerificationOutcome("error", f"The issuer page could not be reached: {exc}", "Retry later or open the link manually.",
                                   source_name=name, source_url=url, connection_ok=False)
    base = dict(http_status=resp.status_code, duration_ms=resp.duration_ms, source_name=name, source_url=url, response_body=resp.body,
                response_content_type=resp.headers.get("content-type"), request_summary={"method": "GET", "url": url})
    if resp.status_code == 404:
        return VerificationOutcome("not_found", "The issuer page reports that the record does not exist (HTTP 404).",
                                   "Treat as unverified and confirm with the issuing office.", connection_ok=True, **base)
    if resp.status_code != 200:
        return VerificationOutcome("error", f"The issuer page returned HTTP {resp.status_code}.", "Open the link manually.", connection_ok=False, **base)
    text = _page_text(resp.body, resp.headers.get("content-type"))
    if len(text.strip()) < 40:
        return VerificationOutcome("unable_to_verify", "The issuer page has no readable content here (it may need JavaScript or a captcha).",
                                   "Open the link in a browser, check it, and record the result with a screenshot.", connection_ok=True, **base)
    page_compact = _compact(text)
    page_dates = {d.isoformat() for m in DATE_ANY_RE.finditer(text) if (d := parse_date(m.group(0))[0])}
    matched: dict[str, dict] = {}
    for key in IDENTIFIER_FIELDS:
        v = doc_fields.get(key)
        if v and len(_compact(v)) >= 4:
            matched[key] = {"document": v, "official": "shown on issuer page" if _compact(v) in page_compact else "not found on page",
                            "match": _compact(v) in page_compact}
    nm = doc_fields.get("institution_name")
    if nm:
        words = [w for w in normalize_name(nm).split() if len(w) > 2]
        page_norm = normalize_name(text)
        ok = bool(words) and all(w in page_norm for w in words)
        matched["institution_name"] = {"document": nm, "official": "shown on issuer page" if ok else "not found on page", "match": ok}
    for key in ("issue_date", "valid_until"):
        v = doc_fields.get(key)
        d = parse_date(v)[0] if v else None
        if d:
            ok = d.isoformat() in page_dates
            matched[key] = {"document": v, "official": "shown on issuer page" if ok else "not found on page", "match": ok}
    ids = [k for k in IDENTIFIER_FIELDS if k in matched]
    if not ids:
        return VerificationOutcome("unable_to_verify", "The document has no certificate or reference number to look for on the issuer page.",
                                   "Check the page manually and record the result.", connection_ok=True, matched_values=matched, **base)
    if any(matched[k]["match"] for k in ids) and all(v["match"] for k, v in matched.items() if k in ("institution_name",) + IDENTIFIER_FIELDS):
        shown = ", ".join(k.replace("_", " ") for k, v in matched.items() if v["match"])
        return VerificationOutcome("verified", f"The official issuer page ({host}) shows the document's {shown}.", connection_ok=True,
                                   matched_values=matched, **base)
    missing = ", ".join(k.replace("_", " ") for k, v in matched.items() if not v["match"])
    return VerificationOutcome("unable_to_verify", f"The issuer page was reached, but these document values were not found on it: {missing}.",
                               "Open the link and compare manually; if the page shows different details, record a mismatch with a screenshot.",
                               connection_ok=True, matched_values=matched, **base)
