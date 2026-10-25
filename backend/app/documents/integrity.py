"""Integrity and tampering analysis.

Dimensions are reported separately and never collapsed into a single score:
  file_integrity     — the stored file is byte-identical to what was uploaded
  signature          — embedded digital signatures (cryptographic validity, trust, coverage)
  issuer_identity    — who the signer / stated issuer is (not the same as official confirmation)
  tampering          — indicators that content may have been altered (each with significance)
  content_security   — active content found in the file (never executed)
  visual_elements    — visual elements for officer review (e.g. coloured ink regions)

Indicators are evidence for an officer to examine, not proof of forgery. Missing
metadata, scanned PDFs and compression are not treated as problems by themselves.
"""

from __future__ import annotations

import io
import logging
import re
from pathlib import Path
from dataclasses import dataclass, field
from datetime import datetime

import cv2
import numpy as np
from PIL import Image

from ..config import get_settings
from ..security.crypto import sha256_hex
from .extract import ExtractionResult
from .fields import FIELD_LABELS as FIELD_LABEL, Candidate
from .normalize import AICTE_ID_RE, clean_text, digits_tokens, word_tokens
from .ocr import run_ocr

log = logging.getLogger("app.integrity")

IMAGE_EDITORS = ["photoshop", "gimp", "paint.net", "pixlr", "affinity photo", "corel photo", "picsart", "snapseed"]
PDF_EDITORS = ["pdfescape", "sejda", "pdf-xchange editor", "foxit phantompdf", "nitro pro", "inkscape", "canva",
               "smallpdf", "ilovepdf", "pdfelement", "libreoffice draw"]


@dataclass
class Artifact:
    kind: str
    page_no: int | None
    png: bytes
    width: int
    height: int
    meta: dict = field(default_factory=dict)


@dataclass
class Check:
    check_type: str
    dimension: str
    status: str  # pass | warning | fail | info | not_applicable | error | unable
    title: str
    summary: str
    details: dict = field(default_factory=dict)
    evidence: list = field(default_factory=list)
    artifacts: list[Artifact] = field(default_factory=list)

    @property
    def significance(self) -> str:
        return self.details.get("significance", "none")


def _png_bytes(arr_rgb: np.ndarray, max_side: int = 1400) -> tuple[bytes, int, int]:
    img = Image.fromarray(arr_rgb)
    if max(img.size) > max_side:
        r = max_side / max(img.size)
        img = img.resize((int(img.width * r), int(img.height * r)), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue(), img.width, img.height


# ---------------------------------------------------------------- file integrity

# Values whose alteration changes what a document says; indicators elsewhere on the page are informational only.
KEY_FIELDS = {"aicte_permanent_id", "application_id", "reference_number", "certificate_number", "issue_date", "valid_until",
              "valid_from", "establishment_year", "program_intake", "institution_name", "academic_year", "pincode"}


def _key_hits(page_no: int, bbox, fields, tight: bool = False) -> list[str]:
    """Key extracted values whose box overlaps ``bbox`` (normalised [x0, y0, x1, y1]) on the same page.

    ``tight``: the region must be local to the value (an edit leaves a patch around it, not a band across the page).
    """
    if not bbox or not fields:
        return []
    x0, y0, x1, y1 = bbox
    area = max(0.0, x1 - x0) * max(0.0, y1 - y0)
    hits = []
    for f in fields:
        if f.field_key not in KEY_FIELDS or f.page_no != page_no or not f.bbox:
            continue
        a0, b0, a1, b1 = f.bbox
        if not (min(x1, a1) > max(x0, a0) and min(y1, b1) > max(y0, b0)):
            continue
        if tight and area > max(0.02, 25 * (a1 - a0) * (b1 - b0)):
            continue
        hits.append(f.field_key)
    return hits


def check_file_hash(stored_bytes: bytes, recorded_sha256: str) -> Check:
    actual = sha256_hex(stored_bytes)
    ok = actual == recorded_sha256
    return Check(
        "file_hash", "file_integrity", "pass" if ok else "fail", "File integrity (SHA-256)",
        ("The stored file is byte-identical to the file uploaded (SHA-256 verified)." if ok
         else "The stored file no longer matches the hash recorded at upload. The evidence copy may be corrupted."),
        {"recorded_sha256": recorded_sha256, "computed_sha256": actual, "significance": "none" if ok else "high"},
    )


def check_active_content(active: list[dict]) -> Check:
    if not active:
        return Check("active_content", "content_security", "pass", "Active content",
                     "No scripts, launch actions, embedded files or dynamic forms were found.")
    return Check("active_content", "content_security", "warning", "Active content present (not executed)",
                 "The PDF contains active or embedded content: " + "; ".join(a["description"] for a in active)
                 + ". The platform never executes it. Unusual for issued certificates; review the source of the file.",
                 {"items": active, "significance": "low"})


# ---------------------------------------------------------------- signatures

def _load_trust_roots() -> list:
    """Load trust anchors from PDF_TRUST_ROOTS (files or folders; PEM, DER or bare base64)."""
    import base64

    from asn1crypto import pem, x509

    from ..config import BACKEND_DIR

    files: list[Path] = []
    for entry in get_settings().trust_root_paths:
        p = Path(entry)
        if not p.is_absolute():
            p = (BACKEND_DIR / p).resolve()
        if p.is_dir():
            files += sorted(f for f in p.iterdir() if f.suffix.lower() in {".pem", ".cer", ".crt", ".der"})
        elif p.exists():
            files.append(p)
    roots = []
    for f in files:
        try:
            raw = f.read_bytes()
            if pem.detect(raw):
                for _t, _h, der in pem.unarmor(raw, multiple=True):
                    roots.append(x509.Certificate.load(der))
            elif raw[:1] == b"0":
                roots.append(x509.Certificate.load(raw))
            else:
                roots.append(x509.Certificate.load(base64.b64decode(b"".join(raw.split()))))
        except Exception as exc:  # noqa: BLE001
            log.warning("could not load trust root %s: %s", f.name, type(exc).__name__)
    return roots


def check_signatures(data: bytes, encrypted: bool) -> list[Check]:
    if encrypted:
        return [Check("digital_signature", "signature", "unable", "Digital signatures",
                      "The PDF is encrypted; signature validation was not performed.")]
    try:
        from pyhanko.pdf_utils.reader import PdfFileReader
        from pyhanko.sign.validation import validate_pdf_signature
        from pyhanko_certvalidator import ValidationContext
    except Exception as exc:  # noqa: BLE001
        return [Check("digital_signature", "signature", "error", "Digital signatures", f"Signature library unavailable: {exc}")]
    try:
        reader = PdfFileReader(io.BytesIO(data), strict=False)
        sigs = list(reader.embedded_signatures)
    except Exception as exc:  # noqa: BLE001
        return [Check("digital_signature", "signature", "error", "Digital signatures",
                      f"The signature structure could not be read ({type(exc).__name__}).")]
    if not sigs:
        return [Check("digital_signature", "signature", "info", "Digital signatures",
                      "No embedded digital signature. Many genuine documents are unsigned (e.g. scans, or letters stated to be "
                      "computer generated); absence alone is not an indicator of tampering.",
                      {"signature_count": 0})]
    roots = _load_trust_roots()
    out: list[Check] = []
    for idx, sig in enumerate(sigs, start=1):
        try:
            vc = ValidationContext(trust_roots=roots, allow_fetching=False) if roots else ValidationContext(allow_fetching=False)
            st = validate_pdf_signature(sig, signer_validation_context=vc)
            signer = st.signing_cert.subject.human_friendly if st.signing_cert is not None else "unknown"
            issuer = st.signing_cert.issuer.human_friendly if st.signing_cert is not None else "unknown"
            coverage = getattr(st.coverage, "name", str(st.coverage))
            mod_level = getattr(getattr(st, "modification_level", None), "name", None)
            details = {
                "field_name": sig.field_name,
                "intact": bool(st.intact),
                "cryptographically_valid": bool(st.valid),
                "trusted": bool(st.trusted),
                "trust_roots_configured": len(roots),
                "signer_subject": signer,
                "signer_certificate_issuer": issuer,
                "signer_reported_time": st.signer_reported_dt.isoformat() if st.signer_reported_dt else None,
                "coverage": coverage,
                "modification_level": mod_level,
                "docmdp_ok": getattr(st, "docmdp_ok", None),
                "md_algorithm": getattr(st, "md_algorithm", None),
            }
            if not st.intact or not st.valid:
                status, sig_level = "fail", "high"
                summary = "The signature is cryptographically invalid: the signed content was altered after signing or the signature is corrupt."
            elif coverage != "ENTIRE_FILE" and mod_level not in (None, "NONE", "LTA_UPDATES"):
                status, sig_level = "warning", "high"
                summary = (f"The signature is intact for the revision it covers, but the file was changed after signing "
                           f"(modification level: {mod_level}). Content added later is not covered by the signature.")
            elif not st.trusted:
                status, sig_level = "warning", "low"
                summary = ("The signature is cryptographically intact, but the signer certificate could not be validated "
                           "against a trusted root" + (" (no trust anchors are configured — set PDF_TRUST_ROOTS)." if not roots else "."))
            else:
                status, sig_level = "pass", "none"
                summary = "The signature is intact, cryptographically valid and chains to a configured trust root."
            details["significance"] = sig_level
            out.append(Check("digital_signature", "signature", status, f"Digital signature {idx}", summary, details))
            out.append(Check("signer_identity", "issuer_identity", "info", f"Signer identity (signature {idx})",
                             f"Certificate subject: {signer}. Issued by: {issuer}. This identifies the certificate holder; "
                             "it is not confirmation from the issuing authority's records.",
                             {"signer_subject": signer, "signer_certificate_issuer": issuer}))
        except Exception as exc:  # noqa: BLE001
            out.append(Check("digital_signature", "signature", "error", f"Digital signature {idx}",
                             f"Validation could not be completed ({type(exc).__name__}: {str(exc)[:200]}).",
                             {"field_name": getattr(sig, "field_name", None)}))
    return out


# ---------------------------------------------------------------- PDF structure & metadata

def check_revisions(data: bytes) -> Check:
    eofs = len(re.findall(rb"%%EOF", data))
    linearized = b"/Linearized" in data[:2048]
    updates = max(0, eofs - 1 - (1 if linearized else 0))
    if updates == 0:
        return Check("revision_history", "tampering", "pass", "Revision history",
                     "The file has a single revision (no incremental updates after it was first written).",
                     {"incremental_updates": 0, "linearized": linearized})
    return Check("revision_history", "tampering", "info", "Revision history",
                 f"The file contains {updates} incremental update(s) appended after it was first written. This is normal for "
                 "signed, form-filled or annotated PDFs, but means content may have been added after the original version.",
                 {"incremental_updates": updates, "linearized": linearized, "significance": "low"})


def _pdf_date(s: str | None) -> datetime | None:
    if not s:
        return None
    m = re.match(r"D?:?(\d{4})(\d{2})?(\d{2})?(\d{2})?(\d{2})?(\d{2})?", s)
    if not m:
        return None
    parts = [int(p) if p else d for p, d in zip(m.groups(), (0, 1, 1, 0, 0, 0))]
    try:
        return datetime(*parts)
    except ValueError:
        return None


def check_metadata(meta: dict) -> Check:
    producer = (meta.get("producer") or "").strip()
    creator = (meta.get("creator") or "").strip()
    created, modified = _pdf_date(meta.get("creationDate")), _pdf_date(meta.get("modDate"))
    details = {"producer": producer or None, "creator": creator or None,
               "created": created.isoformat() if created else None, "modified": modified.isoformat() if modified else None}
    notes, level = [], "none"
    tools = f"{producer} {creator}".lower()
    if any(e in tools for e in IMAGE_EDITORS):
        notes.append(f"Metadata names image-editing software ({producer or creator}).")
        level = "medium"
    elif any(e in tools for e in PDF_EDITORS):
        notes.append(f"Metadata names a PDF editing/conversion tool ({producer or creator}); commonly used for legitimate compression or merging.")
        level = "low"
    if created and modified and modified < created:
        notes.append("Modification date precedes the creation date.")
        level = "medium" if level != "medium" else level
    elif created and modified and (modified - created).days > 30:
        notes.append(f"Modified {(modified - created).days} days after creation.")
        level = level if level != "none" else "low"
    if not meta:
        return Check("metadata", "tampering", "info", "Document metadata",
                     "No embedded metadata. Absence of metadata is common and is not an indicator by itself.", details)
    if not notes:
        return Check("metadata", "tampering", "pass", "Document metadata",
                     "Embedded metadata shows no unusual producer or date pattern.", details)
    details["significance"] = level
    return Check("metadata", "tampering", "warning" if level == "medium" else "info", "Document metadata",
                 " ".join(notes) + " Metadata can be edited and is supporting context only.", details)


def check_hidden_text(data: bytes) -> Check:
    import pymupdf

    doc = pymupdf.open(stream=data, filetype="pdf")
    findings, scan_layers = [], 0
    try:
        for page in doc:
            W, H = page.rect.width or 1, page.rect.height or 1
            try:
                traces = page.get_texttrace()
            except Exception:  # noqa: BLE001
                continue
            invisible = [t for t in traces if t.get("type") == 3 or float(t.get("opacity", 1)) == 0.0]
            if not invisible:
                continue
            img_cover = 0.0
            for info in page.get_image_info():
                x0, y0, x1, y1 = info["bbox"]
                img_cover = max(img_cover, max(0, x1 - x0) * max(0, y1 - y0) / (W * H))
            if img_cover >= 0.8:
                scan_layers += 1  # searchable scan: invisible OCR layer over a page image (expected)
                continue
            for t in invisible[:20]:
                text = "".join(chr(c[0]) for c in t.get("chars", []) if c and c[0] > 31)
                x0, y0, x1, y1 = t["bbox"]
                findings.append({"page_no": page.number + 1, "text": text[:120],
                                 "bbox": [round(x0 / W, 5), round(y0 / H, 5), round(x1 / W, 5), round(y1 / H, 5)]})
    finally:
        doc.close()
    if findings:
        return Check("hidden_text", "tampering", "warning", "Hidden text",
                     f"{len(findings)} invisible text span(s) found on pages that are not scanned images. Hidden text can make "
                     "the searchable content differ from what a reader sees.",
                     {"spans": findings, "significance": "medium"},
                     [{"type": "page", "page_no": f["page_no"], "bbox": f["bbox"]} for f in findings[:10]])
    if scan_layers:
        return Check("hidden_text", "tampering", "pass", "Hidden text",
                     f"{scan_layers} page(s) carry an invisible text layer over a full-page image, which is how searchable scans "
                     "are produced. The layer is compared with the visible image in the rendered-content check.",
                     {"scan_text_layer_pages": scan_layers})
    return Check("hidden_text", "tampering", "pass", "Hidden text", "No invisible text was found.")


def check_font_consistency(data: bytes, fields=None) -> Check:
    import pymupdf

    doc = pymupdf.open(stream=data, filetype="pdf")
    usage: dict[str, int] = {}
    lines_spans: list[tuple[int, list[dict], float, float]] = []
    scanned = 0
    try:
        for page in doc:
            W, H = page.rect.width or 1, page.rect.height or 1
            if any(r.width * r.height / (W * H) >= 0.6 for img in page.get_images(full=True) for r in page.get_image_rects(img[0])):
                scanned += 1
                continue  # a scanned page's text is an OCR layer: its fonts say nothing about the printed document
            for block in page.get_text("dict").get("blocks", []):
                if block.get("type") != 0:
                    continue
                for line in block.get("lines", []):
                    spans = [s for s in line.get("spans", []) if s.get("text", "").strip()]
                    for s in spans:
                        usage[s["font"]] = usage.get(s["font"], 0) + len(s["text"].strip())
                    if len(spans) >= 2:
                        lines_spans.append((page.number + 1, spans, W, H))
    finally:
        doc.close()
    total = sum(usage.values()) or 1

    def family(name: str) -> str:
        return re.split(r"[-,+]", name.split("+")[-1])[0].lower()

    flags = []
    for page_no, spans, W, H in lines_spans:
        for s in spans:
            t = s["text"].strip()
            digits = sum(ch.isdigit() for ch in t)
            if len(t) < 2 or digits / len(t) < 0.6:
                continue
            others = [o for o in spans if o is not s]
            other_fams = {family(o["font"]) for o in others}
            sizes = [o["size"] for o in others]
            rare = usage.get(s["font"], 0) / total < 0.03
            # Only a different, rarely used font counts; size differences are normal in tables, footnotes and headings.
            if family(s["font"]) not in other_fams and rare:
                x0, y0, x1, y1 = s["bbox"]
                box = [round(x0 / W, 5), round(y0 / H, 5), round(x1 / W, 5), round(y1 / H, 5)]
                flags.append({"page_no": page_no, "text": t[:60], "font": s["font"], "size": round(s["size"], 1),
                              "line_fonts": sorted({o["font"] for o in others}), "bbox": box, "key_values": _key_hits(page_no, box, fields)})
    if not lines_spans and not usage:
        return Check("font_consistency", "tampering", "not_applicable", "Font consistency",
                     "No printed text layer to analyse" + (" (scanned pages carry only an OCR layer)" if scanned else "") + "; see image analysis instead.")
    key = [f for f in flags if f["key_values"]]
    if not flags:
        return Check("font_consistency", "tampering", "pass", "Font consistency",
                     "Numbers in the text use the same fonts as the surrounding text.", {"fonts_used": len(usage), "scanned_pages_skipped": scanned})
    if not key:
        return Check("font_consistency", "tampering", "info", "Font consistency",
                     f"{len(flags)} number(s) use a different font from their line, but none of them is an ID, date, reference number or intake. "
                     "This is common in templates and is not treated as suspicious.",
                     {"flags": flags[:25], "significance": "low", "scanned_pages_skipped": scanned})
    names = sorted({FIELD_LABEL.get(k, k.replace("_", " ")) for f in key for k in f["key_values"]})
    return Check("font_consistency", "tampering", "warning", "Font consistency",
                 f"Key value(s) printed in a different, rarely used font from the rest of their line: {', '.join(names)} "
                 f"({', '.join(repr(f['text']) for f in key[:4])}). Replaced figures often show this; compare with the issuer's copy.",
                 {"flags": flags[:25], "significance": "medium", "scanned_pages_skipped": scanned},
                 [{"type": "page", "page_no": f["page_no"], "bbox": f["bbox"], "note": f["text"]} for f in key[:10]])


# ---------------------------------------------------------------- rendered vs embedded

def check_render_vs_text(data: bytes, ex: ExtractionResult, fields: list[Candidate], password: str | None) -> Check:
    import pymupdf

    embedded_pages = [p for p in ex.pages if p.text_source == "embedded"]
    if not embedded_pages:
        return Check("render_text_match", "tampering", "not_applicable", "Rendered content vs embedded text",
                     "The document has no embedded text layer to compare.")
    s = get_settings()
    doc = pymupdf.open(stream=data, filetype="pdf")
    if doc.needs_pass and password:
        doc.authenticate(password)
    issues, page_stats = [], []
    field_pages = {f.page_no for f in fields if f.page_no}
    target = [p for p in embedded_pages if p.page_no <= 1 or p.page_no in field_pages][:2]
    try:
        for p in target:
            page = doc.load_page(p.page_no - 1)
            scale = min(s.ocr_render_dpi, 140) / 72.0
            pix = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False, colorspace=pymupdf.csRGB)
            img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 3)
            h, w = img.shape[:2]
            ocr_lines = run_ocr(np.ascontiguousarray(img))
            ocr_text = " ".join(ln.text for ln in ocr_lines)
            emb_text = " ".join(ln.text for ln in p.lines)
            e_words = {w_ for w_ in word_tokens(emb_text) if len(w_) >= 3}
            o_words = set(word_tokens(ocr_text))
            recall = len(e_words & o_words) / len(e_words) if e_words else 1.0
            page_stats.append({"page_no": p.page_no, "embedded_words": len(e_words), "recall": round(recall, 3)})
            if e_words and len(e_words) >= 15 and recall < 0.5:
                issues.append({"page_no": p.page_no, "kind": "page_text_mismatch", "recall": round(recall, 3),
                               "missing_sample": sorted(e_words - o_words)[:15]})
            # Field-level check: compare each numeric field with the OCR lines visible at the same location.
            norm_ocr = [([ln.bbox[0] / w, ln.bbox[1] / h, ln.bbox[2] / w, ln.bbox[3] / h], ln) for ln in ocr_lines]
            for f in fields:
                if f.page_no != p.page_no or not f.bbox or not re.search(r"\d", f.value or ""):
                    continue
                fx0, fy0, fx1, fy1 = f.bbox
                cy = (fy0 + fy1) / 2
                overlapping = [ln for (bx0, by0, bx1, by1), ln in norm_ocr
                               if by0 <= cy <= by1 and bx0 < fx1 and bx1 > fx0]
                if not overlapping:
                    continue
                seen = " ".join(ln.text for ln in overlapping)
                conf = min(ln.confidence for ln in overlapping)
                exp_digits = "".join(re.findall(r"\d", f.value))
                seen_digits = "".join(re.findall(r"\d", seen))
                if conf >= 0.85 and exp_digits and seen_digits and exp_digits not in seen_digits and seen_digits not in exp_digits:
                    issues.append({"page_no": p.page_no, "kind": "field_value_mismatch", "field_key": f.field_key,
                                   "embedded_value": f.value, "visible_value": clean_text(seen), "ocr_confidence": round(conf, 3),
                                   "bbox": f.bbox})
    finally:
        doc.close()
    if not issues:
        return Check("render_text_match", "tampering", "pass", "Rendered content vs embedded text",
                     f"The visible rendering agrees with the embedded text on {len(target)} checked page(s), including the extracted numeric fields.",
                     {"pages": page_stats})
    high = any(i["kind"] == "field_value_mismatch" for i in issues)
    return Check(
        "render_text_match", "tampering", "warning", "Rendered content vs embedded text",
        ("The value a reader sees differs from the embedded text for: "
         + ", ".join(f"{i['field_key']} (embedded '{i['embedded_value']}', visible '{i['visible_value']}')" for i in issues if i["kind"] == "field_value_mismatch")
         + ". This pattern occurs when visible content is overlaid on the original text." if high else
         "A large share of the embedded text is not visible in the rendered page, which can indicate covered or hidden content."),
        {"issues": issues, "pages": page_stats, "significance": "high" if high else "medium",
         "method": "Each page is rendered and read with OCR; embedded words and numeric field values are compared with what is visible."},
        [{"type": "page", "page_no": i["page_no"], "bbox": i.get("bbox"), "note": i["kind"]} for i in issues])


# ---------------------------------------------------------------- image forensics

def _ela(img_rgb: np.ndarray, block: int = 16) -> tuple[list[dict], np.ndarray, dict]:
    pil = Image.fromarray(img_rgb)
    buf = io.BytesIO()
    pil.save(buf, format="JPEG", quality=90)
    resaved = np.asarray(Image.open(io.BytesIO(buf.getvalue())).convert("RGB")).astype(np.int16)
    diff = np.abs(img_rgb.astype(np.int16) - resaved).max(axis=2).astype(np.float32)
    gray = cv2.cvtColor(img_rgb, cv2.COLOR_RGB2GRAY)
    grad = cv2.magnitude(cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3), cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3))
    h, w = diff.shape
    bh, bw = h // block, w // block
    if bh < 4 or bw < 4:
        return [], diff, {}
    d = diff[: bh * block, : bw * block].reshape(bh, block, bw, block).mean(axis=(1, 3))
    g = grad[: bh * block, : bw * block].reshape(bh, block, bw, block).mean(axis=(1, 3))
    ratio = d / (g / 8.0 + 2.0)
    med = float(np.median(ratio))
    mad = float(np.median(np.abs(ratio - med))) * 1.4826 + 1e-6
    z = (ratio - med) / mad
    mask = ((z > 6) & (d > 2.0)).astype(np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    n, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    regions = []
    total_blocks = bh * bw
    for i in range(1, n):
        x, y, ww, hh, area = stats[i]
        if area < 6 or area > 0.25 * total_blocks:
            continue
        zmax = float(z[labels == i].max())
        regions.append({"bbox": [round(x / bw, 4), round(y / bh, 4), round((x + ww) / bw, 4), round((y + hh) / bh, 4)],
                        "blocks": int(area), "max_score": round(zmax, 1)})
    regions.sort(key=lambda r: -r["max_score"])
    stats_out = {"median_ratio": round(med, 3), "blocks": total_blocks}
    norm = np.clip(ratio / (med * 6 + 1e-6), 0, 1)
    return regions[:8], (norm * 255).astype(np.uint8), stats_out


def _heatmap(img_rgb: np.ndarray, block_map: np.ndarray, regions: list[dict]) -> np.ndarray:
    h, w = img_rgb.shape[:2]
    heat = cv2.resize(block_map, (w, h), interpolation=cv2.INTER_NEAREST)
    colored = cv2.cvtColor(cv2.applyColorMap(heat, cv2.COLORMAP_INFERNO), cv2.COLOR_BGR2RGB)
    out = cv2.addWeighted(img_rgb, 0.55, colored, 0.45, 0)
    for r in regions:
        x0, y0, x1, y1 = r["bbox"]
        cv2.rectangle(out, (int(x0 * w), int(y0 * h)), (int(x1 * w), int(y1 * h)), (0, 200, 255), max(2, w // 400))
    return out


def _copy_move(img_rgb: np.ndarray) -> list[dict]:
    gray = cv2.cvtColor(img_rgb, cv2.COLOR_RGB2GRAY)
    h, w = gray.shape
    scale = min(1.0, 1600 / max(h, w))
    if scale < 1.0:
        gray = cv2.resize(gray, (int(w * scale), int(h * scale)))
    orb = cv2.ORB_create(nfeatures=4000)
    kps, des = orb.detectAndCompute(gray, None)
    if des is None or len(kps) < 50:
        return []
    matcher = cv2.BFMatcher(cv2.NORM_HAMMING)
    bins: dict[tuple[int, int], list[tuple]] = {}
    for ms in matcher.knnMatch(des, des, k=3):
        for m in ms[1:]:
            if m.distance > 24:
                continue
            p1, p2 = np.array(kps[m.queryIdx].pt), np.array(kps[m.trainIdx].pt)
            dx, dy = p2 - p1
            if dx < 0 or (dx == 0 and dy < 0):
                dx, dy, p1, p2 = -dx, -dy, p2, p1
            if np.hypot(dx, dy) < 40:
                continue
            bins.setdefault((int(dx // 12), int(dy // 12)), []).append((p1, p2))
    gh, gw = gray.shape
    regions = []
    for (bx, by), pairs in sorted(bins.items(), key=lambda kv: -len(kv[1])):
        unique = {tuple(np.round(p[0]).astype(int)) for p in pairs}
        if len(unique) < 30:
            break
        src = np.array([p[0] for p in pairs])
        dst = np.array([p[1] for p in pairs])
        sx0, sy0 = src.min(axis=0)
        sx1, sy1 = src.max(axis=0)
        area = (sx1 - sx0) * (sy1 - sy0) / (gw * gh)
        if area > 0.15 or area < 0.0005:
            continue
        dx0, dy0 = dst.min(axis=0)
        dx1, dy1 = dst.max(axis=0)
        regions.append({"matches": len(unique), "offset_px": [int(bx * 12 / scale), int(by * 12 / scale)],
                        "source_bbox": [round(sx0 / gw, 4), round(sy0 / gh, 4), round(sx1 / gw, 4), round(sy1 / gh, 4)],
                        "target_bbox": [round(dx0 / gw, 4), round(dy0 / gh, 4), round(dx1 / gw, 4), round(dy1 / gh, 4)]})
        if len(regions) >= 3:
            break
    return regions


def _page_images_for_forensics(data: bytes, detected_type: str, ex: ExtractionResult) -> list[tuple[int, np.ndarray, bool]]:
    """Return (page_no, rgb_array, is_jpeg_source) for scanned/photographic pages (max 10)."""
    out = []
    if detected_type != "pdf":
        for p in ex.pages[:10]:
            if p.analysis_image is not None:
                out.append((p.page_no, p.analysis_image, detected_type == "jpeg"))
        return out
    import pymupdf

    doc = pymupdf.open(stream=data, filetype="pdf")
    try:
        for page in doc:
            if len(out) >= 10:
                break
            W, H = page.rect.width or 1, page.rect.height or 1
            for img in page.get_images(full=True):
                xref = img[0]
                rects = page.get_image_rects(xref)
                cover = max(((r.width * r.height) / (W * H) for r in rects), default=0)
                if cover < 0.6:
                    continue
                info = doc.extract_image(xref)
                if not info or not info.get("image"):
                    continue
                try:
                    arr = np.asarray(Image.open(io.BytesIO(info["image"])).convert("RGB"))
                except Exception:  # noqa: BLE001
                    continue
                if max(arr.shape[:2]) > 4200:
                    r = 4200 / max(arr.shape[:2])
                    arr = cv2.resize(arr, (int(arr.shape[1] * r), int(arr.shape[0] * r)))
                out.append((page.number + 1, arr, info.get("ext") in {"jpeg", "jpg", "jpx"}))
                break
    finally:
        doc.close()
    return out


def check_image_forensics(data: bytes, detected_type: str, ex: ExtractionResult, fields=None) -> list[Check]:
    pages = _page_images_for_forensics(data, detected_type, ex)
    if not pages:
        return [Check("image_forensics", "tampering", "not_applicable", "Image region analysis",
                      "The document has no scanned or photographic page images to analyse.")]
    ela_flags, cm_flags, artifacts, analysed_jpeg = [], [], [], 0
    for page_no, arr, is_jpeg in pages:
        if is_jpeg:
            analysed_jpeg += 1
            regions, block_map, stats = _ela(arr)
            if regions:
                png, w, h = _png_bytes(_heatmap(arr, block_map, regions))
                artifacts.append(Artifact("ela_heatmap", page_no, png, w, h, {"regions": regions, **stats}))
                ela_flags.extend({"page_no": page_no, **r} for r in regions)
        for r in _copy_move(arr):
            cm_flags.append({"page_no": page_no, **r})
    checks: list[Check] = []
    if analysed_jpeg == 0:
        checks.append(Check("compression_analysis", "tampering", "not_applicable", "Compression error-level analysis",
                            "Page images are not JPEG-compressed, so error-level analysis does not apply."))
    elif ela_flags:
        for f in ela_flags:
            f["key_values"] = _key_hits(f["page_no"], f["bbox"], fields, tight=True)
        key = [f for f in ela_flags if f["key_values"]]
        method = "JPEG re-compression at quality 90; per-block error normalised by local edge strength; robust z-score > 6."
        if key:
            names = sorted({FIELD_LABEL.get(k, k.replace("_", " ")) for f in key for k in f["key_values"]})
            checks.append(Check(
                "compression_analysis", "tampering", "warning", "Compression error-level analysis",
                f"The area around {', '.join(names)} compresses differently from the rest of the page, as pasted or re-typed content does. "
                "Compare these values with the original document.",
                {"regions": ela_flags, "significance": "medium", "method": method},
                [{"type": "page", "page_no": f["page_no"], "bbox": f["bbox"], "note": "compression anomaly"} for f in key],
                artifacts))
        else:
            checks.append(Check(
                "compression_analysis", "tampering", "info", "Compression error-level analysis",
                f"{len(ela_flags)} area(s) compress differently from the page (photographs, stamps, signatures and highlighter do this), "
                "but none covers an ID, date, reference number or intake, so it is not treated as suspicious.",
                {"regions": ela_flags, "significance": "low", "method": method}, [], artifacts))
    else:
        checks.append(Check("compression_analysis", "tampering", "pass", "Compression error-level analysis",
                            f"No regions with anomalous compression error levels on {analysed_jpeg} analysed page image(s)."))
    if cm_flags:
        strong = any(f["matches"] >= 60 for f in cm_flags)
        checks.append(Check(
            "duplicated_regions", "tampering", "warning" if strong else "info", "Duplicated regions",
            f"{len(cm_flags)} pair(s) of image regions share many identical features at a constant offset, which can indicate a "
            "copied element (e.g. a stamp or signature). Repeated printed elements can also cause this.",
            {"regions": cm_flags, "significance": "medium" if strong else "low",
             "method": "ORB keypoint self-matching clustered by displacement vector."},
            [{"type": "page", "page_no": f["page_no"], "bbox": f["source_bbox"], "note": "source"} for f in cm_flags]
            + [{"type": "page", "page_no": f["page_no"], "bbox": f["target_bbox"], "note": "duplicate"} for f in cm_flags]))
    else:
        checks.append(Check("duplicated_regions", "tampering", "pass", "Duplicated regions",
                            f"No duplicated image regions detected on {len(pages)} page image(s)."))
    return checks


MARK_LABEL = {"stamp": "Stamp / seal", "signature": "Signature", "photo": "Photograph", "emblem": "Logo / emblem", "ink": "Ink mark"}


def _photo_regions(arr: np.ndarray, text_mask: np.ndarray, block: int = 24) -> list[tuple[int, int, int, int]]:
    """Photographs on a page: areas made mostly of mid-tones or saturated colour (paper and print are mostly white)."""
    H, W = arr.shape[:2]
    gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
    sat = cv2.cvtColor(arr, cv2.COLOR_RGB2HSV)[..., 1]
    busy = (((gray > 40) & (gray < 200)) | (sat > 60)).astype(np.float32)
    bh, bw = H // block, W // block
    if bh < 2 or bw < 2:
        return []
    frac = busy[: bh * block, : bw * block].reshape(bh, block, bw, block).mean(axis=(1, 3))
    m = cv2.morphologyEx((frac > 0.5).astype(np.uint8), cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    n, _lab, st, _ = cv2.connectedComponentsWithStats(m, connectivity=8)
    out = []
    for i in range(1, n):
        x, y, w, h, area = st[i]
        X, Y, Wd, Ht = x * block, y * block, w * block, h * block
        if area < 20 or Wd * Ht < 0.015 * W * H or area / (w * h) <= 0.5:
            continue
        if text_mask[Y:Y + Ht, X:X + Wd].mean() > 0.35:
            continue  # shaded table rows or large printed headings, not a photograph
        q = (arr[Y:Y + Ht:2, X:X + Wd:2] // 32).reshape(-1, 3).astype(np.int32)
        counts = np.bincount(q[:, 0] * 64 + q[:, 1] * 8 + q[:, 2], minlength=512)
        if int((counts > 0.002 * len(q)).sum()) < 25:
            continue  # few distinct colours: a flat fill (shaded header, banner), not a photograph
        out.append((X, Y, Wd, Ht))
    return out


def _stroke_shape(strokes: np.ndarray) -> tuple[float, int, float]:
    """(longest stroke width relative to the region, number of strokes, variation of stroke heights) of a binary region.

    Printed text is a row of letters of even height; handwriting varies in height and stamps mix a ring with small letters.
    """
    n, _lab, st, _ = cv2.connectedComponentsWithStats(strokes.astype(np.uint8), connectivity=8)
    big = [st[i] for i in range(1, n) if st[i][4] >= 6]
    if not big:
        return 0.0, 0, 0.0
    heights = np.array([c[3] for c in big], dtype=np.float32)
    return max(c[2] for c in big) / max(strokes.shape[1], 1), len(big), float(heights.std() / max(heights.mean(), 1.0))


def _detect_marks(arr: np.ndarray, text_boxes: list[list[float]], digital: bool) -> list[dict]:
    """Locate stamps/seals, signatures, photographs and logos on a page image (RGB).

    Coloured ink (any hue from green to violet, or red) and dark pen strokes count only where they contain long connected
    strokes (a signature, a stamp ring) rather than rows of separate printed letters, and never inside photographs.
    """
    H, W = arr.shape[:2]
    hsv = cv2.cvtColor(arr, cv2.COLOR_RGB2HSV)
    hue, sat, val = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    coloured = (((hue >= 35) & (hue <= 165)) | (hue <= 8) | (hue >= 170)) & (sat > 60) & (val > 40)
    text_mask = np.zeros((H, W), bool)
    for x0, y0, x1, y1 in text_boxes:
        text_mask[max(0, int(y0 * H)):int(y1 * H) + 1, max(0, int(x0 * W)):int(x1 * W) + 1] = True
    gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
    photos = _photo_regions(arr, text_mask)
    in_photo = np.zeros((H, W), bool)
    for x, y, w, h in photos:
        in_photo[y:y + h, x:x + w] = True
    out: list[dict] = []

    def overlaps(kind, x, y, w, h):
        for o in out:
            if o["kind"] == kind:
                a0, b0, a1, b1 = o["bbox"][0] * W, o["bbox"][1] * H, o["bbox"][2] * W, o["bbox"][3] * H
                if not (a1 < x or a0 > x + w or b1 < y or b0 > y + h):
                    return True
        return False

    def add(kind, x, y, w, h, colour=None):
        if kind != "photo" and in_photo[y:y + h, x:x + w].mean() > 0.3:
            return  # objects inside a photograph are not stamps or signatures
        if kind in ("signature", "stamp") and (overlaps("signature", x, y, w, h) or overlaps("stamp", x, y, w, h)):
            return
        out.append({"kind": kind, "bbox": [round(x / W, 4), round(y / H, 4), round((x + w) / W, 4), round((y + h) / H, 4)], "colour": colour})

    # 1) coloured ink: merge nearby strokes so one signature or stamp is one region
    k = max(7, W // 70)
    joined = cv2.morphologyEx((coloured * 255).astype(np.uint8), cv2.MORPH_CLOSE, np.ones((k, k), np.uint8))
    joined = cv2.dilate(joined, np.ones((max(3, H // 200), max(9, W // 35)), np.uint8))  # letters of one signature side by side
    n, _lab, st, _ = cv2.connectedComponentsWithStats(joined, connectivity=8)
    for i in range(1, n):
        x, y, w, h, _area = st[i]
        if w * h < 0.0012 * W * H or w * h > 0.25 * W * H:
            continue
        ink = coloured[y:y + h, x:x + w]
        fill = float(ink.mean())
        longest, pieces, hvar = _stroke_shape(ink)
        ar = w / max(h, 1)
        cy, cx = np.argwhere(ink)[len(np.argwhere(ink)) // 2] if ink.any() else (h // 2, w // 2)
        hh = int(hue[y + cy, x + cx])
        colour = "red" if hh <= 8 or hh >= 170 else ("green" if hh < 80 else ("cyan" if hh < 100 else "blue/violet"))
        if fill > 0.55:
            continue  # solid coloured block: highlight or filled graphic
        if pieces >= 4 and hvar < 0.4:
            continue  # letters of even height: coloured printed text
        if pieces <= 6 and text_mask[y:y + h, x:x + w].mean() >= 0.5:
            continue  # a short coloured label the text reader already read
        compact = 0.45 <= ar <= 2.2 and min(w, h) >= 0.04 * W
        if fill >= 0.28 and pieces <= 10:
            add("emblem", x, y, w, h, colour)  # a few heavy shapes: printed logo
        elif compact and (pieces >= 10 or fill >= 0.2):
            add("emblem" if digital and y < 0.15 * H else "stamp", x, y, w, h, colour)  # ring/border with small lettering
        elif 0.05 * W <= w <= 0.6 * W:
            add("signature", x, y, w, h, colour)

    # 2) dark pen strokes (black / blue-black signatures)
    dark = ((gray < 120) & ~coloured).astype(np.uint8) * 255
    thick = cv2.dilate(dark, np.ones((3, 1), np.uint8))  # lets slightly slanted scanned rules count as horizontal runs
    rules = cv2.morphologyEx(thick, cv2.MORPH_OPEN, np.ones((1, max(15, W // 30)), np.uint8)) | \
        cv2.morphologyEx(dark, cv2.MORPH_OPEN, np.ones((max(15, H // 25), 1), np.uint8))
    dark = cv2.bitwise_and(dark, cv2.bitwise_not(cv2.dilate(rules, np.ones((7, 7), np.uint8))))
    merged = cv2.dilate(dark, np.ones((max(3, H // 100), max(5, W // 40)), np.uint8))
    n, _lab, st, _ = cv2.connectedComponentsWithStats(merged, connectivity=8)
    for i in range(1, n):
        x, y, w, h, _area = st[i]
        if not (0.07 * W <= w <= 0.6 * W) or not (0.015 * H <= h <= 0.12 * H) or w / max(h, 1) < 1.3:
            continue
        strokes = dark[y:y + h, x:x + w] > 0
        paper = float((gray[y:y + h, x:x + w] > 190).mean())
        if strokes.mean() > 0.2 or strokes.sum() < 0.0003 * W * H or paper < 0.65:
            continue  # dense (logo, QR, photo) or too faint to be a signature
        longest, pieces, hvar = _stroke_shape(strokes)
        if pieces > 40 or (pieces >= 4 and hvar < 0.45) or (longest < 0.22 and hvar < 0.6):
            continue  # printed words: letters of even height, none long
        if text_mask[y:y + h, x:x + w].mean() > 0.6 and pieces > 12:
            continue  # a printed line the text reader read
        add("signature", x, y, w, h, "dark")
    for x, y, w, h in photos:
        add("photo", x, y, w, h)
    order = {"stamp": 0, "signature": 1, "photo": 2, "emblem": 3, "ink": 4}
    out.sort(key=lambda o: (order[o["kind"]], o["bbox"][1]))
    return [o for kind in order for o in [x for x in out if x["kind"] == kind][:8]]


def check_ink_regions(ex: ExtractionResult) -> Check:
    """Signatures, stamps/seals and logos located on every page, for the officer to look at."""
    found = []
    for p in ex.pages[:30]:
        if p.is_blank:
            continue
        try:
            arr = np.asarray(Image.open(io.BytesIO(p.preview_png)).convert("RGB"))
        except Exception:  # noqa: BLE001
            continue
        boxes = [ln.bbox for ln in p.lines if ln.bbox and len(ln.text.strip()) >= 2]
        for m in _detect_marks(arr, boxes, digital=p.text_source == "embedded"):
            found.append({"page_no": p.page_no, **m})
    count = {k: sum(f["kind"] == k for f in found) for k in MARK_LABEL}
    if not any(count[k] for k in ("stamp", "signature")):
        summary = "No stamp, seal or signature was located on the pages."
        if count["emblem"]:
            summary += f" {count['emblem']} logo/emblem(s) located."
    else:
        pages = sorted({f["page_no"] for f in found if f["kind"] in ("stamp", "signature")})
        summary = (f"Located {count['stamp']} stamp/seal(s) and {count['signature']} signature(s) on page(s) "
                   f"{', '.join(map(str, pages))}. Their presence does not prove authenticity; compare with the issuer's specimen.")
    return Check("ink_regions", "visual_elements", "info", "Signatures and stamps", summary,
                 {"regions": found[:60], "counts": count},
                 [{"type": "page", "page_no": f["page_no"], "bbox": f["bbox"], "kind": f["kind"],
                   "note": MARK_LABEL[f["kind"]] + (f" ({f['colour']} ink)" if f.get("colour") and f["colour"] != "dark" else "")} for f in found[:60]])


# ---------------------------------------------------------------- QR / barcodes

def _decode_codes(arr: np.ndarray) -> list[dict]:
    out = []
    try:
        ok, infos, points, _ = cv2.QRCodeDetector().detectAndDecodeMulti(arr)
        if ok:
            h, w = arr.shape[:2]
            for info, pts in zip(infos, points):
                if info:
                    xs, ys = pts[:, 0], pts[:, 1]
                    out.append({"type": "QR", "content": info[:2000],
                                "bbox": [round(float(xs.min()) / w, 4), round(float(ys.min()) / h, 4), round(float(xs.max()) / w, 4), round(float(ys.max()) / h, 4)]})
    except Exception:  # noqa: BLE001
        pass
    try:
        det = cv2.barcode.BarcodeDetector()
        res = det.detectAndDecodeWithType(arr)
        if res and res[0]:
            infos, types = res[1], res[2]
            for info, typ in zip(infos, types):
                if info:
                    out.append({"type": str(typ) or "barcode", "content": info[:2000], "bbox": None})
    except Exception:  # noqa: BLE001
        pass
    return out


def check_codes(data: bytes, detected_type: str, ex: ExtractionResult, fields: list[Candidate]) -> tuple[Check, list[dict]]:
    import pymupdf

    codes = []
    if detected_type == "pdf":
        doc = pymupdf.open(stream=data, filetype="pdf")
        try:
            for i in range(min(doc.page_count, 5)):
                page = doc.load_page(i)
                pix = page.get_pixmap(matrix=pymupdf.Matrix(200 / 72, 200 / 72), alpha=False, colorspace=pymupdf.csRGB)
                arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 3)
                codes += [{"page_no": i + 1, **c} for c in _decode_codes(np.ascontiguousarray(arr))]
        except Exception:  # noqa: BLE001
            pass
        finally:
            doc.close()
    else:
        for p in ex.pages[:5]:
            if p.analysis_image is not None:
                codes += [{"page_no": p.page_no, **c} for c in _decode_codes(p.analysis_image)]
    if not codes:
        return Check("qr_barcode", "issuer_identity", "info", "QR codes and barcodes",
                     "No QR code or barcode was decoded."), []
    known = {f.field_key: (f.normalized or f.value) for f in fields if f.group_index is None}
    comparisons, level = [], "none"
    for c in codes:
        content = c["content"]
        ids_in_code = set(AICTE_ID_RE.findall(content))
        doc_ids = {v for k, v in known.items() if k in ("aicte_permanent_id", "application_id") and v}
        urls = re.findall(r"https?://[^\s\"'<>]+", content)
        cmp = {"page_no": c["page_no"], "type": c["type"], "content_excerpt": content[:300], "urls": urls[:3]}
        if ids_in_code and doc_ids:
            if ids_in_code & doc_ids:
                cmp["result"] = "Identifiers in the code match identifiers extracted from the document."
            else:
                cmp["result"] = f"The code contains identifier(s) {sorted(ids_in_code)} that differ from the document ({sorted(doc_ids)})."
                level = "high"
        elif any(v and len(str(v)) >= 6 and str(v).lower() in content.lower() for v in known.values()):
            cmp["result"] = "Some extracted values appear in the code content."
        else:
            cmp["result"] = "The code content could not be matched to extracted fields."
        if urls:
            cmp["note"] = "Links are shown for manual checking and are never opened automatically."
        comparisons.append(cmp)
    status = "warning" if level == "high" else "info"
    return Check("qr_barcode", "issuer_identity", status, "QR codes and barcodes",
                 f"{len(codes)} code(s) decoded. " + " ".join(c["result"] for c in comparisons),
                 {"codes": comparisons, "significance": level},
                 [{"type": "page", "page_no": c["page_no"], "bbox": c.get("bbox"), "note": c["type"]} for c in codes]), codes


def issuer_source_check() -> Check:
    return Check("issuer_original_comparison", "issuer_identity", "unable", "Comparison with issuer-held original",
                 "No issuer-hosted original or issuer verification service is configured for this document, so it could not "
                 "be compared with the issuing authority's copy.",
                 {"next_action": "Configure an authorised issuer verification source, or obtain a copy directly from the issuer."})


def run_integrity_checks(*, data: bytes, detected_type: str, sha256: str, encrypted: bool, active_content: list[dict],
                         metadata: dict, ex: ExtractionResult, fields: list[Candidate], password: str | None,
                         progress=None, text_reader=None, defer_ai: bool = False) -> tuple[list[Check], list[dict]]:
    """Run every integrity check. With ``defer_ai`` the slow page re-reading is left to a follow-up job (see run_ai_checks)."""
    checks: list[Check] = [check_file_hash(data, sha256)]
    codes: list[dict] = []

    def safe(fn, *args):
        try:
            res = fn(*args)
            return res if isinstance(res, list) else [res]
        except Exception as exc:  # noqa: BLE001 - one failing check must not hide the others
            log.warning("integrity check %s failed: %s", fn.__name__, type(exc).__name__)
            return [Check(fn.__name__.removeprefix("check_"), "tampering", "error", fn.__name__.removeprefix("check_").replace("_", " ").title(),
                          f"This check could not be completed ({type(exc).__name__}).")]

    if detected_type == "pdf":
        checks.append(check_active_content(active_content))
        checks += safe(check_signatures, data, encrypted)
        checks += safe(check_revisions, data)
        checks += safe(check_metadata, metadata)
        checks += safe(check_hidden_text, data)
        checks += safe(check_font_consistency, data, fields)
    if progress:
        progress(0.72, "Analysing page images")
    ai_future = None
    if text_reader is not None and not defer_ai:
        from concurrent.futures import ThreadPoolExecutor

        ai_pool = ThreadPoolExecutor(max_workers=1)  # runs alongside the local image checks below
        ai_future = ai_pool.submit(check_ai_reading, data, detected_type, ex, fields, password, text_reader)
        ai_pool.shutdown(wait=False)
    checks += safe(check_image_forensics, data, detected_type, ex, fields)
    checks += safe(check_ink_regions, ex)
    try:
        qr_check, codes = check_codes(data, detected_type, ex, fields)
        checks.append(qr_check)
    except Exception as exc:  # noqa: BLE001
        checks.append(Check("qr_barcode", "issuer_identity", "error", "QR codes and barcodes", f"Decoding failed ({type(exc).__name__})."))
    ai_done = False
    if ai_future is not None:
        try:
            checks += ai_future.result()
            ai_done = True
        except Exception as exc:  # noqa: BLE001 - model unavailable: fall back to local re-reading
            log.warning("AI reading cross-check unavailable: %s: %s", type(exc).__name__, str(exc)[:200])
    if defer_ai:
        checks.append(Check("ai_reading", "tampering", "info", "AI reading cross-check",
                            "AI analysis of the pages is running; its results appear here automatically.", {"pending": True}))
    elif detected_type == "pdf" and not ai_done:
        checks += safe(check_render_vs_text, data, ex, fields, password)
    return checks, codes


def run_ai_checks(*, data: bytes, detected_type: str, ex: ExtractionResult, fields: list[Candidate], password: str | None,
                  text_reader=None) -> list[Check]:
    """The page re-reading checks: the AI reader (with visual analysis) when configured, otherwise the local re-reading."""
    if text_reader is not None:
        try:
            return check_ai_reading(data, detected_type, ex, fields, password, text_reader)
        except Exception as exc:  # noqa: BLE001 - model unavailable: fall back to local re-reading
            log.warning("AI reading cross-check unavailable: %s: %s", type(exc).__name__, str(exc)[:200])
    if detected_type == "pdf":
        try:
            return [check_render_vs_text(data, ex, fields, password)]
        except Exception as exc:  # noqa: BLE001
            return [Check("render_text_match", "tampering", "error", "Rendered text vs embedded text",
                          f"This check could not be completed ({type(exc).__name__}).")]
    return [Check("ai_reading", "tampering", "not_applicable", "AI reading cross-check",
                  "No AI reader is configured; the image was read by OCR only.")]


# ---------------------------------------------------------------- AI reading cross-check (fast path)

AI_MAX_PAGES = 6  # pages sent to the AI reader, in parallel
AI_DPI = 130
AI_CHECK_FIELDS = {"aicte_permanent_id", "application_id", "reference_number", "certificate_number", "issue_date",
                   "valid_until", "valid_from", "pincode", "establishment_year", "program_intake"}

def check_ai_reading(data: bytes, detected_type: str, ex: ExtractionResult, fields: list[Candidate], password: str | None, reader) -> Check:
    """An independent text-recognition model re-reads the rendered pages; the document's text and numeric values must be visible.

    ``reader(jpeg_bytes) -> str``. Pages are read in parallel. Values present in the text layer but not visible to the
    reader indicate overlaid/covered content (or a misread), so they are flagged for review.
    """
    from concurrent.futures import ThreadPoolExecutor

    import pymupdf

    field_pages = sorted({f.page_no for f in fields if f.page_no})
    pages = sorted(set([p.page_no for p in ex.pages if not p.is_blank][:AI_MAX_PAGES] + field_pages[:2]))[:AI_MAX_PAGES]
    if not pages:
        return [Check("ai_reading", "tampering", "not_applicable", "AI reading cross-check", "No readable pages to cross-check.")]
    images: dict[int, bytes] = {}
    if detected_type == "pdf":
        doc = pymupdf.open(stream=data, filetype="pdf")
        if doc.needs_pass and password:
            doc.authenticate(password)
        try:
            for n in pages:
                pix = doc.load_page(n - 1).get_pixmap(matrix=pymupdf.Matrix(AI_DPI / 72, AI_DPI / 72), alpha=False, colorspace=pymupdf.csRGB)
                images[n] = pix.tobytes("jpeg", jpg_quality=85)
        finally:
            doc.close()
    else:
        for p in ex.pages:
            if p.page_no in pages and p.analysis_image is not None:
                b = io.BytesIO()
                Image.fromarray(p.analysis_image).save(b, format="JPEG", quality=85)
                images[p.page_no] = b.getvalue()
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = dict(zip(images, pool.map(reader, images.values())))
    texts = {n: r if isinstance(r, str) else "\n".join(filter(None, [r.get("header"), r.get("text"), r.get("footer")]))
             for n, r in results.items()}
    analyses = {n: r for n, r in results.items() if isinstance(r, dict)}
    extra = [check_visual_analysis(analyses, images)] if analyses else []
    issues, stats = [], []
    for n, text in texts.items():
        page = next(p for p in ex.pages if p.page_no == n)
        doc_words = {w for w in word_tokens(" ".join(ln.text for ln in page.lines)) if len(w) >= 3}
        ai_words = set(word_tokens(text))
        recall = len(doc_words & ai_words) / len(doc_words) if doc_words else 1.0
        ai_digits = re.sub(r"\D", "", text)
        stats.append({"page_no": n, "words": len(doc_words), "agreement": round(recall, 3)})
        if doc_words and len(doc_words) >= 15 and recall < 0.5:
            issues.append({"page_no": n, "kind": "page_text_mismatch", "agreement": round(recall, 3)})
        for f in fields:
            if f.field_key not in AI_CHECK_FIELDS:
                continue  # multi-line values (addresses, names) do not read back as one digit run
            digits = re.sub(r"\D", "", f.value or "")
            if f.page_no == n and len(digits) >= 3 and digits not in ai_digits:
                issues.append({"page_no": n, "kind": "value_not_visible", "field_key": f.field_key, "value": f.value, "bbox": f.bbox})
    if not issues:
        return [Check("ai_reading", "tampering", "pass", "AI reading cross-check",
                      f"An independent AI text reader re-read {len(texts)} page(s); the document's text and every numeric value are visible as extracted.",
                      {"pages": stats})] + extra
    missing = [i for i in issues if i["kind"] == "value_not_visible"]
    good_pages = all(s["agreement"] >= 0.8 for s in stats)
    level = "high" if len(missing) >= 2 and good_pages else "medium"
    parts = []
    if missing:
        parts.append("values not visible on the page: " + ", ".join(f"{i['field_key'].replace('_', ' ')} '{i['value']}'" for i in missing[:5]))
    if any(i["kind"] == "page_text_mismatch" for i in issues):
        parts.append("much of the text layer is not visible on the rendered page")
    return [Check("ai_reading", "tampering", "warning", "AI reading cross-check",
                  "The AI reader disagrees with the extracted text — " + "; ".join(parts) + ". This can indicate overlaid or edited content.",
                  {"issues": issues, "pages": stats, "significance": level},
                  [{"type": "page", "page_no": i["page_no"], "bbox": i.get("bbox"), "note": i["kind"]} for i in issues])] + extra


def check_visual_analysis(analyses: dict[int, dict], images: dict[int, bytes]) -> Check:
    """Record what the AI visual analysis saw on each page: document type, signatures, stamps, photographs and located marks."""
    from ..integrations.vision_report import _clean, assessment_text, items

    pages, evidence = [], []
    for n in sorted(analyses):
        a = analyses[n]
        with Image.open(io.BytesIO(images[n])) as im:
            w, h = im.size
        v = a.get("visual") or {}
        photos = [{"where": _clean(o.get("location_on_page")), "seen": items(o.get("clear_observations"))[:4],
                   "possible_defects": items(o.get("possible_visible_defects"))[:3]}
                  for o in v.get("photograph_observations") or [] if isinstance(o, dict)]
        dets = []
        for d in a.get("detections") or []:
            b = [max(0.0, min(1.0, round(x / dim, 4))) for x, dim in zip(d["box"], (w, h, w, h))]
            if d["kind"] == "image_region" and (b[2] - b[0]) * (b[3] - b[1]) > 0.9:
                continue
            dets.append({"label": d["label"], "kind": d["kind"], "confidence": d["confidence"]})
            evidence.append({"type": "page", "page_no": n, "bbox": b, "note": d["label"], "kind": d["kind"]})
        ass = a.get("assessment") or {}
        pages.append({
            "page_no": n,
            "document_type": _clean(v.get("document_type_candidate")) or None,
            "signatures": _clean(v.get("signature_marks")) or None,
            "stamps": _clean(v.get("stamp_marks")) or None,
            "limitations": items(v.get("evidence_limitations"))[:4],
            "photographs": photos,
            "detections": dets,
            "condition": assessment_text(ass) if ass.get("findings") else None,
        })
    doc_type = next((p["document_type"] for p in pages if p["document_type"]), None)
    concern = any(p["condition"] for p in pages) or any(e["kind"] == "defect" for e in evidence)
    observed = lambda t: bool(t) and not t.lower().startswith(("not observed", "none", "no "))  # noqa: E731
    sig = next((p["signatures"] for p in pages if observed(p["signatures"])), None)
    stamp = next((p["stamps"] for p in pages if observed(p["stamps"])), None)
    parts = [f"{len(pages)} page(s) analysed"]
    if doc_type:
        parts.append(f"appears to be: {doc_type}")
    parts.append(f"signature: {sig}" if sig else "no signature observed")
    parts.append(f"stamp/seal: {stamp}" if stamp else "no stamp or seal observed")
    if concern:
        parts.append("possible visible defects were located in photographs and need review")
    return Check("ai_visual_analysis", "visual_analysis", "warning" if concern else "info", "AI visual analysis",
                 "; ".join(parts)[:1500] + ".",
                 {"pages": pages, "document_type": doc_type, "signature": sig, "stamp": stamp, "significance": "low" if concern else "none"},
                 evidence[:60])
