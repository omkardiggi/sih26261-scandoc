"""Greedy IoU tracking of detections across consecutive sampled frames."""

from __future__ import annotations


def iou(a: dict, b: dict) -> float:
    ix0, iy0 = max(a["x0"], b["x0"]), max(a["y0"], b["y0"])
    ix1, iy1 = min(a["x1"], b["x1"]), min(a["y1"], b["y1"])
    inter = max(0.0, ix1 - ix0) * max(0.0, iy1 - iy0)
    area = lambda r: max(0.0, r["x1"] - r["x0"]) * max(0.0, r["y1"] - r["y0"])  # noqa: E731
    union = area(a) + area(b) - inter
    return inter / union if union > 0 else 0.0


def assign_tracks(frames: list[list[dict]], threshold: float = 0.3, max_gap: int = 1) -> None:
    """``frames`` is a list (in time order) of detection lists; each detection is a dict with
    ``label``, ``box`` and receives a ``track_id``. Detections are linked to the best-overlapping
    detection of the same label in the previous ``max_gap + 1`` frames."""
    next_id = 1
    history: list[list[dict]] = []
    for dets in frames:
        candidates = [d for past in history[-(max_gap + 1):] for d in past]
        used: set[int] = set()
        for det in sorted(dets, key=lambda d: -(d.get("confidence") or 0)):
            best, best_iou = None, threshold
            for c in candidates:
                if c["label"] != det["label"] or c["track_id"] in used:
                    continue
                score = iou(c["box"], det["box"])
                if score >= best_iou:
                    best, best_iou = c, score
            if best is not None:
                det["track_id"] = best["track_id"]
                used.add(best["track_id"])
            else:
                det["track_id"] = next_id
                next_id += 1
        history.append(dets)
