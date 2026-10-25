"""Text, layout and table extraction from PDFs and images.

* Digital PDFs: embedded text lines with coordinates (PyMuPDF) + tables (pdfplumber).
* Scanned pages / images: local OCR with per-line boxes and confidence.
* Every page is rendered to a PNG preview used for evidence display.
Bounding boxes are normalised to 0..1 of the page width/height.
"""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass, field

import numpy as np
from PIL import Image

from ..config import get_settings
from .normalize import clean_text
from .ocr import run_ocr

log = logging.getLogger("app.extract")

MIN_EMBEDDED_CHARS = 25
MAX_RENDER_SIDE = 4200


@dataclass
class Line:
    page_no: int
    seq: int
    text: str
    bbox: list[float]
    confidence: float | None
    source: str  # embedded | ocr


@dataclass
class PageResult:
    page_no: int
    width_pt: float
    height_pt: float
    preview_png: bytes
    preview_w: int
    preview_h: int
    text_source: str
    char_count: int
    ocr_confidence: float | None
    is_blank: bool
    unreadable: bool
    lines: list[Line] = field(default_factory=list)
    analysis_image: np.ndarray | None = None  # RGB render used for OCR / visual checks (not persisted)


@dataclass
class TableResult:
    page_no: int
    bbox: list[float] | None
    header: list[str]
    rows: list[list[str]]
    cell_boxes: list[list[list[float] | None]]
    source: str = "embedded"


@dataclass
class ExtractionResult:
    pages: list[PageResult]
    tables: list[TableResult]
    text_layer: str
    ocr_used: bool
    mean_ocr_confidence: float | None

    def all_lines(self) -> list[Line]:
        return [ln for p in self.pages for ln in p.lines]

    def full_text(self) -> str:
        return "\n".join(ln.text for ln in self.all_lines())


def _png(img: Image.Image, max_width: int = 1600) -> tuple[bytes, int, int]:
    if img.width > max_width:
        ratio = max_width / img.width
        img = img.resize((max_width, max(1, int(img.height * ratio))), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue(), img.width, img.height


def _is_blank(gray: np.ndarray) -> bool:
    return float((gray < 200).mean()) < 0.002


def _ocr_page(img_rgb: np.ndarray, page_no: int, seq_start: int = 0) -> tuple[list[Line], float | None]:
    h, w = img_rgb.shape[:2]
    lines = []
    confs = []
    for i, ol in enumerate(run_ocr(img_rgb)):
        x0, y0, x1, y1 = ol.bbox
        lines.append(Line(page_no, seq_start + i, clean_text(ol.text),
                          [round(x0 / w, 5), round(y0 / h, 5), round(x1 / w, 5), round(y1 / h, 5)],
                          round(ol.confidence, 4), "ocr"))
        confs.append(ol.confidence)
    return lines, (round(float(np.mean(confs)), 4) if confs else None)


def _render(page, dpi: int):
    import pymupdf

    rect = page.rect
    scale = dpi / 72.0
    longest = max(rect.width, rect.height) * scale
    if longest > MAX_RENDER_SIDE:
        scale = MAX_RENDER_SIDE / max(rect.width, rect.height)
    pix = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False, colorspace=pymupdf.csRGB)
    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    return img


def _embedded_lines(page, page_no: int) -> list[Line]:
    rect = page.rect
    W, H = rect.width or 1, rect.height or 1
    d = page.get_text("dict")
    out: list[Line] = []
    seq = 0
    for block in d.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            text = clean_text("".join(span.get("text", "") for span in line.get("spans", [])))
            if not text:
                continue
            x0, y0, x1, y1 = line["bbox"]
            out.append(Line(page_no, seq, text, [round(x0 / W, 5), round(y0 / H, 5), round(x1 / W, 5), round(y1 / H, 5)], 1.0, "embedded"))
            seq += 1
    out.sort(key=lambda ln: (round(ln.bbox[1], 3), ln.bbox[0]))
    for i, ln in enumerate(out):
        ln.seq = i
    return out


def _pdf_tables(data: bytes, password: str | None, pages_with_text: set[int]) -> list[TableResult]:
    import pdfplumber

    results: list[TableResult] = []
    try:
        with pdfplumber.open(io.BytesIO(data), password=password or "") as pdf:
            for idx, page in enumerate(pdf.pages, start=1):
                if idx not in pages_with_text:
                    continue
                W, H = float(page.width) or 1, float(page.height) or 1
                for t in page.find_tables():
                    raw_rows = t.extract()
                    if not raw_rows:
                        continue
                    rows = [[clean_text(c) for c in r] for r in raw_rows]
                    boxes: list[list[list[float] | None]] = []
                    for r in t.rows:
                        boxes.append([
                            [round(c[0] / W, 5), round(c[1] / H, 5), round(c[2] / W, 5), round(c[3] / H, 5)] if c else None
                            for c in r.cells
                        ])
                    x0, top, x1, bottom = t.bbox
                    results.append(TableResult(idx, [x0 / W, top / H, x1 / W, bottom / H], rows[0], rows, boxes))
    except Exception as exc:  # noqa: BLE001 - table extraction is best-effort
        log.info("table extraction skipped: %s", type(exc).__name__)
    return results


def _overlap(a: list[float], b: list[float]) -> float:
    """Share of box ``a`` covered by box ``b`` (normalised coordinates)."""
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    area = max((a[2] - a[0]) * (a[3] - a[1]), 1e-9)
    return max(0.0, w) * max(0.0, h) / area


def _scan_with_sparse_text(page, lines: list[Line]) -> bool:
    """True when the page is a full-page scan whose digital text covers only a small part of it."""
    W, H = page.rect.width or 1, page.rect.height or 1
    scanned = any(r.width * r.height / (W * H) >= 0.6 for img in page.get_images(full=True) for r in page.get_image_rects(img[0]))
    covered = sum((ln.bbox[2] - ln.bbox[0]) * (ln.bbox[3] - ln.bbox[1]) for ln in lines if ln.bbox)
    return scanned and covered < 0.08


def extract_pdf(data: bytes, password: str | None = None, progress=None) -> ExtractionResult:
    import pymupdf

    s = get_settings()
    doc = pymupdf.open(stream=data, filetype="pdf")
    if doc.needs_pass:
        if not password or not doc.authenticate(password):
            raise ValueError("PDF is password-protected")
    pages: list[PageResult] = []
    ocr_confs: list[float] = []
    embedded_pages = 0
    try:
        n = doc.page_count
        for i in range(n):
            page = doc.load_page(i)
            page_no = i + 1
            lines = _embedded_lines(page, page_no)
            chars = sum(len(ln.text) for ln in lines)
            preview_img = _render(page, s.preview_render_dpi)
            preview_png, pw, ph = _png(preview_img)
            gray = np.asarray(preview_img.convert("L"))
            blank = _is_blank(gray)
            analysis = None
            source = "embedded"
            ocr_conf = None
            if chars >= MIN_EMBEDDED_CHARS and not (not blank and _scan_with_sparse_text(page, lines)):
                embedded_pages += 1
            elif chars >= MIN_EMBEDDED_CHARS:
                # A scanned page carrying a little digital text (e.g. a digital-signature stamp): read the scan as well.
                analysis_img = _render(page, s.ocr_render_dpi)
                analysis = np.asarray(analysis_img)
                ocr_lines, ocr_conf = _ocr_page(analysis, page_no, seq_start=len(lines))
                lines = lines + [ln for ln in ocr_lines if not any(_overlap(ln.bbox, e.bbox) > 0.5 for e in lines)]
                chars = sum(len(ln.text) for ln in lines)
                source = "ocr"
                if ocr_conf is not None:
                    ocr_confs.append(ocr_conf)
            elif not blank:
                analysis_img = _render(page, s.ocr_render_dpi)
                analysis = np.asarray(analysis_img)
                ocr_lines, ocr_conf = _ocr_page(analysis, page_no)
                lines = ocr_lines
                chars = sum(len(ln.text) for ln in lines)
                source = "ocr"
                if ocr_conf is not None:
                    ocr_confs.append(ocr_conf)
            else:
                source = "none"
            unreadable = (not blank) and (chars < 10 or (ocr_conf is not None and ocr_conf < 0.5))
            pages.append(PageResult(page_no, page.rect.width, page.rect.height, preview_png, pw, ph, source,
                                    chars, ocr_conf, blank, unreadable, lines, analysis))
            if progress:
                progress((i + 1) / n * 0.6, f"Read page {page_no} of {n}")
    finally:
        doc.close()
    tables = _pdf_tables(data, password, {p.page_no for p in pages if p.text_source == "embedded"})
    text_pages = [p for p in pages if not p.is_blank]
    if text_pages and embedded_pages == len(text_pages):
        layer = "embedded"
    elif embedded_pages == 0:
        layer = "none"
    else:
        layer = "partial"
    return ExtractionResult(pages, tables, layer, bool(ocr_confs),
                            round(float(np.mean(ocr_confs)), 4) if ocr_confs else None)


def extract_image(data: bytes, progress=None) -> ExtractionResult:
    s = get_settings()
    Image.MAX_IMAGE_PIXELS = s.max_image_pixels
    pages: list[PageResult] = []
    confs: list[float] = []
    with Image.open(io.BytesIO(data)) as im:
        n = getattr(im, "n_frames", 1)
        for idx in range(n):
            if n > 1:
                im.seek(idx)
            frame = im.convert("RGB")
            if max(frame.size) > MAX_RENDER_SIDE:
                ratio = MAX_RENDER_SIDE / max(frame.size)
                frame = frame.resize((int(frame.width * ratio), int(frame.height * ratio)), Image.LANCZOS)
            arr = np.asarray(frame)
            preview_png, pw, ph = _png(frame)
            blank = _is_blank(np.asarray(frame.convert("L")))
            lines, conf = ([], None) if blank else _ocr_page(arr, idx + 1)
            if conf is not None:
                confs.append(conf)
            chars = sum(len(ln.text) for ln in lines)
            unreadable = (not blank) and (chars < 10 or (conf is not None and conf < 0.5))
            # Images have no physical size; use pixels at 72 dpi as nominal points.
            pages.append(PageResult(idx + 1, float(frame.width), float(frame.height), preview_png, pw, ph,
                                    "ocr" if lines else "none", chars, conf, blank, unreadable, lines, arr))
            if progress:
                progress((idx + 1) / n * 0.6, f"Read image page {idx + 1} of {n}")
    return ExtractionResult(pages, [], "none", True, round(float(np.mean(confs)), 4) if confs else None)
