"""OCR engine wrapper (RapidOCR / ONNX Runtime, runs locally — no document leaves the server)."""

from __future__ import annotations

import threading
from dataclasses import dataclass

import numpy as np

_engine = None
_lock = threading.Lock()


@dataclass
class OcrLine:
    text: str
    bbox: tuple[float, float, float, float]  # pixel coordinates (x0, y0, x1, y1)
    confidence: float


def _get_engine():
    global _engine
    if _engine is None:
        from rapidocr_onnxruntime import RapidOCR

        _engine = RapidOCR()
    return _engine


def run_ocr(image_rgb: np.ndarray) -> list[OcrLine]:
    """Return text lines with bounding boxes and per-line recognition confidence."""
    with _lock:  # the ONNX session is shared; serialise calls for predictable memory use
        result, _elapsed = _get_engine()(image_rgb)
    lines: list[OcrLine] = []
    for item in result or []:
        box, text, conf = item
        xs = [p[0] for p in box]
        ys = [p[1] for p in box]
        text = (text or "").strip()
        if not text:
            continue
        lines.append(OcrLine(text=text, bbox=(min(xs), min(ys), max(xs), max(ys)), confidence=float(conf)))
    lines.sort(key=lambda ln: (round(ln.bbox[1] / 12), ln.bbox[0]))
    return lines


def engine_available() -> bool:
    try:
        _get_engine()
        return True
    except Exception:  # noqa: BLE001
        return False
