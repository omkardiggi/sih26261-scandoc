"""Background jobs: image inference and video frame sampling + inference.

Only models an officer has configured and enabled are used. When none apply, the
evidence is stored (and video frames are still extracted with timestamps) and the
status says so — no detections are ever produced without a real model response.
"""

from __future__ import annotations

import hashlib
import logging

from sqlalchemy import select

from .. import audit, storage
from ..config import get_settings
from ..db import session_scope
from ..integrations.roboflow import Prediction, infer
from ..integrations.vision_report import assessment_text, summarize
from ..jobs import PermanentJobError
from ..jobs.worker import JobContext, handler
from ..models import (
    Annotation,
    Finding,
    InferenceRun,
    InspectionCase,
    Integration,
    MediaAsset,
    ModelConfig,
    VideoFrame,
    utcnow,
)
from ..security.crypto import encrypt_secret, sha256_hex
from ..services.notify import notify
from .tracking import assign_tracks
from .video import expected_samples, sample_frames

log = logging.getLogger("app.inspection")


def applicable_models(db, media: MediaAsset, model_ids: list[str] | None = None) -> list[ModelConfig]:
    stmt = select(ModelConfig).where(ModelConfig.enabled.is_(True))
    if model_ids:
        stmt = stmt.where(ModelConfig.id.in_(model_ids))
    models = db.execute(stmt).scalars().all()
    out = []
    for m in models:
        integ = db.get(Integration, m.integration_id)
        if integ is None or not integ.enabled:
            continue
        if m.facility_categories and media.facility_category and media.facility_category not in m.facility_categories:
            continue
        if get_settings().private_mode:
            from urllib.parse import urlsplit

            from ..integrations.http import _resolves_to_own_network

            p = urlsplit(m.endpoint_base or integ.base_url or "")
            if not (p.hostname and _resolves_to_own_network(p.hostname, p.port or 443)):
                continue  # private mode: only models served on this machine / network
        out.append(m)
    return out


def _snapshot(m: ModelConfig) -> dict:
    return {"model_config_id": m.id, "name": m.name, "project_id": m.project_id, "model_version": m.model_version,
            "task_type": m.task_type, "confidence_threshold": m.confidence_threshold,
            "overlap_threshold": m.overlap_threshold, "config_revision": m.config_revision}


def _run_inference(db, model: ModelConfig, media: MediaAsset, image_bytes: bytes, frame: VideoFrame | None,
                   job_id: str | None) -> tuple[InferenceRun, list[Annotation]]:
    existing = db.execute(select(InferenceRun).where(
        InferenceRun.model_config_id == model.id, InferenceRun.media_id == media.id,
        InferenceRun.frame_id == (frame.id if frame else None), InferenceRun.status == "succeeded",
    )).scalars().all()
    for run in existing:
        if run.model_snapshot.get("config_revision") == model.config_revision:
            anns = db.execute(select(Annotation).where(Annotation.inference_run_id == run.id)).scalars().all()
            return run, list(anns)  # idempotent: identical model configuration already applied
    integ = db.get(Integration, model.integration_id)
    run = InferenceRun(model_config_id=model.id, media_id=media.id, frame_id=frame.id if frame else None,
                       status="running", model_snapshot=_snapshot(model), started_at=utcnow(), job_id=job_id)
    db.add(run)
    db.flush()
    result = infer(integ, model, image_bytes)
    run.finished_at = utcnow()
    run.duration_ms = result.duration_ms
    run.http_status = result.http_status
    run.image_width, run.image_height = result.image_width, result.image_height
    if result.raw:
        run.response_sha256 = sha256_hex(result.raw)
        run.raw_response_enc = encrypt_secret(result.raw.decode("utf-8", "replace"))
    model.last_test_at = utcnow()
    if not result.ok:
        run.status = "failed"
        run.error = result.message
        model.status = "auth_failed" if result.status == "auth_failed" else "failed"
        model.last_error = result.message
        integ.connection_status = "auth_failed" if result.status == "auth_failed" else integ.connection_status
        return run, []
    run.status = "succeeded"
    run.prediction_count = len(result.predictions)
    model.status = "operational"
    model.last_error = None
    integ.connection_status = "operational"
    integ.last_success_at = utcnow()
    anns = []
    for p in (_workflow_predictions(result) if model.task_type == "workflow" else result.predictions):
        a = Annotation(media_id=media.id, frame_id=frame.id if frame else None, inference_run_id=run.id, source="model",
                       label=p.label[:120], confidence=p.confidence, geometry_type=p.geometry_type, geometry=p.geometry,
                       timestamp_s=frame.timestamp_s if frame else None)
        db.add(a)
        anns.append(a)
    db.flush()
    return run, anns


def _workflow_predictions(result) -> list[Prediction]:
    """Keep what an officer can use from a multi-output workflow: located marks, the condition assessment and any text."""
    a = summarize(result.predictions)
    area = (result.image_width or 0) * (result.image_height or 0)
    out = []
    for d in a["detections"]:
        x0, y0, x1, y1 = d["box"]
        if d["kind"] == "image_region" and (not area or (x1 - x0) * (y1 - y0) > 0.9 * area):
            continue  # the whole frame is the photograph
        out.append(Prediction(d["label"], d["confidence"], "bbox", {"x0": x0, "y0": y0, "x1": x1, "y1": y1, "output": d["kind"]}))
    text = assessment_text(a["assessment"])
    if text:
        out.append(Prediction("Condition assessment", None, "text", {"text": text, "assessment": a["assessment"]}))
    if a["text"].strip():
        out.append(Prediction("Text in image", None, "text", {"text": a["text"][:5000]}))
    return out


def _assessment_findings(db, media: MediaAsset, model: ModelConfig, anns: list[Annotation]) -> int:
    """One finding per issue the AI condition assessment describes (deduplicated across video frames)."""
    inspection = db.get(InspectionCase, media.inspection_id)
    created = 0
    for a in anns:
        for f in (a.geometry or {}).get("assessment", {}).get("findings", []):
            issue = f.get("issue") or "Possible defect"
            origin = f"wf:{model.id}:{model.config_revision}:{media.id}:{hashlib.sha1(issue.lower().encode()).hexdigest()[:12]}"
            if created >= 10 or db.execute(select(Finding.id).where(Finding.origin_key == origin)).first():
                continue
            when = f" at {_fmt_ts(a.timestamp_s)}" if a.timestamp_s is not None else ""
            desc = " ".join(x for x in [
                f"Affected: {f['object']}." if f.get("object") else "",
                f"Where{when}: {f['where']}." if f.get("where") else "",
                f"Seen: {f['evidence']}" if f.get("evidence") else "",
                f"Certainty: {f['certainty']}." if f.get("certainty") else "",
                f"Verify: {f['verify']}" if f.get("verify") else "",
                "Automated image analysis is not proof of non-compliance; officer review and physical verification are required.",
            ] if x)
            db.add(Finding(
                institution_id=media.institution_id, inspection_id=media.inspection_id, case_id=inspection.case_id if inspection else None,
                media_id=media.id, source="model_detection", category="potential_defect",
                title=f"Possible issue: {issue}"[:300], description=desc[:4000], severity="not_assessed",
                severity_basis="Severity is assigned by the officer after review.", status="open", origin_key=origin,
                requires_physical_verification=True,
                evidence=[{"type": "media", "media_id": media.id}, {"type": "annotation", "annotation_id": a.id, "frame_id": a.frame_id,
                                                                    "timestamp_s": a.timestamp_s}],
            ))
            created += 1
    return created


def _fmt_ts(t: float | None) -> str:
    if t is None:
        return ""
    m, s = divmod(t, 60)
    return f"{int(m):02d}:{s:05.2f}"


def _create_findings(db, media: MediaAsset, model: ModelConfig, anns: list[Annotation]) -> int:
    """Findings only for classes the officer configured as reportable (class_rules[label].finding)."""
    if model.task_type == "workflow":
        return _assessment_findings(db, media, model, anns)
    inspection = db.get(InspectionCase, media.inspection_id)
    rules = model.class_rules or {}
    groups: dict[tuple, list[Annotation]] = {}
    for a in anns:
        rule = rules.get(a.label) or {}
        if not rule.get("finding"):
            continue
        if a.confidence is not None and a.confidence < float(rule.get("min_confidence", model.confidence_threshold)):
            continue
        key = (a.label, a.track_id) if a.frame_id else (a.label, a.id)
        groups.setdefault(key, []).append(a)
    created = 0
    for (label, _k), items in groups.items():
        rule = rules.get(label) or {}
        times = sorted(a.timestamp_s for a in items if a.timestamp_s is not None)
        span = f" between {_fmt_ts(times[0])} and {_fmt_ts(times[-1])}" if times else ""
        best = max((a.confidence or 0) for a in items)
        severity = rule.get("severity") if rule.get("severity") in {"low", "medium", "high", "critical"} else "not_assessed"
        origin = f"det:{model.id}:{model.config_revision}:{media.id}:{label}:{items[0].track_id if items[0].frame_id else items[0].id}"
        if db.execute(select(Finding.id).where(Finding.origin_key == origin)).first():
            continue
        criterion_id = None
        if rule.get("criterion_code"):
            from ..models import InspectionCriterion

            crit = db.execute(select(InspectionCriterion).where(InspectionCriterion.code == rule["criterion_code"])).scalar_one_or_none()
            criterion_id = crit.id if crit else None
        db.add(Finding(
            institution_id=media.institution_id, inspection_id=media.inspection_id, case_id=inspection.case_id if inspection else None,
            media_id=media.id, criterion_id=criterion_id, source="model_detection", category="potential_defect",
            title=f"Potential issue detected: {label}"[:300],
            description=(f"Automated image analysis detected "
                         f"'{label}' in {len(items)} {'frame(s)' if media.kind == 'video' else 'location(s)'}{span}, highest confidence "
                         f"{best:.2f}. A model detection is not proof of non-compliance and requires officer review"
                         + (" and physical verification." if rule.get("requires_physical_verification", True) else ".")),
            severity=severity,
            severity_basis=(f"Configured severity rule for '{label}'." if severity != "not_assessed"
                            else "No severity rule configured for this class."),
            status="open", origin_key=origin, requires_physical_verification=bool(rule.get("requires_physical_verification", True)),
            evidence=[{"type": "media", "media_id": media.id}] + [
                {"type": "annotation", "annotation_id": a.id, "frame_id": a.frame_id, "timestamp_s": a.timestamp_s} for a in items[:50]
            ],
        ))
        created += 1
    return created


@handler("analyze_image")
def analyze_image(ctx: JobContext) -> dict:
    with session_scope() as db:
        media = db.get(MediaAsset, ctx.payload["media_id"])
        if media is None:
            raise PermanentJobError("Media no longer exists.")
        models = applicable_models(db, media, ctx.payload.get("model_ids"))
        if not models:
            media.processing_status = "no_model"
            media.processing_error = "No enabled inspection model applies to this image; it is stored for manual review."
            return {"models": 0}
        media.processing_status = "processing"
        key = media.storage_key
    data = storage.get(key)
    total_preds, failures, findings = 0, [], 0
    with session_scope() as db:
        media = db.get(MediaAsset, ctx.payload["media_id"])
        for i, m in enumerate(applicable_models(db, media, ctx.payload.get("model_ids"))):
            ctx.progress(i / max(1, len(models)), f"Analysing image ({i + 1} of {len(models)})")
            run, anns = _run_inference(db, m, media, data, None, ctx.job_id)
            if run.status != "succeeded":
                failures.append(f"analysis {len(failures) + 1}: {run.error}")
                continue
            total_preds += len(anns)
            findings += _create_findings(db, media, m, anns)
            audit.record(db, "inspection.inference", entity_type="media", entity_id=media.id, institution_id=media.institution_id,
                         summary=f"Automated analysis returned {len(anns)} result(s)", outcome=run.status,
                         model_info=_snapshot(m), after={"inference_run_id": run.id, "predictions": len(anns)})
        media.processing_status = "completed" if not failures else ("failed" if len(failures) == len(models) else "partial")
        media.processing_error = "; ".join(failures)[:2000] or None
        if ctx.created_by_id:
            notify(db, ctx.created_by_id, "inspection_processed", f"Image analysed: {media.original_filename}",
                   f"{total_preds} prediction(s); {findings} finding(s) for review." + (f" Failures: {len(failures)}." if failures else ""),
                   f"/app/inspections/media/{media.id}")
    if failures and len(failures) == len(models):
        raise PermanentJobError("All model requests failed: " + "; ".join(failures)[:500])
    return {"predictions": total_preds, "findings": findings, "failures": failures}


@handler("process_video")
def process_video(ctx: JobContext) -> dict:
    s = get_settings()
    with session_scope() as db:
        media = db.get(MediaAsset, ctx.payload["media_id"])
        if media is None:
            raise PermanentJobError("Media no longer exists.")
        interval = float(ctx.payload.get("sample_interval_s") or media.sample_interval_s or s.video_default_sample_seconds)
        media.sample_interval_s = interval
        media.processing_status = "processing"
        key, duration = media.storage_key, media.duration_s
        have_frames = db.execute(select(VideoFrame.id).where(VideoFrame.media_id == media.id)).first() is not None

    if not have_frames:
        ctx.progress(0.02, "Extracting frames", force=True)
        with storage.plaintext_tempfile(key, suffix=".video") as src, storage.temp_directory() as out_dir:
            frames = sample_frames(src, out_dir, interval, s.video_max_frames)
            keys = [(f, storage.put(f.path.read_bytes(), "frames")) for f in frames]
            sizes = []
            from PIL import Image

            for f, _k in keys:
                with Image.open(f.path) as im:
                    sizes.append(im.size)
        with session_scope() as db:
            media = db.get(MediaAsset, ctx.payload["media_id"])
            for (f, k), (w, h) in zip(keys, sizes):
                db.add(VideoFrame(media_id=media.id, seq=f.seq, timestamp_s=f.timestamp_s, storage_key=k, width=w, height=h))
            media.frames_extracted = len(keys)
            exp = expected_samples(duration, interval)
            media.frames_skipped = max(0, (exp or len(keys)) - len(keys))  # samples beyond the frame cap
            if keys:
                media.preview_key = keys[0][1]
            audit.record(db, "inspection.frames_extracted", entity_type="media", entity_id=media.id,
                         institution_id=media.institution_id,
                         summary=f"Extracted {len(keys)} frames every {interval}s", after={"frames": len(keys), "interval_s": interval})

    with session_scope() as db:
        media = db.get(MediaAsset, ctx.payload["media_id"])
        models = applicable_models(db, media, ctx.payload.get("model_ids"))
        frames = db.execute(select(VideoFrame).where(VideoFrame.media_id == media.id).order_by(VideoFrame.seq)).scalars().all()
        frame_info = [(fr.id, fr.storage_key, fr.status) for fr in frames]
        if not models:
            media.processing_status = "no_model"
            media.processing_error = ("Frames were extracted with timestamps for manual review. No enabled inspection model "
                                      "applies, so no automated analysis was performed.")
            return {"frames": len(frames), "models": 0}
        model_ids = [m.id for m in models]

    processed = failed = 0
    errors: list[str] = []
    for idx, (frame_id, fkey, _status) in enumerate(frame_info):
        ctx.progress(0.1 + 0.85 * idx / max(1, len(frame_info)), f"Analysing frame {idx + 1} of {len(frame_info)}")
        img = storage.get(fkey)
        with session_scope() as db:
            media = db.get(MediaAsset, ctx.payload["media_id"])
            frame = db.get(VideoFrame, frame_id)
            ok_any = False
            for m in db.execute(select(ModelConfig).where(ModelConfig.id.in_(model_ids))).scalars():
                run, _anns = _run_inference(db, m, media, img, frame, ctx.job_id)
                if run.status == "succeeded":
                    ok_any = True
                else:
                    errors.append(f"frame {frame.seq}: {run.error}")
            frame.status = "analyzed" if ok_any else "failed"
            frame.error = None if ok_any else (errors[-1] if errors else "Inference failed")[:500]
            processed += ok_any
            failed += not ok_any

    with session_scope() as db:
        media = db.get(MediaAsset, ctx.payload["media_id"])
        findings = 0
        for m in db.execute(select(ModelConfig).where(ModelConfig.id.in_(model_ids))).scalars():
            anns = db.execute(select(Annotation).join(VideoFrame, Annotation.frame_id == VideoFrame.id).where(
                Annotation.media_id == media.id, Annotation.source == "model",
                Annotation.inference_run_id.in_(select(InferenceRun.id).where(InferenceRun.model_config_id == m.id)),
            ).order_by(VideoFrame.seq)).scalars().all()
            by_frame: dict[str, list[dict]] = {}
            for a in anns:
                box = a.geometry if a.geometry_type == "bbox" else a.geometry.get("bbox")
                if box:
                    by_frame.setdefault(a.frame_id, []).append({"ann": a, "label": a.label, "box": box, "confidence": a.confidence})
            ordered = [by_frame.get(fid, []) for fid, _k, _s in frame_info]
            assign_tracks(ordered)
            for dets in ordered:
                for d in dets:
                    d["ann"].track_id = d["track_id"]
            db.flush()
            findings += _create_findings(db, media, m, anns)
            audit.record(db, "inspection.inference", entity_type="media", entity_id=media.id, institution_id=media.institution_id,
                         summary=f"Automated analysis of {processed} sampled frames", model_info=_snapshot(m),
                         after={"frames_processed": processed, "frames_failed": failed, "annotations": len(anns)})
        media.frames_processed = processed
        media.frames_failed = failed
        media.processing_status = "completed" if failed == 0 else ("failed" if processed == 0 else "partial")
        media.processing_error = ("; ".join(errors[:5]) + (f" (+{len(errors) - 5} more)" if len(errors) > 5 else ""))[:2000] or None
        if ctx.created_by_id:
            notify(db, ctx.created_by_id, "inspection_processed", f"Video analysed: {media.original_filename}",
                   f"{processed} of {len(frame_info)} sampled frames analysed; {findings} finding(s) for review.",
                   f"/app/inspections/media/{media.id}")
    if processed == 0 and frame_info:
        raise PermanentJobError("No sampled frame could be analysed: " + "; ".join(errors[:3])[:500])
    return {"frames": len(frame_info), "processed": processed, "failed": failed, "findings": findings}


def _on_media_failure(ctx: JobContext, message: str) -> None:
    with session_scope() as db:
        media = db.get(MediaAsset, ctx.payload.get("media_id"))
        if media is None:
            return
        media.processing_status = "failed"
        media.processing_error = message[:2000]
        audit.record(db, "inspection.processing_failed", entity_type="media", entity_id=media.id,
                     institution_id=media.institution_id, outcome="failed", summary=message[:500])


analyze_image.on_final_failure = _on_media_failure  # type: ignore[attr-defined]
process_video.on_final_failure = _on_media_failure  # type: ignore[attr-defined]
