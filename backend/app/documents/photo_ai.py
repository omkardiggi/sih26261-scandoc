"""Photograph recognition that runs on this server (no outside service).

A photo is given a class name (classroom, fire extinguisher, dustbins, ...) and checked for visible defects.
If an officer-trained model is present (data/models/photo_model.onnx + photo_model.json, see scripts/train_photo_model.py)
it is used; otherwise the bundled general image model (CLIP, data/models/clip_vision_q.onnx) is matched against the
class and defect descriptions in data/models/clip_labels.json.
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path

import numpy as np
from PIL import Image

from ..config import BACKEND_DIR

log = logging.getLogger("app.photo_ai")
MODEL_DIR = BACKEND_DIR.parent / "data" / "models"
CLIP_MEAN = np.array([0.48145466, 0.4578275, 0.40821073], dtype=np.float32)
CLIP_STD = np.array([0.26862954, 0.26130258, 0.27577711], dtype=np.float32)
DEFECT_THRESHOLD = 0.5  # general model: a defect must beat every "good condition" description and the other defects

_lock = threading.Lock()
_state: dict = {}


def _session(path: Path):
    import onnxruntime as ort

    opts = ort.SessionOptions()
    opts.intra_op_num_threads = 2
    return ort.InferenceSession(str(path), sess_options=opts, providers=["CPUExecutionProvider"])


def _load() -> dict:
    with _lock:
        if _state:
            return _state
        custom, meta = MODEL_DIR / "photo_model.onnx", MODEL_DIR / "photo_model.json"
        general = _load_general()
        if custom.exists() and meta.exists():
            m = json.loads(meta.read_text(encoding="utf-8"))
            _state.update(kind="custom", sess=_session(custom), labels=m["labels"], defect_labels=set(m.get("defect_labels", [])),
                          size=int(m.get("input_size", 224)), mean=np.array(m.get("mean", [0.485, 0.456, 0.406]), dtype=np.float32),
                          std=np.array(m.get("std", [0.229, 0.224, 0.225]), dtype=np.float32), name=m.get("name", "officer-trained model"),
                          general=general)
        elif general:
            _state.update(general)
        else:
            _state.update(kind="none")
        return _state


def _load_general() -> dict | None:
    """The bundled general image model (CLIP) with its class and defect descriptions, if present."""
    if not ((MODEL_DIR / "clip_vision_q.onnx").exists() and (MODEL_DIR / "clip_labels.json").exists()):
        return None
    labels = json.loads((MODEL_DIR / "clip_labels.json").read_text(encoding="utf-8"))
    return dict(kind="clip", sess=_session(MODEL_DIR / "clip_vision_q.onnx"),
                bucket_check={k: np.array(v, dtype=np.float32) for k, v in labels.get("bucket_check", {}).items()},
                classes=[c["name"] for c in labels["classes"]],
                class_emb=np.array([c["embedding"] for c in labels["classes"]], dtype=np.float32),
                defects=[d["name"] for d in labels["defects"]],
                defect_emb=np.array([d["embedding"] for d in labels["defects"]], dtype=np.float32),
                good=np.array(labels["good"], dtype=np.float32).reshape(-1, 512), size=224, mean=CLIP_MEAN, std=CLIP_STD,
                name="general image model")


def available() -> bool:
    try:
        return _load()["kind"] != "none"
    except Exception as exc:  # noqa: BLE001 - recognition is optional; never break document processing
        log.warning("photo recognition unavailable: %s", exc)
        return False


def _prepare(img_rgb: np.ndarray, size: int, mean: np.ndarray, std: np.ndarray) -> np.ndarray:
    im = Image.fromarray(img_rgb).convert("RGB")
    w, h = im.size
    r = size / min(w, h)
    im = im.resize((max(size, round(w * r)), max(size, round(h * r))), Image.BICUBIC)
    w, h = im.size
    left, top = (w - size) // 2, (h - size) // 2
    a = np.asarray(im.crop((left, top, left + size, top + size)), dtype=np.float32) / 255.0
    return ((a - mean) / std).transpose(2, 0, 1)[None]


def _softmax(x: np.ndarray) -> np.ndarray:
    e = np.exp(x - x.max())
    return e / e.sum()


TRAINED_MIN_CONFIDENCE = 0.85  # below this the trained model is probably seeing something it was not taught
FIRE_CLASSES = {"Fire safety equipment", "Fire extinguisher", "Fire sand buckets", "Fire hose reel"}
# Empty fire buckets: set on a small set of photos (one rack of empty buckets, filled racks and 60 extinguisher photos),
# so the result is reported as a possible defect for the officer to verify on site.
BUCKET_MAX_GRAIN = 2.6  # sand gives the bucket opening a grainy texture; an empty painted inside is smooth
BUCKET_MIN_EMPTY = 0.8  # general model's "empty bucket" vs "bucket of sand", averaged over the buckets


def analyse(img_rgb: np.ndarray) -> dict | None:
    """{class, score, top: [(name, score)], defects: [{name, score}], model} for one photograph, or None."""
    if not available() or img_rgb.size == 0 or min(img_rgb.shape[:2]) < 24:
        return None
    st = _load()
    r = _analyse_with(st, img_rgb)
    if st["kind"] == "custom" and r["score"] < TRAINED_MIN_CONFIDENCE and not r["defects"] and st.get("general"):
        r = _analyse_with(st["general"], img_rgb)
        r["model"] = "general image model (not in the trained classes)"
    if r["class"] in FIRE_CLASSES:
        empty = _empty_fire_buckets(st.get("general") or st, img_rgb)
        if empty:
            r["defects"] = [empty] + r["defects"]
    return r


def _red(hsv: np.ndarray) -> np.ndarray:
    return ((hsv[..., 0] <= 10) | (hsv[..., 0] >= 165)) & (hsv[..., 1] > 90) & (hsv[..., 2] > 50)


def _empty_fire_buckets(g: dict, img_rgb: np.ndarray) -> dict | None:
    """A rack of red fire buckets whose openings show no sand: {name, score, reason, boxes} or None."""
    import cv2

    emb = g.get("bucket_check") or {}
    if g.get("kind") != "clip" or not all(k in emb for k in ("bucket", "extinguisher", "other", "empty", "full")):
        return None
    H, W = img_rgb.shape[:2]
    hsv = cv2.cvtColor(img_rgb, cv2.COLOR_RGB2HSV)
    k = max(3, min(H, W) // 30)
    body = cv2.morphologyEx(_red(hsv).astype(np.uint8), cv2.MORPH_OPEN, np.ones((k, k), np.uint8))  # drops stand bars and handles
    n, _lab, stats, _ = cv2.connectedComponentsWithStats(body)
    gray = cv2.cvtColor(img_rgb, cv2.COLOR_RGB2GRAY).astype(np.float32)
    grains, empties, boxes = [], [], []
    for i in range(1, n):
        x, y, w, h, area = (int(v) for v in stats[i])
        if area < 0.004 * H * W or not (0.7 <= w / h <= 2.2) or w < 12:
            continue
        crop = img_rgb[max(0, y - int(0.35 * h)):y + h, max(0, x - int(0.15 * w)):x + w + int(0.15 * w)]
        e = g["sess"].run(None, {"pixel_values": _prepare(crop, g["size"], g["mean"], g["std"])})[0][0]
        e = e / np.linalg.norm(e)
        if _softmax(100.0 * np.array([emb["bucket"] @ e, emb["extinguisher"] @ e, emb["other"] @ e]))[0] < 0.5:
            continue  # a red shape that is not a bucket
        empties.append(float(_softmax(100.0 * np.array([emb["empty"] @ e, emb["full"] @ e]))[0]))
        # The opening: just above the red body, where sand would show; measured at a fixed bucket width.
        oy0, oy1, ox0, ox1 = max(0, y - int(0.30 * h)), y + int(0.12 * h), x + int(0.1 * w), x + w - int(0.1 * w)
        inside = ~_red(hsv[oy0:oy1, ox0:ox1])
        s = 100.0 / w
        band = cv2.resize(gray[oy0:oy1, ox0:ox1], None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
        ins = cv2.resize(inside.astype(np.uint8), (band.shape[1], band.shape[0]), interpolation=cv2.INTER_NEAREST).astype(bool)
        lap = np.abs(cv2.Laplacian(cv2.GaussianBlur(band, (3, 3), 0), cv2.CV_32F))
        grains.append(float(lap[ins].mean()) if ins.any() else 0.0)
        top = max(0, y - int(0.35 * h))
        boxes.append([round(x / W, 4), round(top / H, 4), round((x + w) / W, 4), round((y + h) / H, 4)])
    if len(grains) < 2:
        return None  # only a rack of buckets is judged, not a single red object
    grain, empty = float(np.median(grains)), float(np.mean(empties))
    if grain > BUCKET_MAX_GRAIN or empty < BUCKET_MIN_EMPTY:
        return None
    return {"name": "Empty fire buckets", "score": round(empty, 3), "boxes": boxes,
            "reason": f"{len(grains)} fire buckets found; their openings show no sand (smooth inside, no sand texture), so they "
                      "appear empty. Fire buckets must be kept filled with dry sand."}


def _analyse_with(st: dict, img_rgb: np.ndarray) -> dict:
    x = _prepare(img_rgb, st["size"], st["mean"], st["std"])
    if st["kind"] == "custom":
        logits = st["sess"].run(None, {st["sess"].get_inputs()[0].name: x})[0][0]
        p = _softmax(logits.astype(np.float32))
        order = np.argsort(-p)
        classes = [(st["labels"][i], float(p[i])) for i in order if st["labels"][i] not in st["defect_labels"]]
        defects = [{"name": st["labels"][i], "score": round(float(p[i]), 3)} for i in order
                   if st["labels"][i] in st["defect_labels"] and p[i] >= 0.5]
    else:
        emb = st["sess"].run(None, {"pixel_values": x})[0][0]
        emb = emb / np.linalg.norm(emb)
        p = _softmax(100.0 * st["class_emb"] @ emb)
        order = np.argsort(-p)
        classes = [(st["classes"][i], float(p[i])) for i in order]
        cond = _softmax(100.0 * np.concatenate([st["defect_emb"] @ emb, st["good"] @ emb]))
        nd = len(st["defects"])
        best = int(np.argmax(cond))
        defects = []
        from ..config import get_settings

        if get_settings().photo_general_defects and best < nd and cond[best] >= DEFECT_THRESHOLD:
            defects.append({"name": st["defects"][best], "score": round(float(cond[best]), 3)})
    return {"class": classes[0][0], "score": round(classes[0][1], 3), "top": [(n, round(s, 3)) for n, s in classes[:3]],
            "defects": defects[:3], "model": st["name"]}
