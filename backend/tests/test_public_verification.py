"""No-API verification methods: issuer QR pages on official hosts, CCA trust roots, Roboflow Workflows (synthetic responses only)."""

import httpx

from test_integrations_inspection import _reset, _transport


def _generic():
    from app.models import Integration

    return Integration(adapter="issuer_qr_generic", name="generic", config={"allowed_host_suffixes": [".gov.in", ".nic.in"], "allowed_hosts": []})


DOC = {"certificate_number": "SYN-FSC-2026-0042", "institution_name": "Synthetic Institute of Technology", "valid_until": "31-12-2027"}


def test_generic_issuer_page_verified_when_values_shown():
    from app.integrations.official import verify_generic_qr

    page = "<html><body><h1>Fire NOC</h1><p>NOC No: SYN-FSC-2026-0042</p><p>Issued to SYNTHETIC INSTITUTE OF TECHNOLOGY</p><p>Valid up to 31/12/2027</p></body></html>"
    _transport(lambda req: httpx.Response(200, text=page, headers={"content-type": "text/html"}))
    try:
        r = verify_generic_qr(_generic(), "https://firenoc.example.gov.in/verify?id=42", DOC)
        assert r.outcome == "verified" and r.matched_values["certificate_number"]["match"] and r.matched_values["valid_until"]["match"]
    finally:
        _reset()


def test_generic_issuer_page_missing_values_is_not_a_false_mismatch():
    from app.integrations.official import verify_generic_qr

    _transport(lambda req: httpx.Response(200, text="<html><body><p>Certificate SYN-OTHER-0001 issued to Another College of Arts</p></body></html>",
                                          headers={"content-type": "text/html"}))
    try:
        r = verify_generic_qr(_generic(), "https://firenoc.example.gov.in/verify?id=1", DOC)
        assert r.outcome == "unable_to_verify" and "not found" in r.reason
    finally:
        _reset()


def test_generic_issuer_page_refuses_non_government_hosts():
    from app.integrations.official import generic_qr_allowed, verify_generic_qr

    called = []
    _transport(lambda req: called.append(1) or httpx.Response(200, text="ok"))
    try:
        assert not generic_qr_allowed("https://gov.in.example.com/x", _generic())
        assert not generic_qr_allowed("http://site.gov.in/x", _generic())
        assert generic_qr_allowed("https://site.gov.in/x", _generic())
        r = verify_generic_qr(_generic(), "https://verify-certificates.example.com/x", DOC)
        assert r.outcome == "unable_to_verify" and not called
    finally:
        _reset()


def test_cca_india_trust_roots_are_loaded():
    from app.documents.integrity import _load_trust_roots

    names = {r.subject.native["common_name"] for r in _load_trust_roots()}
    assert {"CCA India 2022", "CCA India 2022 SPL"} <= names


def test_workflow_outputs_parsed_from_real_response_shapes():
    from app.integrations.roboflow import _strip_images, parse_workflow_outputs

    ocr = {"outputs": [{"recognized_text": "SYNTHETIC TEXT", "visualized_image": {"type": "base64", "value": "A" * 5000}}]}
    preds = parse_workflow_outputs(ocr, 1.0, 640, 480)
    assert [(p.label, p.geometry_type, p.geometry["text"]) for p in preds] == [("recognized_text", "text", "SYNTHETIC TEXT")]
    assert "omitted" in str(_strip_images(ocr))
    det = {"outputs": [{"model_predictions": {"image": {"width": 640, "height": 480}, "predictions": [
        {"x": 100, "y": 100, "width": 20, "height": 40, "confidence": 0.8, "class": "crack", "class_id": 0}]}}]}
    p = parse_workflow_outputs(det, 1.0, 640, 480)[0]
    assert p.label == "crack" and p.geometry == {"x0": 90.0, "y0": 80.0, "x1": 110.0, "y1": 120.0, "output": "model_predictions"}


def test_multi_output_workflow_summarized():
    """Shapes of the document/image analysis workflow: text, visual evidence JSON, condition report, nested crop detections."""
    import json

    from app.integrations.roboflow import parse_workflow_outputs
    from app.integrations.vision_report import assessment_text, summarize

    crop = {"offset_x": 400, "offset_y": 300, "width": 100, "height": 80}
    payload = {"outputs": [{
        "document_text": "SYNTHETIC CERTIFICATE\nValid up to 31-12-2030",
        "header_text": "SYNTHETIC ISSUER",
        "visual_evidence": "```json\n" + json.dumps({"signature_marks": "Handwritten signature at bottom right", "stamp_marks": "not observed on this page",
                                                         "document_type_candidate": "Fire safety certificate", "evidence_limitations": ["Cannot prove issuance."]}) + "\n```",
        "condition_and_requirement_report": [json.dumps({"scene_category": "classroom", "assessment_status": "visible_concern_observed",
                                                          "findings": [{"defect_candidate": "ceiling_water_stain", "affected_object": "ceiling",
                                                                        "exact_visible_evidence": "Brown stain", "certainty": "clear_visual_evidence",
                                                                        "verification_needed": "Inspect roof"}], "limitations": ["Single photo"]})],
        "photograph_regions": {"image": {"width": 640, "height": 480}, "predictions": [
            {"x": 450, "y": 340, "width": 100, "height": 80, "confidence": 1.0, "class": "embedded photograph"}]},
        "defect_candidates": [{"image": {"width": 100, "height": 80}, "predictions": [
            {"x": 50, "y": 40, "width": 20, "height": 10, "confidence": 0.9, "class": "water stain", "root_parent_origin": crop}]}],
        "localized_defect_evidence": [{"image": {"width": 100, "height": 80}, "predictions": [
            {"x": 50, "y": 40, "width": 20, "height": 10, "confidence": 0.9, "class": "water stain", "root_parent_origin": crop}]}],
    }]}
    a = summarize(parse_workflow_outputs(payload, 1.0, 640, 480), 640, 480)
    assert a["text"].startswith("SYNTHETIC CERTIFICATE") and a["header"] == "SYNTHETIC ISSUER"
    assert a["visual"]["document_type_candidate"] == "Fire safety certificate"
    stains = [d for d in a["detections"] if d["kind"] == "defect"]
    assert len(stains) == 1  # reported by two outputs, kept once
    assert stains[0]["box"] == [round(440 / 640, 4), round(335 / 480, 4), round(460 / 640, 4), round(345 / 480, 4)]  # crop offset applied
    assert a["assessment"]["status"] == "visible_concern" and a["assessment"]["findings"][0]["issue"] == "Ceiling water stain"
    assert "Inspect roof" in assessment_text(a["assessment"])


def test_workflow_endpoint_format():
    from app.integrations.roboflow import endpoint
    from app.models import Integration, ModelConfig

    m = ModelConfig(project_id="synthetic-workspace", model_version="synthetic-workflow", task_type="workflow")
    assert endpoint(Integration(base_url="https://serverless.roboflow.com"), m) == "https://serverless.roboflow.com/synthetic-workspace/workflows/synthetic-workflow"


def test_institution_name_comparison_explains_mismatches():
    from app.documents.normalize import compare_names, find_name_in_text

    # Case, punctuation, abbreviations, honorific spellings, initials, joined words and OCR slips are all the same name.
    for doc, reg in [("SYNTHETIC INSTITUTE OF TECHNOLOGY", "synthetic institute of technology"),
                     ("Shri Testdeo Baba College of Engg. & Management", "SHREE TESTDEOBABA COLLEGE OF ENGINEERING AND MANAGEMENT"),
                     ("R.V. Synthetic College", "RV SYNTHETIC COLLEGE"),
                     ("SREE SYNTHETIC ENGIHEERTNG COLLEGE", "Sri Synthetic Engineering College")]:
        assert compare_names(doc, reg).status == "pass", (doc, reg)
    # An address typed into the record's name is not required in the document.
    assert compare_names("SYNTHETIC COLLEGE OF ENGINEERING", "SYNTHETIC COLLEGE OF ENGINEERING, STATION ROAD - 400001").status == "pass"
    # A different proper name fails, and the reason names the differing words.
    r = compare_names("ALPHA SYNTHETIC INSTITUTE OF TECHNOLOGY", "BETA SYNTHETIC INSTITUTE OF TECHNOLOGY")
    assert r.status == "fail" and "BETA" in r.reason and "ALPHA" in r.reason
    # Only a generic word missing: a warning, explained.
    w = compare_names("SYNTHETIC ENGINEERING", "Synthetic Engineering College")
    assert w.status == "warning" and "COLLEGE" in w.reason
    # Searching free text finds the line that names the institution.
    t = find_name_in_text("Receipt\nM/S SYNTHETIC RAMA COLLEGE, located at Testland\nDate: 01-01-2030", "Synthetic Rama College")
    assert t.status == "pass" and "SYNTHETIC RAMA COLLEGE" in t.document_name.upper()
    assert find_name_in_text("Unrelated text only", "Synthetic Rama College").status == "fail"


def test_tampering_indicators_only_count_on_key_values():
    from types import SimpleNamespace

    from app.documents.integrity import _key_hits

    date = SimpleNamespace(field_key="issue_date", page_no=2, bbox=[0.15, 0.79, 0.32, 0.81])
    note = SimpleNamespace(field_key="remarks", page_no=2, bbox=[0.1, 0.5, 0.9, 0.55])
    assert _key_hits(2, [0.14, 0.78, 0.34, 0.83], [date, note], tight=True) == ["issue_date"]  # patch around the date
    assert _key_hits(2, [0.0, 0.77, 1.0, 1.0], [date], tight=True) == []  # a band across the page is not an edit
    assert _key_hits(2, [0.1, 0.5, 0.9, 0.55], [note]) == []  # not a key value
    assert _key_hits(3, [0.14, 0.78, 0.34, 0.83], [date]) == []  # other page
