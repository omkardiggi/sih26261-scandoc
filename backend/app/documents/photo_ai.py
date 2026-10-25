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
        if custom.exists() and meta.exists():
            m = json.loads(meta.read_text(encoding="utf-8"))
            _state.update(kind="custom", sess=_session(custom), labels=m["labels"], defect_labels=set(m.get("defect_labels", [])),
                          size=int(m.get("input_size", 224)), mean=np.array(m.get("mean", [0.485, 0.456, 0.406]), dtype=np.float32),
                          std=np.array(m.get("std", [0.229, 0.224, 0.225]), dtype=np.float32), name=m.get("name", "officer-trained model"))
        elif (MODEL_DIR / "clip_vision_q.onnx").exists() and (MODEL_DIR / "clip_labels.json").exists():
            labels = json.loads((MODEL_DIR / "clip_labels.json").read_text(encoding="utf-8"))
            _state.update(kind="clip", sess=_session(MODEL_DIR / "clip_vision_q.onnx"),
                          classes=[c["name"] for c in labels["classes"]],
                          class_emb=np.array([c["embedding"] for c in labels["classes"]], dtype=np.float32),
                          defects=[d["name"] for d in labels["defects"]],
                          defect_emb=np.array([d["embedding"] for d in labels["defects"]], dtype=np.float32),
                          good=np.array(labels["good"], dtype=np.float32).reshape(-1, 512), size=224, mean=CLIP_MEAN, std=CLIP_STD,
                          name="general image model")
        else:
            _state.update(kind="none")
        return _state


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


def analyse(img_rgb: np.ndarray) -> dict | None:
    """{class, score, top: [(name, score)], defects: [{name, score}], model} for one photograph, or None."""
    if not available() or img_rgb.size == 0 or min(img_rgb.shape[:2]) < 24:
        return None
    st = _load()
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
