"""Official adapters, inference, annotations, video timestamps, jobs, audit, dashboard and reports
(acceptance criteria 10-11, 14-18, 20-23, 27-28). External services are simulated with httpx.MockTransport
inside tests only; synthetic responses never reach operational code paths."""

import io
import json
import subprocess

import httpx
from PIL import Image

from conftest import create_institution, run_jobs, synthetic_pdf, upload

SYN_STATUS_HTML = """<html><body><table>
<tr><th>1-46200000001 - AICTE APPROVAL PROCESS 2026-2027</th></tr>
<tr><td>Name of the Institute</td><td>SYNTHETIC INSTITUTE OF TECHNOLOGY</td></tr>
<tr><td>Academic Year</td><td>2026-2027</td></tr>
<tr><td>Application ID</td><td>1-46200000001</td></tr>
<tr><td>Permanent ID</td><td>1-9990001</td></tr>
<tr><td>Status</td><td>EOA Recommended by Council</td></tr>
</table><table>
<tr><th>Course Type</th><th>Level</th><th>Program</th><th>Course</th><th>Affiliating Body</th><th>Intake Approved for 2026-2027</th></tr>
<tr><td>Regular</td><td>UNDER GRADUATE</td><td>ENGINEERING</td><td>CIVIL ENGINEERING</td><td>SYNTHETIC UNIVERSITY</td><td>60</td></tr>
<tr><td>Regular</td><td>UNDER GRADUATE</td><td>ENGINEERING</td><td>MECHANICAL ENGINEERING</td><td>SYNTHETIC UNIVERSITY</td><td>90</td></tr>
</table></body></html>"""


def _transport(handler):
    from app.integrations import http as h

    h.transport_override = httpx.MockTransport(handler)


def _reset():
    from app.integrations import http as h

    h.transport_override = None


def test_qr_official_verification_matches_and_mismatches():
    from app.integrations.official import verify_via_qr
    from app.models import Integration

    integ = Integration(adapter="aicte_qr_status", config={"allowed_hosts": ["approvalstatus.aicte.gov.in"],
                                                           "path_prefix": "/api/getInstituteDetails/"}, name="qr")
    url = "https://approvalstatus.aicte.gov.in/api/getInstituteDetails/U1lOVEhFVElD"
    _transport(lambda req: httpx.Response(200, text=SYN_STATUS_HTML, headers={"content-type": "text/html"}))
    try:
        doc = {"institution_name": "Synthetic Institute of Technology", "aicte_permanent_id": "1-9990001",
               "application_id": "1-46200000001", "academic_year": "2026-27"}
        progs = [{"course": "CIVIL ENGINEERING", "level": "UNDER GRADUATE", "intake": 60},
                 {"course": "MECHANICAL ENGINEERING", "level": "UNDER GRADUATE", "intake": 90}]
        ok = verify_via_qr(integ, url, doc, progs)
        assert ok.outcome == "verified" and "EOA Recommended by Council" in ok.reason
        progs[1]["intake"] = 120
        bad = verify_via_qr(integ, url, doc, progs)
        assert bad.outcome == "mismatch" and "120" in bad.reason
        # Host not on the allowlist is never contacted
        called = []
        _transport(lambda req: called.append(1) or httpx.Response(200, text=SYN_STATUS_HTML))
        refused = verify_via_qr(integ, "https://aicte-verify.example.com/api/getInstituteDetails/x", doc, progs)
        assert refused.outcome == "unable_to_verify" and not called
        # Server errors are never treated as success
        _transport(lambda req: httpx.Response(503, text="down"))
        assert verify_via_qr(integ, url, doc, progs).outcome == "error"
        _transport(lambda req: httpx.Response(200, text="<html><body>No record</body></html>"))
        assert verify_via_qr(integ, url, doc, progs).outcome == "not_found"
    finally:
        _reset()


def test_generic_rest_adapter_and_connection_test(officer_client):
    c = officer_client
    _transport(lambda req: httpx.Response(401, json={"error": "denied"}) if "Bearer good" not in req.headers.get("authorization", "")
               else (httpx.Response(200, json={"ok": True}) if req.url.path.endswith("/health")
                     else httpx.Response(200, json={"data": {"name": "Synthetic Institute of Technology", "pid": "1-9990101"}})))
    try:
        r = c.post("/api/integrations", json={
            "name": "Synthetic University Records API", "purpose": "Synthetic test integration", "base_url": "https://records.example.test",
            "auth_method": "bearer", "claim_types": ["institution_identity"], "enabled": True,
            "config": {"test_request": {"path": "/health", "expect_json_path": "ok"},
                       "request": {"path": "/institutes/{aicte_permanent_id}"},
                       "response": {"found_path": "data", "fields": {"institution_name": "data.name", "aicte_permanent_id": "data.pid"}},
                       "match": {"institution_name": "name"}}})
        assert r.status_code == 201, r.text
        iid = r.json()["id"]
        t = c.post(f"/api/integrations/{iid}/test").json()
        assert t["ok"] is False and "credential" in t["message"].lower()  # no credential -> not operational
        assert c.put(f"/api/integrations/{iid}/secret", json={"value": "bad-key"}).status_code == 200
        t = c.post(f"/api/integrations/{iid}/test").json()
        assert t["ok"] is False and t["integration"]["connection_status"] == "auth_failed"
        c.put(f"/api/integrations/{iid}/secret", json={"value": "good"})
        t = c.post(f"/api/integrations/{iid}/test").json()
        assert t["ok"] is True and t["integration"]["connection_status"] == "operational"
        assert "good" not in json.dumps(c.get("/api/integrations").json())  # secret never returned

        from tests.test_documents import eoa_pdf

        inst = create_institution(c, aicte_permanent_id="1-9990101", name="Synthetic Institute of Technology")
        doc = upload(c, inst["id"], eoa_pdf(pid="1-9990101"), "rest.pdf").json()["document"]
        run_jobs()
        res = c.post(f"/api/documents/{doc['id']}/official-verification",
                     json={"method": "integration", "integration_id": iid, "claim_type": "institution_identity"})
        assert res.status_code == 202 and res.json()["job"]
        run_jobs()
        d = c.get(f"/api/documents/{doc['id']}").json()
        att = d["official_attempts"][0]
        assert att["outcome"] == "verified" and att["response_sha256"] and att["http_status"] == 200
        assert d["document"]["status"] in ("verified_official", "requires_manual_review")
        raw = c.get(f"/api/official-attempts/{att['id']}/response").json()
        assert "Synthetic Institute of Technology" in raw["body"]
    finally:
        _reset()


def _png(w=640, h=480, color=(180, 180, 180)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, format="PNG")
    return buf.getvalue()


def _setup_model(c, task="object_detection", rules=None):
    from app.db import session_scope
    from app.models import ModelConfig

    with session_scope() as db:  # isolate from models created by earlier tests
        for mc in db.query(ModelConfig):
            mc.enabled = False
    integ = c.get("/api/models").json()["provider"]
    c.patch(f"/api/integrations/{integ['id']}", json={**{k: integ[k] for k in ("name", "purpose", "provider", "source_owner", "base_url",
             "auth_method", "auth_header_name", "secret_env_var", "config", "claim_types", "documentation_url", "required_permissions",
             "coverage_notes", "rate_limit_notes", "privacy_notes")}, "enabled": True})
    c.put(f"/api/integrations/{integ['id']}/secret", json={"value": "synthetic-inference-key"})
    r = c.post("/api/models", json={"name": f"Synthetic {task} model", "project_id": "synthetic-project", "model_version": "1",
                                    "task_type": task, "classes": ["crack", "chair"], "enabled": True,
                                    "class_rules": rules or {"crack": {"finding": True, "severity": "medium",
                                                                       "severity_basis": "Synthetic test rule", "min_confidence": 0.5}}})
    assert r.status_code == 201, r.text
    return r.json()


def test_image_inference_annotations_match_model_output(officer_client):
    c = officer_client
    seen = {}

    def handler(req):
        seen["auth"] = req.headers.get("authorization")
        seen["path"] = req.url.path
        return httpx.Response(200, json={"image": {"width": 640, "height": 480}, "predictions": [
            {"x": 100, "y": 80, "width": 40, "height": 20, "confidence": 0.91, "class": "crack", "class_id": 0},
            {"x": 300, "y": 300, "width": 100, "height": 100, "confidence": 0.62, "class": "chair", "class_id": 1}]})

    _transport(handler)
    try:
        model = _setup_model(c)
        assert c.post(f"/api/models/{model['id']}/test").json()["ok"] is True
        inst = create_institution(c, name="Synthetic Inspection Institute")
        insp = c.post("/api/inspections", json={"institution_id": inst["id"], "title": "Synthetic inspection"}).json()
        m = c.post(f"/api/inspections/{insp['id']}/media", data={"facility_category": "classroom"},
                   files={"file": ("room.png", _png(), "image/png")})
        assert m.status_code == 201, m.text
        run_jobs()
        detail = c.get(f"/api/media/{m.json()['id']}").json()
        assert seen["auth"] == "Bearer synthetic-inference-key" and seen["path"] == "/synthetic-project/1"
        anns = sorted(detail["annotations"], key=lambda a: a["label"])
        assert [a["label"] for a in anns] == ["chair", "crack"]
        crack = anns[1]
        assert crack["geometry"] == {"x0": 80.0, "y0": 70.0, "x1": 120.0, "y1": 90.0} and crack["confidence"] == 0.91
        assert detail["inference_runs"][0]["status"] == "succeeded" and "model_snapshot" not in detail["inference_runs"][0]  # model details stay server-side
        fnd = detail["findings"]
        assert len(fnd) == 1 and fnd[0]["severity"] == "medium" and fnd[0]["requires_physical_verification"]
        assert any(e.get("annotation_id") == crack["id"] for e in fnd[0]["evidence"])
        # Officer correction keeps the original model output
        corr = c.post(f"/api/annotations/{crack['id']}/correct", json={"label": "crack", "geometry_type": "bbox",
                       "geometry": {"x0": 75, "y0": 65, "x1": 125, "y1": 95}, "note": "Synthetic: widened box"})
        assert corr.status_code == 201
        detail = c.get(f"/api/media/{m.json()['id']}").json()
        orig = next(a for a in detail["annotations"] if a["id"] == crack["id"])
        assert orig["geometry"] == crack["geometry"] and orig["review_status"] == "corrected"
        assert any(a["original_annotation_id"] == crack["id"] and a["source"] == "officer" for a in detail["annotations"])
    finally:
        _reset()


def test_inference_auth_failure_recorded_and_no_fake_detections(officer_client):
    c = officer_client
    _transport(lambda req: httpx.Response(403, json={"message": "Forbidden"}))
    try:
        model = _setup_model(c)
        res = c.post(f"/api/models/{model['id']}/test").json()
        assert res["ok"] is False and res["model"]["status"] == "auth_failed"
        inst = create_institution(c, name="Synthetic Auth Failure Institute")
        insp = c.post("/api/inspections", json={"institution_id": inst["id"], "title": "Synthetic failure"}).json()
        m = c.post(f"/api/inspections/{insp['id']}/media", files={"file": ("x.png", _png(), "image/png")}).json()
        run_jobs()
        d = c.get(f"/api/media/{m['id']}").json()
        assert d["annotations"] == [] and d["media"]["processing_status"] == "failed"
        assert all(r["status"] == "failed" for r in d["inference_runs"])
        failed = c.get("/api/jobs", params={"status": "failed"}).json()["items"]
        job = next(j for j in failed if j["entity_id"] == m["id"])
        _transport(lambda req: httpx.Response(200, json={"image": {"width": 640, "height": 480}, "predictions": []}))
        c.patch(f"/api/models/{model['id']}", json={**{k: model[k] for k in ("name", "project_id", "model_version", "task_type", "classes",
                "class_rules", "confidence_threshold", "overlap_threshold", "facility_categories", "input_formats", "evaluation")},
                "enabled": True, "description": "retry"})
        assert c.post(f"/api/jobs/{job['id']}/retry").status_code == 200
        run_jobs()
        assert c.get(f"/api/jobs/{job['id']}").json()["status"] == "succeeded"
    finally:
        _reset()


def test_video_frames_have_accurate_timestamps(officer_client, tmp_path):
    c = officer_client
    video = tmp_path / "synthetic.mp4"
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc=duration=7:size=320x240:rate=25",
                    "-pix_fmt", "yuv420p", str(video)], check=True)
    calls = []

    def handler(req):
        calls.append(1)
        n = len(calls)
        return httpx.Response(200, json={"image": {"width": 320, "height": 240}, "predictions": [
            {"x": 50 + n, "y": 50, "width": 30, "height": 30, "confidence": 0.8, "class": "crack", "class_id": 0}]})

    _transport(handler)
    try:
        _setup_model(c)
        inst = create_institution(c, name="Synthetic Video Institute")
        insp = c.post("/api/inspections", json={"institution_id": inst["id"], "title": "Synthetic video"}).json()
        m = c.post(f"/api/inspections/{insp['id']}/media", data={"sample_interval_s": "2"},
                   files={"file": ("walk.mp4", video.read_bytes(), "video/mp4")})
        assert m.status_code == 201, m.text
        run_jobs()
        d = c.get(f"/api/media/{m.json()['id']}").json()
        assert [f["timestamp_s"] for f in d["frames"]] == [0.0, 2.0, 4.0, 6.0]
        assert d["media"]["frames_extracted"] == 4 and d["media"]["frames_processed"] == 4 and d["media"]["sample_interval_s"] == 2.0
        anns = [a for a in d["annotations"] if a["source"] == "model"]
        assert sorted(a["timestamp_s"] for a in anns) == [0.0, 2.0, 4.0, 6.0]
        assert len({a["track_id"] for a in anns}) == 1  # tracked across frames
        assert len(d["findings"]) == 1 and "00:00.00" in d["findings"][0]["description"] and "00:06.00" in d["findings"][0]["description"]
    finally:
        _reset()


def test_no_model_configured_means_no_detections(officer_client):
    from app.db import session_scope
    from app.models import ModelConfig

    with session_scope() as db:
        for mc in db.query(ModelConfig):
            mc.enabled = False
    c = officer_client
    inst = create_institution(c, name="Synthetic No Model Institute")
    insp = c.post("/api/inspections", json={"institution_id": inst["id"], "title": "Synthetic no model"}).json()
    m = c.post(f"/api/inspections/{insp['id']}/media", files={"file": ("x.png", _png(), "image/png")}).json()
    run_jobs()
    d = c.get(f"/api/media/{m['id']}").json()
    assert d["media"]["processing_status"] == "no_model" and d["annotations"] == [] and d["inference_runs"] == []


def test_audit_chain_dashboard_and_reports(officer_client):
    from sqlalchemy import func, select, text

    from app.db import session_scope
    from app.models import Document, Institution, VerificationCase

    c = officer_client
    inst = create_institution(c, name="Synthetic Reporting Institute", state="Reportland")
    c.patch(f"/api/institutions/{inst['id']}", json={"changes": {"district": "New District"}, "reason": "Synthetic correction"})
    hist = c.get(f"/api/institutions/{inst['id']}/versions").json()["items"]
    assert hist and hist[0]["snapshot"]["district"] == "Sample District"  # previous version preserved
    upload(c, inst["id"], synthetic_pdf(["Synthetic audit report doc"]), "r.pdf")
    run_jobs()
    v = c.post("/api/audit/verify").json()
    assert v["ok"] is True and v["events_checked"] > 5
    with session_scope() as db:
        try:
            with db.begin_nested():
                db.execute(text("DELETE FROM audit_events"))
            blocked = False
        except Exception:  # noqa: BLE001
            blocked = True
    assert blocked, "audit events must be append-only"
    s = c.get("/api/dashboard/summary").json()["cards"]
    with session_scope() as db:
        assert s["total_institutions"] == db.execute(select(func.count()).select_from(Institution).where(Institution.is_archived.is_(False))).scalar_one()
        assert s["pending_verification_cases"] == db.execute(select(func.count()).select_from(VerificationCase)
                                                             .where(VerificationCase.status != "decision_recorded")).scalar_one()
        n_docs_state = db.execute(select(func.count()).select_from(Document).join(Institution, Institution.id == Document.institution_id)
                                  .where(Institution.state == "Reportland")).scalar_one()
    charts = c.get("/api/dashboard/charts", params={"grain": "month"}).json()
    assert {"document_outcomes", "cases_flow", "turnaround", "discrepancy_trends"} <= charts.keys()
    rep = c.get("/api/reports/institutions", params={"state": "Reportland"}).json()
    assert rep["total"] == 1 and rep["rows"][0]["Institution"] == "Synthetic Reporting Institute"
    docs = c.get("/api/reports/documents", params={"institution_id": inst["id"]}).json()
    assert docs["total"] == n_docs_state
    csv_r = c.get("/api/reports/documents", params={"institution_id": inst["id"], "format": "csv"})
    assert csv_r.status_code == 200 and "text/csv" in csv_r.headers["content-type"]
    pdf_r = c.get("/api/reports/audit", params={"format": "pdf"})
    assert pdf_r.content.startswith(b"%PDF")
    for kind in ("pending_cases", "discrepancies", "inspections", "corrective_actions", "decisions", "officer_activity", "integrations"):
        assert c.get(f"/api/reports/{kind}").status_code == 200
    for kind in ("uploads", "pending_tasks", "institutions_updated", "high_priority_discrepancies", "inspection_findings",
                 "corrective_awaiting", "failed_jobs", "decisions", "external_failures"):
        assert c.get("/api/dashboard/activity", params={"kind": kind}).status_code == 200


def test_file_links_require_session_and_expire(app, officer_client):
    from conftest import make_client

    c = officer_client
    inst = create_institution(c, name="Synthetic File Access Institute")
    doc = upload(c, inst["id"], synthetic_pdf(["Synthetic file access"]), "f.pdf").json()["document"]
    run_jobs()
    url = c.get(f"/api/documents/{doc['id']}").json()["download_url"]
    assert c.get(url).status_code == 200
    assert make_client(app).get(url).status_code == 401  # link alone is not enough
    assert c.get(url[:-4] + "AAAA").status_code == 403  # tampered signature
