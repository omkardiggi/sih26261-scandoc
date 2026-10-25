"""Inspection & Analysis: inspection cases, media evidence, annotations, checklist, findings, corrective actions."""

from __future__ import annotations

from datetime import date, datetime

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import audit, storage
from .. import serializers as ser
from ..config import get_settings
from ..db import get_db
from ..deps import Auth, require_officer
from ..documents.upload import UploadRejected, safe_filename
from ..inspection.media import validate_image, validate_video
from ..integrations.clamav import scan_bytes
from ..jobs import enqueue
from ..models import (
    Annotation,
    CorrectiveAction,
    Document,
    Finding,
    InferenceRun,
    InspectionAssessment,
    InspectionCase,
    InspectionCriterion,
    Institution,
    Job,
    MediaAsset,
    ModelConfig,
    VerificationCase,
    VideoFrame,
    utcnow,
)
from ..security.crypto import sha256_hex
from .common import file_url, get_or_404, like, page_params, paginate
from .verifier import next_reference

router = APIRouter(prefix="/api", tags=["inspections"])

FACILITY_CATEGORIES = ["classroom", "laboratory", "computer_laboratory", "library", "workshop", "equipment", "sanitation",
                       "accessibility", "fire_safety", "building_exterior", "building_interior", "hostel", "other"]
INSPECTION_STATUSES = ["open", "in_progress", "under_review", "completed"]
ASSESSMENT_STATUSES = ["not_started", "in_review", "satisfactory", "issue_found", "requires_physical_verification", "not_assessable"]


def _insp(db: Session, i: InspectionCase, *, detail: bool = False) -> dict:
    inst = db.get(Institution, i.institution_id)
    media_count = db.execute(select(func.count()).select_from(MediaAsset).where(MediaAsset.inspection_id == i.id)).scalar_one()
    open_findings = db.execute(select(func.count()).select_from(Finding).where(Finding.inspection_id == i.id,
                                                                                Finding.status.in_(("open", "under_review")))).scalar_one()
    out = {"id": i.id, "reference": i.reference, "institution_id": i.institution_id, "institution_name": inst.name if inst else None,
           "case_id": i.case_id, "title": i.title, "inspection_type": i.inspection_type, "inspection_date": ser.iso(i.inspection_date),
           "location": i.location, "status": i.status, "notes": i.notes, "created_by_id": i.created_by_id,
           "created_at": ser.iso(i.created_at), "updated_at": ser.iso(i.updated_at), "completed_at": ser.iso(i.completed_at),
           "media_count": media_count, "open_findings": open_findings}
    return out


def _media(m: MediaAsset, officer_id: str, *, job: Job | None = None) -> dict:
    return {"id": m.id, "inspection_id": m.inspection_id, "institution_id": m.institution_id, "kind": m.kind,
            "facility_category": m.facility_category, "caption": m.caption, "original_filename": m.original_filename,
            "detected_type": m.detected_type, "size_bytes": m.size_bytes, "sha256": m.sha256, "width": m.width, "height": m.height,
            "duration_s": m.duration_s, "fps": m.fps, "codec": m.codec, "captured_at": m.captured_at, "exif": m.exif,
            "duplicate_of_id": m.duplicate_of_id, "uploaded_by_id": m.uploaded_by_id, "uploaded_at": ser.iso(m.uploaded_at),
            "processing_status": m.processing_status, "processing_error": m.processing_error,
            "sample_interval_s": m.sample_interval_s, "frames_extracted": m.frames_extracted, "frames_processed": m.frames_processed,
            "frames_failed": m.frames_failed, "frames_skipped": m.frames_skipped,
            "url": file_url("media", m.id, officer_id),
            "thumbnail_url": file_url("media", m.id, officer_id) if m.kind == "image" else None,
            "job": ser.job(job) if job else None}


class InspectionIn(BaseModel):
    institution_id: str
    title: str = Field(min_length=3, max_length=300)
    case_id: str | None = None
    inspection_type: str = Field(default="evidence_review", max_length=60)
    inspection_date: date | None = None
    location: str | None = Field(default=None, max_length=300)
    notes: str | None = Field(default=None, max_length=10000)


class InspectionPatch(BaseModel):
    title: str | None = Field(default=None, max_length=300)
    status: str | None = None
    inspection_date: date | None = None
    location: str | None = Field(default=None, max_length=300)
    notes: str | None = Field(default=None, max_length=10000)
    case_id: str | None = None
    reason: str | None = Field(default=None, max_length=2000)


@router.get("/inspections/meta")
def inspection_meta(auth: Auth = Depends(require_officer)) -> dict:
    return {"facility_categories": FACILITY_CATEGORIES, "statuses": INSPECTION_STATUSES, "assessment_statuses": ASSESSMENT_STATUSES,
            "default_sample_interval_s": get_settings().video_default_sample_seconds,
            "max_video_frames": get_settings().video_max_frames}


@router.get("/inspections")
def list_inspections(q: str | None = None, institution_id: str | None = None, status: str | None = None,
                     pg: tuple[int, int] = Depends(page_params), auth: Auth = Depends(require_officer), db: Session = Depends(get_db)) -> dict:
    stmt = select(InspectionCase).join(Institution, Institution.id == InspectionCase.institution_id)
    if q:
        stmt = stmt.where(or_(InspectionCase.title.ilike(like(q)), InspectionCase.reference.ilike(like(q)), Institution.name.ilike(like(q))))
    if institution_id:
        stmt = stmt.where(InspectionCase.institution_id == institution_id)
    if status:
        stmt = stmt.where(InspectionCase.status == status)
    rows, total = paginate(db, stmt.order_by(InspectionCase.created_at.desc()), *pg)
    return ser.page_of([_insp(db, i) for i in rows], total, *pg)


@router.post("/inspections", status_code=201)
def create_inspection(body: InspectionIn, auth: Auth = Depends(require_officer), db: Session = Depends(get_db)) -> dict:
    inst = get_or_404(db, Institution, body.institution_id, "Institution")
    if body.case_id:
        c = get_or_404(db, VerificationCase, body.case_id, "Case")
        if c.institution_id != inst.id:
            raise HTTPException(422, "The case belongs to a different institution.")
    for _ in range(3):
        i = InspectionCase(reference=next_reference(db, InspectionCase, "IN"), institution_id=inst.id, case_id=body.case_id,
                           title=body.title, inspection_type=body.inspection_type,
                           inspection_date=datetime.combine(body.inspection_date, datetime.min.time()) if body.inspection_date else None,
                           location=body.location, notes=body.notes, created_by_id=auth.officer.id)
        try:
            with db.begin_nested():
                db.add(i)
                db.flush()
            break
        except IntegrityError:
            continue
    audit.record(db, "inspection.created", officer=auth.officer, entity_type="inspection", entity_id=i.id, institution_id=inst.id,
                 case_id=body.case_id, after=_insp(db, i), ctx=auth.ctx)
    db.commit()
    return _insp(db, i)


@router.get("/inspections/{insp_id}")
def get_inspection(insp_id: str, auth: Auth = Depends(require_officer), db: Session = Depends(get_db)) -> dict:
    i = get_or_404(db, InspectionCase, insp_id, "Inspection")
    media = db.execute(select(MediaAsset).where(MediaAsset.inspection_id == i.id).order_by(MediaAsset.uploaded_at)).scalars().all()
    jobs = {j.entity_id: j for j in db.execute(select(Job).where(Job.entity_type == "media", Job.entity_id.in_([m.id for m in media]))
                                               .order_by(Job.created_at)).scalars()} if media else {}
    findings = db.execute(select(Finding).where(Finding.inspection_id == i.id).order_by(Finding.created_at.desc())).scalars().all()
    criteria = db.execute(select(InspectionCriterion).where(InspectionCriterion.active.is_(True)).order_by(InspectionCriterion.code)).scalars().all()
    assessments = {a.criterion_id: a for a in db.execute(select(InspectionAssessment).where(InspectionAssessment.inspection_id == i.id)).scalars()}
    cas = db.execute(select(CorrectiveAction).where(CorrectiveAction.inspection_id == i.id).order_by(CorrectiveAction.created_at.desc())).scalars().all()
    frame_previews = {}
    for m in media:
        if m.kind == "video":
            fr = db.execute(select(VideoFrame).where(VideoFrame.media_id == m.id).order_by(VideoFrame.seq).limit(1)).scalar_one_or_none()
            if fr:
                frame_previews[m.id] = file_url("frame", fr.id, auth.officer.id)
    out = _insp(db, i)
    out["media"] = [_media(m, auth.officer.id, job=jobs.get(m.id)) | ({"thumbnail_url": frame_previews.get(m.id)} if m.kind == "video" else {})
                    for m in media]
    out["findings"] = [ser.finding(f) for f in findings]
    out["checklist"] = [{"criterion": _crit(c), "assessment": _assess(assessments.get(c.id)) if assessments.get(c.id) else None} for c in criteria]
    out["corrective_actions"] = [ser.corrective(c) for c in cas]
    return out


@router.patch("/inspections/{insp_id}")
def update_inspection(insp_id: str, body: InspectionPatch, auth: Auth = Depends(require_officer), db: Session = Depends(get_db)) -> dict:
    i = get_or_404(db, InspectionCase, insp_id, "Inspection")
    before = _insp(db, i)
    data = body.model_dump(exclude_unset=True, exclude={"reason"})
    if "status" in data and data["status"] not in INSPECTION_STATUSES:
        raise HTTPException(422, "Unknown status.")
    if "inspection_date" in data:
        data["inspection_date"] = datetime.combine(data["inspection_date"], datetime.min.time()) if data["inspection_date"] else None
    for k, v in data.items():
        setattr(i, k, v)
    if data.get("status") == "completed":
        i.completed_at = utcnow()
    audit.record(db, "inspection.updated", officer=auth.officer, entity_type="inspection", entity_id=i.id, institution_id=i.institution_id,
                 case_id=i.case_id, before=before, after=_insp(db, i), reason=body.reason, ctx=auth.ctx)
    db.commit()
    return _insp(db, i)


@router.post("/inspections/{insp_id}/media", status_code=201)
async def upload_media(insp_id: str, file: UploadFile = File(...), facility_category: str | None = Form(None),
                       caption: str | None = Form(None, max_length=500), sample_interval_s: float | None = Form(None),
                       auth: Auth = Depends(require_officer), db: Session = Depends(get_db)) -> dict:
    s = get_settings()
    insp = get_or_404(db, InspectionCase, insp_id, "Inspection")
    if facility_category and facility_category not in FACILITY_CATEGORIES:
        raise HTTPException(422, "Unknown facility category.")
    if sample_interval_s is not None and not (0.2 <= sample_interval_s <= 600):
        raise HTTPException(422, "Sampling interval must be between 0.2 and 600 seconds.")
    filename = safe_filename(file.filename)
    limit = s.max_video_mb * 1024 * 1024
    data = await file.read(limit + 1)
    if len(data) > limit:
        raise HTTPException(413, "The file is too large.")
    from ..documents.upload import IMAGE_EXTENSIONS, extension_of

    try:
        if extension_of(filename) in IMAGE_EXTENSIONS:
            info = validate_image(filename, data)
        else:
            with storage.plaintext_tempfile(data=data, suffix=".video") as tmp:
                info = validate_video(filename, data, tmp)
    except UploadRejected as exc:
        audit.record(db, "media.upload_rejected", officer=auth.officer, entity_type="inspection", entity_id=insp.id,
                     institution_id=insp.institution_id, summary=str(exc), after={"filename": filename}, ctx=auth.ctx)
        db.commit()
        raise HTTPException(422, str(exc)) from exc
    mal, detail = scan_bytes(data)
    if mal == "infected":
        raise HTTPException(422, f"The file was rejected by the malware scanner ({detail}).")
    if s.malware_scan_required and mal != "clean":
        raise HTTPException(503, f"Uploads require a malware scan, which is unavailable: {detail}")
    sha = sha256_hex(data)
    dup = db.execute(select(MediaAsset).where(MediaAsset.sha256 == sha).order_by(MediaAsset.uploaded_at)).scalars().first()
    key = storage.put(data, "media")
    m = MediaAsset(inspection_id=insp.id, institution_id=insp.institution_id, kind=info.kind, facility_category=facility_category,
                   caption=caption, original_filename=filename, detected_type=info.detected_type, size_bytes=len(data), sha256=sha,
                   storage_key=key, width=info.width, height=info.height, duration_s=info.duration_s, fps=info.fps, codec=info.codec,
                   captured_at=info.captured_at, exif=info.exif or None, duplicate_of_id=dup.id if dup else None,
                   uploaded_by_id=auth.officer.id, processing_status="queued",
                   sample_interval_s=sample_interval_s or (s.video_default_sample_seconds if info.kind == "video" else None))
    db.add(m)
    db.flush()
    job = enqueue(db, "process_video" if m.kind == "video" else "analyze_image", {"media_id": m.id, "sample_interval_s": m.sample_interval_s},
                  idempotency_key=f"media:{m.id}:1", entity_type="media", entity_id=m.id, institution_id=m.institution_id,
                  created_by_id=auth.officer.id, priority=80)
    audit.record(db, "media.uploaded", officer=auth.officer, entity_type="media", entity_id=m.id, institution_id=m.institution_id,
                 case_id=insp.case_id, after={"filename": filename, "kind": m.kind, "sha256": sha, "size_bytes": len(data),
                                              "malware_scan": mal, "duplicate_of": m.duplicate_of_id},
                 evidence=[{"type": "media", "media_id": m.id, "sha256": sha}], ctx=auth.ctx)
    if insp.status == "open":
        insp.status = "in_progress"
    db.commit()
    return _media(m, auth.officer.id, job=job)


def _ann(a: Annotation) -> dict:
    return {"id": a.id, "media_id": a.media_id, "frame_id": a.frame_id, "inference_run_id": a.inference_run_id, "source": a.source,
            "label": a.label, "confidence": a.confidence, "geometry_type": a.geometry_type, "geometry": a.geometry,
            "timestamp_s": a.timestamp_s, "track_id": a.track_id, "original_annotation_id": a.original_annotation_id,
            "review_status": a.review_status, "review_note": a.review_note, "reviewed_by_id": a.reviewed_by_id,
            "reviewed_at": ser.iso(a.reviewed_at), "created_at": ser.iso(a.created_at)}


@router.get("/media/{media_id}")
def get_media(media_id: str, auth: Auth = Depends(require_officer), db: Session = Depends(get_db)) -> dict:
    m = get_or_404(db, MediaAsset, media_id, "Media")
    insp = db.get(InspectionCase, m.inspection_id)
    inst = db.get(Institution, m.institution_id)
    frames = db.execute(select(VideoFrame).where(VideoFrame.media_id == m.id).order_by(VideoFrame.seq)).scalars().all()
    runs = db.execute(select(InferenceRun).where(InferenceRun.media_id == m.id).order_by(InferenceRun.created_at)).scalars().all()
    anns = db.execute(select(Annotation).where(Annotation.media_id == m.id).order_by(Annotation.created_at)).scalars().all()
    findings = db.execute(select(Finding).where(Finding.media_id == m.id)).scalars().all()
    job = db.execute(select(Job).where(Job.entity_type == "media", Job.entity_id == m.id).order_by(Job.created_at.desc())).scalars().first()
    models = {mc.id: mc for mc in db.execute(select(ModelConfig)).scalars()}
    return {
        "media": _media(m, auth.officer.id, job=job),
        "inspection": {"id": insp.id, "reference": insp.reference, "title": insp.title},
        "institution": {"id": inst.id, "name": inst.name},
        "frames": [{"id": f.id, "seq": f.seq, "timestamp_s": f.timestamp_s, "width": f.width, "height": f.height, "status": f.status,
                    "error": f.error, "url": file_url("frame", f.id, auth.officer.id)} for f in frames],
        # Which models run is confidential: runs are reported without model names, projects or versions.
        "inference_runs": [{"id": r.id, "frame_id": r.frame_id, "status": r.status, "error": "Automated analysis failed" if r.error else None,
                            "prediction_count": r.prediction_count, "started_at": ser.iso(r.started_at),
                            "finished_at": ser.iso(r.finished_at)} for r in runs],
        "annotations": [_ann(a) for a in anns],
        "findings": [ser.finding(f) for f in findings],
    }


@router.post("/media/{media_id}/analyze", status_code=202)
def analyze_media(media_id: str, body: dict, auth: Auth = Depends(require_officer), db: Session = Depends(get_db)) -> dict:
    m = get_or_404(db, MediaAsset, media_id, "Media")
    model_ids = [str(x) for x in (body.get("model_ids") or [])][:20]
    n = db.execute(select(func.count()).select_from(Job).where(Job.entity_type == "media", Job.entity_id == m.id)).scalar_one()
    payload = {"media_id": m.id, "model_ids": model_ids or None, "sample_interval_s": m.sample_interval_s}
    job = enqueue(db, "process_video" if m.kind == "video" else "analyze_image", payload, idempotency_key=f"media:{m.id}:{n + 1}",
                  entity_type="media", entity_id=m.id, institution_id=m.institution_id, created_by_id=auth.officer.id, priority=80)
    m.processing_status = "queued"
    audit.record(db, "media.analysis_requested", officer=auth.officer, entity_type="media", entity_id=m.id,
                 institution_id=m.institution_id, after={"job_id": job.id, "model_ids": model_ids}, ctx=auth.ctx)
    db.commit()
    return {"job": ser.job(job)}


class AnnotationIn(BaseModel):
    label: str = Field(min_length=1, max_length=120)
    geometry_type: str = Field(pattern="^(bbox|polygon)$")
    geometry: dict
    frame_id: str | None = None
    note: str | None = Field(default=None, max_length=4000)


def _check_geometry(gt: str, g: dict) -> dict:
    try:
        if gt == "bbox":
            box = {k: float(g[k]) for k in ("x0", "y0", "x1", "y1")}
            if box["x1"] <= box["x0"] or box["y1"] <= box["y0"]:
                raise ValueError
            return box
        pts = [[float(x), float(y)] for x, y in g["points"]]
        if len(pts) < 3:
            raise ValueError
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        return {"points": pts, "bbox": {"x0": min(xs), "y0": min(ys), "x1": max(xs), "y1": max(ys)}}
    except (KeyError, TypeError, ValueError):
        raise HTTPException(422, "Invalid annotation geometry.") from None


@router.post("/media/{media_id}/annotations", status_code=201)
def add_annotation(media_id: str, body: AnnotationIn, auth: Auth = Depends(require_officer), db: Session = Depends(get_db)) -> dict:
    m = get_or_404(db, MediaAsset, media_id, "Media")
    frame = None
    if body.frame_id:
        frame = get_or_404(db, VideoFrame, body.frame_id, "Frame")
        if frame.media_id != m.id:
            raise HTTPException(422, "Frame does not belong to this media.")
    a = Annotation(media_id=m.id, frame_id=frame.id if frame else None, source="officer", label=body.label,
                   geometry_type=body.geometry_type, geometry=_check_geometry(body.geometry_type, body.geometry),
                   timestamp_s=frame.timestamp_s if frame else None, review_status="confirmed", review_note=body.note,
                   reviewed_by_id=auth.officer.id, reviewed_at=utcnow(), created_by_id=auth.officer.id)
    db.add(a)
    db.flush()
    audit.record(db, "annotation.created", officer=auth.officer, entity_type="annotation", entity_id=a.id, institution_id=m.institution_id,
                 after=_ann(a), reason=body.note, ctx=auth.ctx)
    db.commit()
    return _ann(a)


class ReviewIn(BaseModel):
    status: str = Field(pattern="^(confirmed|rejected)$")
    note: str | None = Field(default=None, max_length=4000)


@router.post("/annotations/{ann_id}/review")
def review_annotation(ann_id: str, body: ReviewIn, auth: Auth = Depends(require_officer), db: Session = Depends(get_db)) -> dict:
    a = get_or_404(db, Annotation, ann_id, "Annotation")
    m = db.get(MediaAsset, a.media_id)
    if body.status == "rejected" and not (body.note or "").strip():
        raise HTTPException(422, "Explain why the annotation is rejected.")
    before = {"review_status": a.review_status}
    # Only review metadata changes; the model's label, geometry and confidence are never modified.
    a.review_status, a.review_note, a.reviewed_by_id, a.reviewed_at = body.status, body.note, auth.officer.id, utcnow()
    audit.record(db, "annotation.reviewed", officer=auth.officer, entity_type="annotation", entity_id=a.id, institution_id=m.institution_id,
                 before=before, after={"review_status": a.review_status}, reason=body.note, ctx=auth.ctx)
    db.commit()
    return _ann(a)


@router.post("/annotations/{ann_id}/correct", status_code=201)
def correct_annotation(ann_id: str, body: AnnotationIn, auth: Auth = Depends(require_officer), db: Session = Depends(get_db)) -> dict:
    orig = get_or_404(db, Annotation, ann_id, "Annotation")
    m = db.get(MediaAsset, orig.media_id)
    if not (body.note or "").strip():
        raise HTTPException(422, "Explain the correction.")
    new = Annotation(media_id=orig.media_id, frame_id=orig.frame_id, source="officer", label=body.label, geometry_type=body.geometry_type,
                     geometry=_check_geometry(body.geometry_type, body.geometry), timestamp_s=orig.timestamp_s, track_id=orig.track_id,
                     original_annotation_id=orig.id, review_status="confirmed", review_note=body.note, reviewed_by_id=auth.officer.id,
                     reviewed_at=utcnow(), created_by_id=auth.officer.id)
    db.add(new)
    orig.review_status, orig.reviewed_by_id, orig.reviewed_at = "corrected", auth.officer.id, utcnow()
    db.flush()
    audit.record(db, "annotation.corrected", officer=auth.officer, entity_type="annotation", entity_id=new.id, institution_id=m.institution_id,
                 before=_ann(orig), after=_ann(new), reason=body.note, ctx=auth.ctx)
    db.commit()
    return _ann(new)


# ---------------------------------------------------------------- checklist

class CriterionIn(BaseModel):
    code: str = Field(pattern=r"^[A-Za-z0-9_\-.]{2,40}$")
    title: str = Field(min_length=3, max_length=300)
    facility_category: str | None = None
    applicable_standard: str | None = Field(default=None, max_length=4000)
    official_source: str = Field(min_length=3, max_length=4000)
    guidance: str | None = Field(default=None, max_length=8000)
    requires_physical_verification: bool = True
    linked_classes: list[str] = Field(default_factory=list)
    recommended_action: str | None = Field(default=None, max_length=4000)
    active: bool = True


def _crit(c: InspectionCriterion) -> dict:
    return {"id": c.id, "code": c.code, "title": c.title, "facility_category": c.facility_category,
            "applicable_standard": c.applicable_standard, "official_source": c.official_source, "guidance": c.guidance,
            "requires_physical_verification": c.requires_physical_verification, "linked_classes": c.linked_classes,
            "recommended_action": c.recommended_action, "active": c.active}


def _assess(a: InspectionAssessment) -> dict:
    return {"id": a.id, "criterion_id": a.criterion_id, "observed_evidence": a.observed_evidence, "detected_issue": a.detected_issue,
            "severity": a.severity, "uncertainty": a.uncertainty, "evidence_refs": a.evidence_refs, "review_status": a.review_status,
            "recommended_action": a.recommended_action, "reviewed_by_id": a.reviewed_by_id, "reviewed_at": ser.iso(a.reviewed_at),
            "updated_at": ser.iso(a.updated_at)}


@router.get("/inspection-criteria")
def list_criteria(auth: Auth = Depends(require_officer), db: Session = Depends(get_db)) -> dict:
    return {"items": [_crit(c) for c in db.execute(select(InspectionCriterion).order_by(InspectionCriterion.code)).scalars()]}


@router.post("/inspection-criteria", status_code=201)
def create_criterion(body: CriterionIn, auth: Auth = Depends(require_officer), db: Session = Depends(get_db)) -> dict:
    if db.execute(select(InspectionCriterion).where(InspectionCriterion.code == body.code)).first():
        raise HTTPException(409, "A criterion with this code exists.")
    c = InspectionCriterion(**body.model_dump(), created_by_id=auth.officer.id)
    db.add(c)
    db.flush()
    audit.record(db, "criterion.created", officer=auth.officer, entity_type="criterion", entity_id=c.id, after=_crit(c), ctx=auth.ctx)
    db.commit()
    return _crit(c)


@router.patch("/inspection-criteria/{crit_id}")
def update_criterion(crit_id: str, body: CriterionIn, auth: Auth = Depends(require_officer), db: Session = Depends(get_db)) -> dict:
    c = get_or_404(db, InspectionCriterion, crit_id, "Criterion")
    before = _crit(c)
    for k, v in body.model_dump(exclude={"code"}).items():
        setattr(c, k, v)
    audit.record(db, "criterion.updated", officer=auth.officer, entity_type="criterion", entity_id=c.id, before=before, after=_crit(c), ctx=auth.ctx)
    db.commit()
    return _crit(c)


class AssessmentIn(BaseModel):
    observed_evidence: str | None = Field(default=None, max_length=8000)
    detected_issue: str | None = Field(default=None, max_length=8000)
    severity: str = Field(default="not_assessed", pattern="^(not_assessed|low|medium|high|critical)$")
    uncertainty: str | None = Field(default=None, max_length=4000)
    evidence_refs: list[dict] = Field(default_factory=list)
    review_status: str = "in_review"
    recommended_action: str | None = Field(default=None, max_length=4000)


@router.put("/inspections/{insp_id}/assessments/{crit_id}")
def save_assessment(insp_id: str, crit_id: str, body: AssessmentIn, auth: Auth = Depends(require_officer), db: Session = Depends(get_db)) -> dict:
    insp = get_or_404(db, InspectionCase, insp_id, "Inspection")
    get_or_404(db, InspectionCriterion, crit_id, "Criterion")
    if body.review_status not in ASSESSMENT_STATUSES:
        raise HTTPException(422, "Unknown review status.")
    if body.severity != "not_assessed" and not (body.observed_evidence or "").strip():
        raise HTTPException(422, "A severity requires the observed evidence it is based on.")
    a = db.execute(select(InspectionAssessment).where(InspectionAssessment.inspection_id == insp.id,
                                                      InspectionAssessment.criterion_id == crit_id)).scalar_one_or_none()
    before = _assess(a) if a else None
    if a is None:
        a = InspectionAssessment(inspection_id=insp.id, criterion_id=crit_id)
        db.add(a)
    for k, v in body.model_dump().items():
        setattr(a, k, v)
    a.reviewed_by_id, a.reviewed_at = auth.officer.id, utcnow()
    db.flush()
    audit.record(db, "assessment.saved", officer=auth.officer, entity_type="assessment", entity_id=a.id, institution_id=insp.institution_id,
                 before=before, after=_assess(a), ctx=auth.ctx)
    db.commit()
    return _assess(a)


# ---------------------------------------------------------------- findings & corrective actions

@router.get("/findings")
def list_findings(institution_id: str | None = None, case_id: str | None = None, inspection_id: str | None = None,
                  document_id: str | None = None, status: str | None = None, severity: str | None = None, source: str | None = None,
                  open_only: bool = False, pg: tuple[int, int] = Depends(page_params), auth: Auth = Depends(require_officer),
                  db: Session = Depends(get_db)) -> dict:
    stmt = select(Finding)
    for col, val in ((Finding.institution_id, institution_id), (Finding.case_id, case_id), (Finding.inspection_id, inspection_id),
                     (Finding.document_id, document_id), (Finding.status, status), (Finding.severity, severity), (Finding.source, source)):
        if val:
            stmt = stmt.where(col == val)
    if open_only:
        stmt = stmt.where(Finding.status.in_(("open", "under_review")))
    rows, total = paginate(db, stmt.order_by(Finding.created_at.desc()), *pg)
    names = dict(db.execute(select(Institution.id, Institution.name).where(Institution.id.in_({r.institution_id for r in rows}))).all()) if rows else {}
    return ser.page_of([ser.finding(f) | {"institution_name": names.get(f.institution_id)} for f in rows], total, *pg)


class FindingIn(BaseModel):
    institution_id: str
    title: str = Field(min_length=3, max_length=300)
    description: str = Field(min_length=5, max_length=10000)
    category: str = Field(default="officer_observation", max_length=60)
    severity: str = Field(default="not_assessed", pattern="^(not_assessed|info|low|medium|high|critical)$")
    severity_basis: str | None = Field(default=None, max_length=2000)
    case_id: str | None = None
    inspection_id: str | None = None
    document_id: str | None = None
    media_id: str | None = None
    criterion_id: str | None = None
    evidence: list[dict] = Field(default_factory=list)
    requires_physical_verification: bool = False


@router.post("/findings", status_code=201)
def create_finding(body: FindingIn, auth: Auth = Depends(require_officer), db: Session = Depends(get_db)) -> dict:
    get_or_404(db, Institution, body.institution_id, "Institution")
    if body.severity not in ("not_assessed", "info") and not (body.severity_basis or "").strip():
        raise HTTPException(422, "State the basis for the severity (e.g. the applicable standard).")
    f = Finding(source="officer", status="open", created_by_id=auth.officer.id, **body.model_dump(exclude={"evidence"}),
                evidence=audit.jsonable(body.evidence))
    db.add(f)
    db.flush()
    audit.record(db, "finding.created", officer=auth.officer, entity_type="finding", entity_id=f.id, institution_id=f.institution_id,
                 case_id=f.case_id, after=ser.finding(f), evidence=f.evidence, ctx=auth.ctx)
    db.commit()
    return ser.finding(f)


class FindingPatch(BaseModel):
    status: str = Field(pattern="^(open|under_review|resolved|dismissed)$")
    note: str = Field(min_length=3, max_length=8000)


@router.patch("/findings/{finding_id}")
def update_finding(finding_id: str, body: FindingPatch, auth: Auth = Depends(require_officer), db: Session = Depends(get_db)) -> dict:
    f = get_or_404(db, Finding, finding_id, "Finding")
    before = {"status": f.status}
    f.status = body.status
    if body.status in ("resolved", "dismissed"):
        f.resolved_by_id, f.resolved_at, f.resolution_note = auth.officer.id, utcnow(), body.note
    audit.record(db, "finding.status_changed", officer=auth.officer, entity_type="finding", entity_id=f.id, institution_id=f.institution_id,
                 case_id=f.case_id, before=before, after={"status": f.status}, reason=body.note, ctx=auth.ctx)
    _reevaluate(db, f.case_id, f.document_id, auth)
    db.commit()
    return ser.finding(f)


def _reevaluate(db: Session, case_id: str | None, document_id: str | None, auth: Auth) -> None:
    cid = case_id
    if not cid and document_id:
        d = db.get(Document, document_id)
        cid = d.case_id if d else None
    if cid:
        c = db.get(VerificationCase, cid)
        enqueue(db, "evaluate_case", {"case_id": cid}, entity_type="case", entity_id=cid, institution_id=c.institution_id if c else None,
                created_by_id=auth.officer.id, priority=50)


@router.get("/corrective-actions")
def list_corrective(institution_id: str | None = None, case_id: str | None = None, inspection_id: str | None = None,
                    status: str | None = None, open_only: bool = False, pg: tuple[int, int] = Depends(page_params),
                    auth: Auth = Depends(require_officer), db: Session = Depends(get_db)) -> dict:
    stmt = select(CorrectiveAction)
    for col, val in ((CorrectiveAction.institution_id, institution_id), (CorrectiveAction.case_id, case_id),
                     (CorrectiveAction.inspection_id, inspection_id), (CorrectiveAction.status, status)):
        if val:
            stmt = stmt.where(col == val)
    if open_only:
        stmt = stmt.where(CorrectiveAction.status.in_(("open", "submitted", "under_review")))
    rows, total = paginate(db, stmt.order_by(CorrectiveAction.created_at.desc()), *pg)
    names = dict(db.execute(select(Institution.id, Institution.name).where(Institution.id.in_({r.institution_id for r in rows}))).all()) if rows else {}
    return ser.page_of([ser.corrective(c) | {"institution_name": names.get(c.institution_id)} for c in rows], total, *pg)


class CorrectiveIn(BaseModel):
    institution_id: str
    title: str = Field(min_length=3, max_length=300)
    description: str | None = Field(default=None, max_length=8000)
    finding_id: str | None = None
    case_id: str | None = None
    inspection_id: str | None = None
    due_date: date | None = None


@router.post("/corrective-actions", status_code=201)
def create_corrective(body: CorrectiveIn, auth: Auth = Depends(require_officer), db: Session = Depends(get_db)) -> dict:
    get_or_404(db, Institution, body.institution_id, "Institution")
    case_id, inspection_id = body.case_id, body.inspection_id
    if body.finding_id:
        f = get_or_404(db, Finding, body.finding_id, "Finding")
        case_id = case_id or f.case_id
        inspection_id = inspection_id or f.inspection_id
    c = CorrectiveAction(institution_id=body.institution_id, finding_id=body.finding_id, case_id=case_id, inspection_id=inspection_id,
                         title=body.title, description=body.description,
                         due_date=datetime.combine(body.due_date, datetime.min.time()) if body.due_date else None,
                         created_by_id=auth.officer.id)
    db.add(c)
    db.flush()
    audit.record(db, "corrective_action.created", officer=auth.officer, entity_type="corrective_action", entity_id=c.id,
                 institution_id=c.institution_id, case_id=c.case_id, after=ser.corrective(c), ctx=auth.ctx)
    _reevaluate(db, c.case_id, None, auth)
    db.commit()
    return ser.corrective(c)


class CorrectivePatch(BaseModel):
    status: str = Field(pattern="^(open|submitted|under_review|accepted|rejected|cancelled)$")
    note: str = Field(min_length=3, max_length=8000)
    evidence_document_id: str | None = None


@router.patch("/corrective-actions/{ca_id}")
def update_corrective(ca_id: str, body: CorrectivePatch, auth: Auth = Depends(require_officer), db: Session = Depends(get_db)) -> dict:
    c = get_or_404(db, CorrectiveAction, ca_id, "Corrective action")
    if body.evidence_document_id:
        d = get_or_404(db, Document, body.evidence_document_id, "Document")
        if d.institution_id != c.institution_id:
            raise HTTPException(422, "Evidence must belong to the same institution.")
        c.evidence_document_id = d.id
    if body.status == "accepted" and not (c.evidence_document_id or body.evidence_document_id):
        raise HTTPException(422, "Accepting a corrective action requires a supporting document.")
    before = {"status": c.status}
    c.status = body.status
    c.resolution_note = body.note
    if body.status in ("accepted", "rejected", "cancelled"):
        c.closed_by_id, c.closed_at = auth.officer.id, utcnow()
    audit.record(db, "corrective_action.status_changed", officer=auth.officer, entity_type="corrective_action", entity_id=c.id,
                 institution_id=c.institution_id, case_id=c.case_id, before=before, after={"status": c.status},
                 reason=body.note, evidence=[{"type": "document", "document_id": c.evidence_document_id}] if c.evidence_document_id else None,
                 ctx=auth.ctx)
    _reevaluate(db, c.case_id, None, auth)
    db.commit()
    return ser.corrective(c)
