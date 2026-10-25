# Verification methodology

Automated checks assist officers. No check — and no combination of checks — approves, rejects or certifies a document or institution. There is **no single risk score**; each dimension is reported separately.

## Dimensions per document

1. **Extraction** — fields with page, bounding box, source text, method (table key-value, labelled line, pattern) and *text-reading* confidence (OCR confidence is never treated as authenticity).
2. **Validation** — rules in `backend/app/documents/validation.py` (pass / fail / warning / not applicable / unable), each with original and normalised values. Names: exact after normalisation passes; similarity ≥ 90 is a *warning requiring officer confirmation*; below fails. Identifiers: exact only.
3. **Official records** — attempts recorded with source, method (automated / officer-recorded), HTTP status, response hash and encrypted response. Missing or failed responses are never success.
4. **Cross-document** — values compared across documents in a case (conflict / close match / consistent / single source).
5. **Integrity** — separate dimensions: file integrity, signature validity, issuer identity, tampering indicators (with low/medium/high significance), active content, visual elements. Missing metadata, scans and compression are not indicators by themselves.

## Document status precedence

`rejected` (officer decision) → `possible_tampering` (any high-significance indicator or broken signature/hash) → `information_mismatch` (failed identity/program/date/expiry/format/contradiction/arithmetic rules or an official mismatch) → `requires_manual_review` (unreadable pages, medium indicators, close-match warnings) → `verified_official` (all checked claims supported by an official source) → `unable_to_verify` → `pending_official_verification`.

## Case status

`decision_recorded` → no documents or processing → `submission_incomplete` (missing mandatory requirement) → `awaiting_corrective_documents` → `discrepancies_identified` (open findings) → `pending_review` (documents without officer review) → `ready_for_decision`.

## Discrepancy severity (platform rule, not regulatory)

identity, expiry, contradiction, missing requirement: high · program, academic year, date, arithmetic: medium · format, pages: low. Inspection severities come only from officer-configured class rules or officer assessments; otherwise "not assessed".

## Known limitations

- Field extraction is rule-based and tuned on the public AICTE EoA layout; other layouts may need officer correction (corrections are recorded, originals preserved).
- Error-level analysis and duplicated-region detection are heuristic indicators with false positives (stamps, photos, repeated print).
- Rendered-vs-embedded comparison checks at most 5 pages per document.
- Files are decrypted in memory to serve them; very large videos increase memory use (use chunked storage in production).
- The AICTE QR page is parsed as HTML without a published schema.
- DNS-rebinding protection is best-effort (destination validated before and peer address checked after connect); use an egress firewall in production.
