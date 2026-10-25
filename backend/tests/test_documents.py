"""Document verification pipeline (acceptance criteria 5-13, 19, 24, 28). All documents are SYNTHETIC."""

import io

from conftest import create_institution, run_jobs, synthetic_pdf, upload

EOA_LINES = [
    "All India Council for Technical Education",
    "APPROVAL PROCESS 2026-27 - Extension of Approval (EoA)",
    "F.No. Central/1-46200000001/2026/EOA",
    "Date of Approval: 16-Mar-2026",
    "Sub: Extension of Approval for the Academic Year 2026-27",
]
KV = [["Permanent Id", "1-9990001", "Application Id", "1-46200000001"],
      ["Name of the Institution", "Synthetic Institute of Technology", "Name of the Society/Trust", "Synthetic Education Trust"],
      ["Year of Establishment", "2005", "", ""]]
PROGRAMS = [["Level", "Program", "Course", "Affiliating Body", "Intake Approved for 2025-26", "Intake Approved for 2026-27"],
            ["UNDER GRADUATE", "ENGINEERING", "CIVIL ENGINEERING", "SYNTHETIC UNIVERSITY", "60", "60"],
            ["UNDER GRADUATE", "ENGINEERING", "MECHANICAL ENGINEERING", "SYNTHETIC UNIVERSITY", "120", "90"]]


def eoa_pdf(name="Synthetic Institute of Technology", pid="1-9990001") -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    kv = [r[:] for r in KV]
    kv[0][1], kv[1][1] = pid, name
    buf = io.BytesIO()
    ss = getSampleStyleSheet()
    story = [Paragraph("SYNTHETIC TEST DOCUMENT", ss["Title"])] + [Paragraph(x, ss["BodyText"]) for x in EOA_LINES]
    for t in (kv, PROGRAMS):
        story.append(Spacer(1, 10))
        tbl = Table(t)
        tbl.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.5, colors.black)]))
        story.append(tbl)
    SimpleDocTemplate(buf, pagesize=A4).build(story)
    return buf.getvalue()


def _case(client, inst_id):
    r = client.post("/api/cases", json={"institution_id": inst_id, "academic_year": "2026-27", "application_type": "extension_of_approval"})
    assert r.status_code == 201, r.text
    return r.json()


def test_upload_extract_validate_and_unverifiable(officer_client):
    c = officer_client
    inst = create_institution(c, aicte_permanent_id="1-9990001", establishment_year=2005)
    case = _case(c, inst["id"])
    assert case["status"] == "submission_incomplete"
    r = upload(c, inst["id"], eoa_pdf(), "eoa.pdf", case_id=case["id"])
    assert r.status_code == 201, r.text
    doc_id = r.json()["document"]["id"]
    assert len(r.json()["version"]["sha256"]) == 64
    run_jobs()
    d = c.get(f"/api/documents/{doc_id}").json()
    assert d["version"]["processing_status"] == "completed"
    assert d["document"]["category_key"] == "approval_letter"
    fields = {f["field_key"]: f for f in d["fields"] if f["group_index"] is None}
    assert fields["aicte_permanent_id"]["value"] == "1-9990001"
    assert fields["application_id"]["normalized_value"] == "1-46200000001"
    assert fields["academic_year"]["normalized_value"] == "2026-27"
    assert fields["issue_date"]["normalized_value"] == "2026-03-16"
    assert fields["aicte_permanent_id"]["page_no"] == 1 and len(fields["aicte_permanent_id"]["bbox"]) == 4
    intakes = sorted(f["value"] for f in d["fields"] if f["field_key"] == "program_intake")
    assert intakes == ["60", "90"]  # current-year column chosen from the 2026-27 header
    rules = {v["rule_key"]: v["status"] for v in d["validation"]}
    assert rules["permanent_id_match"] == "pass" and rules["institution_name_match"] == "pass"
    assert rules["academic_year_matches_case"] == "pass"
    # Nothing official was consulted -> never "verified"
    assert d["document"]["status"] == "pending_official_verification"
    assert d["pages"][0]["url"].startswith("/api/files/")
    assert c.get(d["pages"][0]["url"]).headers["content-type"] == "image/png"
    # No configured official API -> explicit "unable to verify"
    res = c.post(f"/api/documents/{doc_id}/official-verification", json={"method": "integration", "claim_type": "institution_identity"})
    assert res.status_code == 202 and res.json()["attempt"]["outcome"] == "unable_to_verify"
    assert "Unable to verify against official records" in res.json()["attempt"]["reason"]
    run_jobs()
    d = c.get(f"/api/documents/{doc_id}").json()
    assert d["document"]["status"] == "unable_to_verify"
    # Explanation has nine sections and missing EoA requirements are reported with handbook sources
    ex = c.get(f"/api/cases/{case['id']}/explanation").json()
    assert len(ex["sections"]) == 9
    missing = [r for r in ex["sections"]["required_documents"]["results"] if r["status"] == "fail"]
    assert missing and all("APH 2024-27" in r["details"]["source_reference"] for r in missing)
    assert c.get(f"/api/cases/{case['id']}").json()["status"] == "submission_incomplete"


def test_identity_mismatch_and_cross_document_conflict(officer_client):
    c = officer_client
    inst = create_institution(c, name="Synthetic College of Engineering", aicte_permanent_id="1-9990002")
    case = _case(c, inst["id"])
    a = upload(c, inst["id"], eoa_pdf(name="Synthetic College of Engineering", pid="1-9990002"), "a.pdf", case_id=case["id"]).json()
    b = upload(c, inst["id"], eoa_pdf(name="Completely Different Polytechnic", pid="1-9990003"), "b.pdf", case_id=case["id"]).json()
    run_jobs()
    db_ = c.get(f"/api/documents/{b['document']['id']}").json()
    rules = {v["rule_key"]: v for v in db_["validation"]}
    assert rules["permanent_id_match"]["status"] == "fail"
    assert rules["institution_name_match"]["status"] == "fail"
    assert rules["institution_name_match"]["details"]["registry_original"] == "Synthetic College of Engineering"
    assert db_["document"]["status"] == "information_mismatch"
    ex = c.get(f"/api/cases/{case['id']}/explanation").json()
    conflicts = {r["field_key"] for r in ex["sections"]["cross_document_comparisons"]["results"] if r["status"] == "conflict"}
    assert {"aicte_permanent_id", "institution_name"} <= conflicts
    assert c.get(f"/api/cases/{case['id']}").json()["status"] in ("discrepancies_identified", "submission_incomplete")
    assert c.get("/api/findings", params={"case_id": case["id"]}).json()["total"] >= 2
    assert a["document"]["id"] != b["document"]["id"]


def test_invalid_oversized_and_spoofed_files_rejected(officer_client):
    c = officer_client
    inst = create_institution(c, name="Synthetic Upload Rules Institute")
    assert upload(c, inst["id"], b"MZ\x90\x00 not a pdf", "evil.pdf").status_code == 422  # content mismatch
    assert upload(c, inst["id"], b"%PDF-1.4 broken", "broken.pdf").status_code == 422  # corrupted
    assert upload(c, inst["id"], b"hello", "notes.exe").status_code == 422  # extension
    assert upload(c, inst["id"], b"%PDF-1.4" + b"0" * (6 * 1024 * 1024), "big.pdf").status_code == 413  # > 5 MB test limit


def test_duplicate_detection_and_versions(officer_client):
    c = officer_client
    inst = create_institution(c, name="Synthetic Duplicate Institute")
    pdf = synthetic_pdf(["Fire Safety Certificate", "Certificate No: SYN-FSC-0001", "Valid up to 31-12-2020"])
    first = upload(c, inst["id"], pdf, "fire.pdf").json()
    second = upload(c, inst["id"], pdf, "fire-again.pdf").json()
    assert second["duplicate_of"]["version_id"] == first["version"]["id"]
    v2 = upload(c, inst["id"], synthetic_pdf(["Fire Safety Certificate v2"]), "fire2.pdf", document_id=first["document"]["id"]).json()
    assert v2["version"]["version_no"] == 2
    run_jobs()
    d = c.get(f"/api/documents/{first['document']['id']}").json()
    assert len(d["versions"]) == 2  # previous version preserved
    dup = c.get(f"/api/documents/{second['document']['id']}").json()
    assert {v["rule_key"]: v["status"] for v in dup["validation"]}["duplicate_file"] == "warning"
    assert {v["rule_key"]: v["status"] for v in dup["validation"]}["not_expired"] == "fail"  # expired per stated validity


def test_scanned_image_ocr(officer_client):
    from PIL import Image, ImageDraw, ImageFont

    c = officer_client
    inst = create_institution(c, name="Synthetic Scan Institute")
    img = Image.new("RGB", (1400, 700), "white")
    d = ImageDraw.Draw(img)
    font = ImageFont.load_default(size=40)
    for i, line in enumerate(["SYNTHETIC TEST DOCUMENT", "Fire Safety Certificate", "Certificate No: SYN-778812",
                              "Date: 05-01-2026", "Valid up to 04-01-2027"]):
        d.text((60, 60 + i * 110), line, fill="black", font=font)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    r = upload(c, inst["id"], buf.getvalue(), "scan.png")
    assert r.status_code == 201
    run_jobs()
    doc = c.get(f"/api/documents/{r.json()['document']['id']}").json()
    assert doc["version"]["ocr_used"] is True and doc["pages"][0]["text_source"] == "ocr"
    fields = {f["field_key"]: f for f in doc["fields"]}
    assert fields["valid_until"]["normalized_value"] == "2027-01-04"
    assert 0 < fields["valid_until"]["confidence"] <= 1 and fields["valid_until"]["confidence_basis"].startswith("OCR")
    assert doc["document"]["category_key"] == "fire_safety_certificate"


def test_overlay_tampering_flagged_with_evidence(officer_client):
    """A visible value drawn over different embedded text must be flagged (rendered vs embedded mismatch)."""
    import pymupdf

    c = officer_client
    inst = create_institution(c, name="Synthetic Overlay Institute")
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 100), "SYNTHETIC TEST DOCUMENT - Occupancy Certificate", fontsize=12)
    page.insert_text((72, 140), "Certificate No: 4455667788", fontsize=14)
    page.draw_rect(pymupdf.Rect(170, 125, 330, 146), color=(1, 1, 1), fill=(1, 1, 1))  # cover original digits
    page.insert_image(pymupdf.Rect(170, 125, 330, 146), stream=_digits_png("9911223344"))
    for i in range(12):
        page.insert_text((72, 200 + i * 20), f"Synthetic filler line {i} describing building occupancy conditions.", fontsize=11)
    data = doc.tobytes()
    r = upload(c, inst["id"], data, "occupancy.pdf")
    run_jobs()
    d = c.get(f"/api/documents/{r.json()['document']['id']}").json()
    check = next(x for x in d["integrity"] if x["check_type"] == "render_text_match")
    assert check["status"] == "warning" and check["details"]["significance"] == "high"
    assert check["evidence"] and check["evidence"][0]["page_no"] == 1
    assert d["document"]["status"] == "possible_tampering"
    # File integrity is reported separately and still passes
    assert next(x for x in d["integrity"] if x["check_type"] == "file_hash")["status"] == "pass"


def _digits_png(text: str) -> bytes:
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGB", (480, 64), "white")
    ImageDraw.Draw(img).text((4, 6), text, fill="black", font=ImageFont.load_default(size=48))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_digital_signature_reports_actual_result(officer_client, tmp_path):
    """Sign a synthetic PDF with a throwaway self-signed certificate, then tamper with it."""
    from datetime import UTC, datetime, timedelta

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
    from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
    from pyhanko.sign import signers

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Synthetic Test Signer")])
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(datetime.now(UTC) - timedelta(days=1))
            .not_valid_after(datetime.now(UTC) + timedelta(days=30)).sign(key, hashes.SHA256()))
    (tmp_path / "k.pem").write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    (tmp_path / "c.pem").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    signer = signers.SimpleSigner.load(str(tmp_path / "k.pem"), str(tmp_path / "c.pem"))
    out = io.BytesIO()
    w = IncrementalPdfFileWriter(io.BytesIO(synthetic_pdf(["Signed synthetic certificate"])))
    signers.sign_pdf(w, signers.PdfSignatureMetadata(field_name="Sig1"), signer=signer, output=out)
    signed = out.getvalue()

    c = officer_client
    inst = create_institution(c, name="Synthetic Signature Institute")
    good = upload(c, inst["id"], signed, "signed.pdf").json()
    tampered = bytearray(signed)
    i = tampered.find(b"Signed synthetic")
    if i < 0:
        i = len(tampered) // 3
    tampered[i + 2] ^= 0x01
    bad = upload(c, inst["id"], bytes(tampered), "signed-tampered.pdf").json()
    run_jobs()
    g = c.get(f"/api/documents/{good['document']['id']}").json()
    sig = next(x for x in g["integrity"] if x["check_type"] == "digital_signature")
    assert sig["details"]["intact"] is True and sig["details"]["trusted"] is False and sig["status"] == "warning"
    assert "Synthetic Test Signer" in sig["details"]["signer_subject"]
    b = c.get(f"/api/documents/{bad['document']['id']}").json()
    if b["version"]["processing_status"] == "completed":
        bsig = next(x for x in b["integrity"] if x["check_type"] == "digital_signature")
        assert bsig["status"] in ("fail", "error")


def test_officer_review_decision_idempotent_and_append_only(officer_client):
    from sqlalchemy import text

    from app.db import session_scope

    c = officer_client
    inst = create_institution(c, name="Synthetic Decision Institute")
    r = upload(c, inst["id"], synthetic_pdf(["Compliance declaration", "I hereby declare"]), "decl.pdf").json()
    run_jobs()
    doc_id = r["document"]["id"]
    assert c.post(f"/api/documents/{doc_id}/review", json={"outcome": "rejected", "reason": "short"}).status_code == 422
    body = {"outcome": "rejected", "reason": "Synthetic test: document does not bear the required declaration text."}
    one = c.post(f"/api/documents/{doc_id}/review", json=body, headers={"Idempotency-Key": "synthetic-key-1"})
    two = c.post(f"/api/documents/{doc_id}/review", json=body, headers={"Idempotency-Key": "synthetic-key-1"})
    assert one.status_code == 201 and one.json()["id"] == two.json()["id"]
    d = c.get(f"/api/documents/{doc_id}").json()
    assert d["document"]["status"] == "rejected" and len(d["decisions"]) == 1
    assert d["decisions"][0]["reason"] == body["reason"]
    with session_scope() as db:
        try:
            with db.begin_nested():
                db.execute(text("UPDATE decisions SET reason='rewritten'"))
            ok = False
        except Exception:  # noqa: BLE001
            ok = True
    assert ok, "decisions must be append-only"
    corr = c.post(f"/api/documents/{doc_id}/review", json={"outcome": "accepted_after_review", "supersedes_id": d["decisions"][0]["id"],
                                                          "reason": "Synthetic test: correction after receiving clarification."})
    assert corr.status_code == 201
    d = c.get(f"/api/documents/{doc_id}").json()
    assert len(d["decisions"]) == 2 and d["document"]["status"] != "rejected"


def test_field_correction_preserves_original(officer_client):
    c = officer_client
    inst = create_institution(c, name="Synthetic Correction Institute", aicte_permanent_id="1-9990009")
    r = upload(c, inst["id"], eoa_pdf(name="Synthetic Correction Institute", pid="1-9990008"), "c.pdf").json()
    run_jobs()
    d = c.get(f"/api/documents/{r['document']['id']}").json()
    pid = next(f for f in d["fields"] if f["field_key"] == "aicte_permanent_id")
    assert {v["rule_key"]: v["status"] for v in d["validation"]}["permanent_id_match"] == "fail"
    res = c.post(f"/api/fields/{pid['id']}/corrections", json={"value": "1-9990009", "reason": "Synthetic: OCR misread confirmed"})
    assert res.status_code == 201
    d = c.get(f"/api/documents/{r['document']['id']}").json()
    pid2 = next(f for f in d["fields"] if f["field_key"] == "aicte_permanent_id")
    assert pid2["value"] == "1-9990008" and pid2["effective_value"] == "1-9990009"  # original kept
    assert {v["rule_key"]: v["status"] for v in d["validation"]}["permanent_id_match"] == "pass"


def test_registry_change_rechecks_existing_documents(officer_client):
    c = officer_client
    inst = create_institution(c, name="Synthetic Registry Change Institute", aicte_permanent_id="1-9990777")
    doc = upload(c, inst["id"], eoa_pdf(name="Synthetic Registry Change Institute", pid="1-9990777"), "rc.pdf").json()["document"]
    run_jobs()
    rules = lambda: {v["rule_key"]: v["status"] for v in c.get(f"/api/documents/{doc['id']}").json()["validation"]}  # noqa: E731
    assert rules()["permanent_id_match"] == "pass"
    r = c.patch(f"/api/institutions/{inst['id']}", json={"changes": {"aicte_permanent_id": "1-9990778"}, "reason": "Synthetic: corrected ID"})
    assert r.status_code == 200
    run_jobs()
    assert rules()["permanent_id_match"] == "fail"  # the document is re-checked against the altered record
    assert c.get(f"/api/documents/{doc['id']}").json()["document"]["status"] == "information_mismatch"


def test_verification_report_pdf_has_summary_annotated_pages_and_appendix(officer_client):
    import pymupdf

    c = officer_client
    inst = create_institution(c, name="Synthetic Report Institute", aicte_permanent_id="1-9990555")
    doc = upload(c, inst["id"], eoa_pdf(name="Synthetic Report Institute", pid="1-9990555"), "rep.pdf").json()["document"]
    run_jobs()
    r = c.get(f"/api/reports/document/{doc['id']}/verification")
    assert r.status_code == 200 and r.headers["content-type"] == "application/pdf"
    assert r.headers["x-frame-options"] == "SAMEORIGIN"
    pdf = pymupdf.open("pdf", r.content)
    text = "\n".join(p.get_text() for p in pdf)
    assert "Is this document correct?" in text and "Matched against the institution record" in text
    assert "1-9990555" in text and "Appendix - text read from the document" in text
    assert any(p.rect.width > 700 for p in pdf)  # original page reproduced with the annotation side panel
    ri = c.get(f"/api/reports/institution/{inst['id']}/verification?download=true")
    assert ri.status_code == 200 and "attachment" in ri.headers["content-disposition"]
    assert "Document check summary" in "\n".join(p.get_text() for p in pymupdf.open("pdf", ri.content))
