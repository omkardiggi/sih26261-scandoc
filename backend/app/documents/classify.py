"""Explainable keyword-based document classification (configurable per category)."""

from __future__ import annotations

from ..models import DocumentCategory

MIN_SCORE = 4


def classify(text: str, categories: list[DocumentCategory]) -> dict:
    low = (text or "").lower()
    first_page = low[:4000]
    scores = []
    for cat in categories:
        if not cat.active or not cat.keywords:
            continue
        score = 0.0
        matched = []
        for kw in cat.keywords:
            pattern = str(kw.get("pattern", "")).lower()
            if not pattern:
                continue
            count = low.count(pattern)
            if count:
                weight = float(kw.get("weight", 1))
                # Matches on the first page count more; repeated matches saturate.
                bonus = 1.5 if pattern in first_page else 1.0
                contribution = weight * min(count, 3) ** 0.5 * bonus
                score += contribution
                matched.append({"pattern": pattern, "count": count, "contribution": round(contribution, 2)})
        if score:
            scores.append((score, cat.key, matched))
    scores.sort(reverse=True)
    if not scores or scores[0][0] < MIN_SCORE:
        return {
            "category_key": "other",
            "method": "keyword rules",
            "score": round(scores[0][0], 2) if scores else 0,
            "explanation": "No category's keywords matched strongly enough; classified as 'Other' for officer review.",
            "candidates": [{"category_key": k, "score": round(s, 2), "matched": m} for s, k, m in scores[:3]],
            "confident": False,
        }
    top_score, top_key, top_matched = scores[0]
    runner = scores[1][0] if len(scores) > 1 else 0.0
    margin = top_score - runner
    return {
        "category_key": top_key,
        "method": "keyword rules",
        "score": round(top_score, 2),
        "margin": round(margin, 2),
        "confident": margin >= 3,
        "explanation": f"Matched {len(top_matched)} configured keyword(s) for this category.",
        "candidates": [{"category_key": k, "score": round(s, 2), "matched": m} for s, k, m in scores[:3]],
    }
