"""Computer-vision inference via the Roboflow hosted API (server-side only; keys never reach the browser).

Request/response formats follow Roboflow's public documentation:
  POST {base}/{project_id}/{version}?confidence=..&overlap=..  body = base64 image
  (Content-Type: application/x-www-form-urlencoded), Authorization: Bearer <key>.
  Detection predictions: x, y (box centre), width, height, confidence, class, class_id.
  Instance segmentation adds ``points``: [{x, y}, ...]. Classification returns ``predictions`` + ``top``.
Nothing about specific projects, classes or capabilities is assumed: they come from ModelConfig.
"""

from __future__ import annotations

import base64
import io
import json
from dataclasses import dataclass, field

from PIL import Image

from ..config import get_settings
from ..models import Integration, ModelConfig
from . import http
from .secrets import resolve_secret

MAX_SEND_SIDE = 2048
TASK_TYPES = {"object_detection", "instance_segmentation", "classification", "workflow"}


@dataclass
class Prediction:
    label: str
    confidence: float | None
    geometry_type: str  # bbox | polygon | image | text
    geometry: dict


@dataclass
class InferenceResult:
    ok: bool
    status: str  # succeeded | failed | auth_failed | not_configured
    message: str
    http_status: int | None = None
    duration_ms: int | None = None
    predictions: list[Prediction] = field(default_factory=list)
    raw: bytes | None = None
    image_width: int | None = None
    image_height: int | None = None


def _prepare(image_bytes: bytes) -> tuple[bytes, float, int, int]:
    """Return (jpeg_bytes, scale_back, orig_w, orig_h); downsizes very large images for upload."""
    with Image.open(io.BytesIO(image_bytes)) as im:
        im = im.convert("RGB")
        w, h = im.size
        scale = 1.0
        if max(w, h) > MAX_SEND_SIDE:
            scale = MAX_SEND_SIDE / max(w, h)
            im = im.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=92)
    return buf.getvalue(), 1.0 / scale, w, h


def endpoint(integration: Integration, model: ModelConfig) -> str:
    base = (model.endpoint_base or integration.base_url or "").rstrip("/")
    if model.task_type == "workflow":
        # Workflows: project_id holds the workspace, model_version holds the workflow id.
        return f"{base}/{model.project_id.strip('/')}/workflows/{str(model.model_version).strip('/')}"
    return f"{base}/{model.project_id.strip('/')}/{str(model.model_version).strip('/')}"


def _strip_images(obj):
    """Drop base64 image payloads (e.g. visualisations) before a response is stored."""
    if isinstance(obj, dict):
        if obj.get("type") == "base64" and isinstance(obj.get("value"), str):
            return {"type": "base64", "value": f"<{len(obj['value'])} characters omitted>"}
        return {k: _strip_images(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_strip_images(v) for v in obj]
    return obj


def parse_workflow_outputs(payload: dict, scale_back: float, sent_w: int, sent_h: int) -> list[Prediction]:
    """Generic parsing of Workflow outputs: detection blocks become boxes/polygons, text outputs become text annotations.

    Every prediction records the workflow output it came from in ``geometry["output"]``; detection blocks nested in
    lists (one per crop/photograph) are parsed too.
    """
    preds: list[Prediction] = []

    def detections(name: str, val: dict) -> None:
        if not val["predictions"]:
            return
        has_points = any(isinstance(p, dict) and p.get("points") for p in val["predictions"])
        if all(isinstance(p, dict) and "x" in p for p in val["predictions"]):
            got = parse_predictions(val, "instance_segmentation" if has_points else "object_detection", scale_back, sent_w, sent_h)
        else:
            got = parse_predictions(val, "classification", scale_back, sent_w, sent_h)
        for p in got:
            p.geometry["output"] = name
        preds.extend(got)

    for out in payload.get("outputs") or []:
        if not isinstance(out, dict):
            continue
        for name, val in out.items():
            if isinstance(val, dict) and isinstance(val.get("predictions"), list):
                detections(name, val)
            elif isinstance(val, str) and val.strip():
                preds.append(Prediction(name[:120], None, "text", {"text": val.strip().strip('"').strip()[:20000], "output": name}))
            elif isinstance(val, dict) and isinstance(val.get("raw_output"), str):
                preds.append(Prediction(name[:120], None, "text", {"text": val["raw_output"][:20000], "output": name}))
            elif isinstance(val, list):
                for item in val:
                    if isinstance(item, dict) and isinstance(item.get("predictions"), list):
                        detections(name, item)
                    elif isinstance(item, str) and item.strip():
                        preds.append(Prediction(name[:120], None, "text", {"text": item.strip()[:20000], "output": name}))
    return preds


def parse_predictions(payload: dict, task_type: str, scale_back: float, sent_w: int, sent_h: int) -> list[Prediction]:
    preds: list[Prediction] = []
    img = payload.get("image") or {}
    # Coordinates are relative to the image the provider processed; map back to the original image.
    rw = float(img.get("width") or sent_w) or 1.0
    rh = float(img.get("height") or sent_h) or 1.0
    fx = (sent_w / rw) * scale_back
    fy = (sent_h / rh) * scale_back
    raw_preds = payload.get("predictions")
    if task_type == "classification":
        items = []
        if isinstance(raw_preds, list):
            items = raw_preds
        elif isinstance(raw_preds, dict):  # multi-label: {"class": {"confidence": x}}
            items = [{"class": k, "confidence": v.get("confidence")} for k, v in raw_preds.items() if isinstance(v, dict)]
        for p in items:
            if p.get("class") is not None:
                preds.append(Prediction(str(p["class"]), _f(p.get("confidence")), "image", {}))
        return preds
    for p in raw_preds or []:
        if not isinstance(p, dict) or p.get("class") is None:
            continue
        x, y, w, h = (_f(p.get(k)) for k in ("x", "y", "width", "height"))
        if None in (x, y, w, h):
            continue
        # Workflow detections made on a crop are relative to that crop; root_parent_origin places it in the image sent.
        org = p.get("root_parent_origin") if isinstance(p.get("root_parent_origin"), dict) else {}
        ox = oy = 0.0
        px, py = fx, fy
        if org and (_f(org.get("width")), _f(org.get("height"))) == (rw, rh) and (rw, rh) != (float(sent_w), float(sent_h)):
            ox, oy = _f(org.get("offset_x")) or 0.0, _f(org.get("offset_y")) or 0.0
            px = py = scale_back
        x, y = x + ox, y + oy
        box = {"x0": round((x - w / 2) * px, 2), "y0": round((y - h / 2) * py, 2),
               "x1": round((x + w / 2) * px, 2), "y1": round((y + h / 2) * py, 2)}
        points = p.get("points")
        if task_type == "instance_segmentation" and isinstance(points, list) and points:
            pts = [[round((_f(pt.get("x")) + ox) * px, 2), round((_f(pt.get("y")) + oy) * py, 2)] for pt in points
                   if isinstance(pt, dict) and _f(pt.get("x")) is not None and _f(pt.get("y")) is not None]
            preds.append(Prediction(str(p["class"]), _f(p.get("confidence")), "polygon", {"points": pts, "bbox": box}))
        else:
            preds.append(Prediction(str(p["class"]), _f(p.get("confidence")), "bbox", box))
    return preds


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def infer(integration: Integration, model: ModelConfig, image_bytes: bytes) -> InferenceResult:
    if model.task_type not in TASK_TYPES:
        return InferenceResult(False, "not_configured", f"Unsupported task type '{model.task_type}'.")
    if not (model.endpoint_base or integration.base_url):
        return InferenceResult(False, "not_configured", "No inference endpoint is configured.")
    key = resolve_secret(integration)
    if not key:
        return InferenceResult(False, "not_configured", "The inference API key is not configured (set ROBOFLOW_API_KEY or store it in settings).")
    payload_img, scale_back, ow, oh = _prepare(image_bytes)
    with Image.open(io.BytesIO(payload_img)) as im:
        sent_w, sent_h = im.size
    try:
        if model.task_type == "workflow":
            # Workflow API: JSON body carrying the key and the image input.
            resp = http.request("POST", endpoint(integration, model), headers={"Accept": "application/json"},
                                json_body={"api_key": key, "inputs": {"image": {"type": "base64", "value": base64.b64encode(payload_img).decode()}}},
                                timeout=get_settings().workflow_timeout_seconds, max_bytes=get_settings().workflow_max_response_bytes)
        else:
            params = {"confidence": model.confidence_threshold, "overlap": model.overlap_threshold, "format": "json"}
            headers = {"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"}
            if (integration.config or {}).get("api_key_transport") == "query":
                params["api_key"] = key
            else:
                headers["Authorization"] = f"Bearer {key}"
            resp = http.request("POST", endpoint(integration, model), headers=headers, params=params,
                                content=base64.b64encode(payload_img))
    except http.OutboundBlocked as exc:
        return InferenceResult(False, "failed", f"Request blocked by outbound safety rules: {exc}")
    except http.OutboundError as exc:
        return InferenceResult(False, "failed", f"Inference service unreachable: {exc}")
    base = dict(http_status=resp.status_code, duration_ms=resp.duration_ms, raw=resp.body, image_width=ow, image_height=oh)
    if resp.status_code in (401, 403):
        return InferenceResult(False, "auth_failed", f"Authentication rejected by the inference service (HTTP {resp.status_code}).", **base)
    if resp.status_code >= 400:
        detail = ""
        try:
            detail = str(json.loads(resp.body).get("message", ""))[:200]
        except Exception:  # noqa: BLE001
            pass
        return InferenceResult(False, "failed", f"Inference failed (HTTP {resp.status_code}). {detail}".strip(), **base)
    try:
        data = json.loads(resp.body)
    except Exception:  # noqa: BLE001
        return InferenceResult(False, "failed", "The inference response was not valid JSON.", **base)
    if model.task_type == "workflow":
        if not isinstance(data, dict) or not isinstance(data.get("outputs"), list):
            return InferenceResult(False, "failed", "The workflow response did not contain an 'outputs' list.", **base)
        base["raw"] = json.dumps(_strip_images(data)).encode("utf-8")
        preds = parse_workflow_outputs(data, scale_back, sent_w, sent_h)
        return InferenceResult(True, "succeeded", f"{len(preds)} output(s) returned.", predictions=preds, **base)
    if not isinstance(data, dict) or "predictions" not in data:
        return InferenceResult(False, "failed", "The inference response did not contain a 'predictions' field.", **base)
    preds = parse_predictions(data, model.task_type, scale_back, sent_w, sent_h)
    return InferenceResult(True, "succeeded", f"{len(preds)} prediction(s) returned.", predictions=preds, **base)


def test_model(integration: Integration, model: ModelConfig) -> InferenceResult:
    """A real inference request with a plain generated test image; success requires a well-formed response."""
    im = Image.new("RGB", (320, 320), (128, 128, 128))
    buf = io.BytesIO()
    im.save(buf, format="JPEG")
    return infer(integration, model, buf.getvalue())
