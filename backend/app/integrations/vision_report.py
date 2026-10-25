"""Condense the outputs of a multi-step vision workflow into one structured result.

The configured document/image analysis workflow returns page text, a visual-evidence review (signatures, stamps,
photographs, document type), a condition assessment for photographed facilities and several detection blocks.
Only what the workflow actually returned is used; missing outputs simply stay empty.
"""

from __future__ import annotations

import json
import re

# Detection outputs worth showing, and what kind of mark they are.
DETECTION_OUTPUTS = {
    "defect_candidates": "defect",
    "localized_defect_evidence": "defect",
    "photograph_object_detections": "object",
    "evidence_detections": "evidence",
    "photograph_regions": "image_region",
}
TEXT_OUTPUTS = ("document_text", "raw_extraction")


def loads(text: str | None):
    """Parse JSON the workflow returned as text (tolerating code fences); None when it is not JSON."""
    if not text:
        return None
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    try:
        return json.loads(t)
    except ValueError:
        m = re.search(r"\{.*\}", t, re.S)
        if m:
            try:
                return json.loads(m.group(0))
            except ValueError:
                return None
    return None


def _clean(s) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip()


def items(v) -> list[str]:
    """A list output that the model sometimes returns as a single sentence; empty and "none" entries dropped."""
    vals = [v] if isinstance(v, str) else v if isinstance(v, list) else []
    return [c for c in (_clean(x) for x in vals if isinstance(x, str)) if c and not re.match(r"(?i)^(none|n/?a|no|nil)\b\.?$", c)]


def humanise(s) -> str:
    s = _clean(str(s or "").replace("_", " "))
    return s[:1].upper() + s[1:]


def summarize(predictions, width: int | None = None, height: int | None = None) -> dict:
    """Return {text, header, footer, visual, assessment, detections}; detection boxes are normalised when a size is given."""
    texts: dict[str, list[str]] = {}
    dets: list[dict] = []
    seen = set()
    for p in predictions:
        out = p.geometry.get("output") or p.label
        if p.geometry_type == "text":
            texts.setdefault(out, []).append(p.geometry.get("text") or "")
            continue
        kind = DETECTION_OUTPUTS.get(out)
        box = p.geometry if p.geometry_type == "bbox" else p.geometry.get("bbox")
        if not kind or not box:
            continue
        b = [box["x0"], box["y0"], box["x1"], box["y1"]]
        if width and height:
            b = [max(0.0, min(1.0, round(v / d, 4))) for v, d in zip(b, (width, height, width, height))]
            if kind == "image_region" and (b[2] - b[0]) * (b[3] - b[1]) > 0.9:
                continue  # the whole frame is the photograph: nothing to locate
        key = (p.label.lower(), tuple(round(v, 2) for v in b))
        if key in seen:
            continue  # the same detection is often reported by more than one output
        seen.add(key)
        dets.append({"label": humanise(p.label), "kind": kind, "confidence": p.confidence, "box": b})

    first = lambda k: next((t for t in texts.get(k, []) if t.strip()), "")  # noqa: E731
    text = next((first(k) for k in TEXT_OUTPUTS if first(k)), "")
    visual = loads(first("visual_evidence"))
    reports = []
    for k in ("condition_and_requirement_report", "photograph_review"):
        for t in texts.get(k, []):
            r = loads(t)
            if isinstance(r, dict) and r not in reports:
                reports.append(r)
    findings, limitations, statuses, scenes = [], [], [], []
    for r in reports:
        statuses.append(r.get("assessment_status"))
        if r.get("scene_category") and r["scene_category"] != "unknown":
            scenes.append(r["scene_category"])
        limitations += items(r.get("limitations"))
        for f in r.get("findings") or []:
            if isinstance(f, dict) and f.get("defect_candidate") and f not in findings:
                findings.append(f)
    concern = any(s == "visible_concern_observed" for s in statuses) or bool(findings)
    assessment = {
        "scene": humanise(scenes[0]) if scenes else None,
        "status": "visible_concern" if concern else ("insufficient_evidence" if statuses else None),
        "findings": [{
            "issue": humanise(f.get("defect_candidate")), "object": _clean(f.get("affected_object")),
            "where": _clean(f.get("location_in_photo")), "evidence": _clean(f.get("exact_visible_evidence")),
            "certainty": humanise(f.get("certainty")), "consequence": _clean(f.get("consequence_candidate")),
            "verify": _clean(f.get("verification_needed")),
        } for f in findings],
        "limitations": list(dict.fromkeys(limitations))[:6],
    }
    return {"text": text, "header": first("header_text"), "footer": first("footer_text"),
            "visual": visual if isinstance(visual, dict) else None, "assessment": assessment, "detections": dets}


def assessment_text(a: dict) -> str:
    """Readable summary of a condition assessment for display next to an image."""
    lines = []
    if a.get("scene"):
        lines.append(f"Scene: {a['scene']}")
    if a.get("findings"):
        for i, f in enumerate(a["findings"], 1):
            lines.append(f"{i}. {f['issue']}" + (f" — {f['object']}" if f.get("object") else ""))
            if f.get("evidence"):
                lines.append(f"   Seen: {f['evidence']}")
            if f.get("verify"):
                lines.append(f"   Verify: {f['verify']}")
    elif a.get("status"):
        lines.append("No visible concern was identified." if a["status"] != "insufficient_evidence"
                     else "Not enough visual evidence to assess the condition.")
    if a.get("limitations"):
        lines.append("Limits: " + " ".join(a["limitations"][:3]))
    return "\n".join(lines)
