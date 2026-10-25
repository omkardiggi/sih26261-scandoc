"""Idempotent seeding of reference *configuration* (never operational records).

Seeds:
* document categories used by the explainable keyword classifier (platform defaults);
* integration entries for known external services, all disabled / unconfigured;
* document requirements transcribed from APH 2024-27 with clause references
  (marked unconfirmed until an officer confirms them).

No institutions, documents, results or statistics are ever created here.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from sqlalchemy import select

from ..config import BACKEND_DIR
from ..db import session_scope
from ..models import DocumentCategory, DocumentRequirement, Integration

log = logging.getLogger("app.bootstrap")

REFERENCE_DIR = BACKEND_DIR.parent / "data" / "reference"

APPLICATION_TYPES = {
    "extension_of_approval": "Extension of Approval (EoA)",
    "new_institution": "Approval for a New Institution",
    "increase_in_intake": "Increase in Intake / Additional Course",
    "change_of_site": "Change of Site / Location",
    "closure": "Progressive / Complete Closure",
    "change_of_name": "Change in Name",
    "other": "Other application / review",
}

# (key, label, keywords[(pattern, weight)], expected_fields, signature_expected, validity_applicable, notes)
_CATEGORIES: list[tuple] = [
    ("approval_letter", "Approval / Extension of Approval letter",
     [("extension of approval", 4), ("letter of approval", 4), ("approval process", 2), ("date of approval", 3),
      ("/eoa", 3), ("/loa", 3), ("all india council for technical education", 1), ("intake approved", 2),
      ("permanent id", 2), ("conduct following programs", 2)],
     ["aicte_permanent_id", "application_id", "institution_name", "reference_number", "issue_date", "academic_year", "issuing_authority"],
     False, False,
     "AICTE EoA letters observed on institution websites state that they are computer generated and require no signature."),
    ("application_form", "Application form / application report",
     [("application form", 4), ("application report", 4), ("online application", 2), ("application id", 2),
      ("application no", 2)], ["institution_name", "application_id", "academic_year"], None, False, None),
    ("affidavit", "Affidavit",
     [("affidavit", 5), ("e-stamp", 3), ("stamp paper", 3), ("solemnly affirm", 3), ("deponent", 3),
      ("notary", 2), ("oath commissioner", 2), ("sworn", 2)], ["issue_date", "institution_name"], None, False, None),
    ("resolution", "Resolution of the Trust / Society / Company",
     [("resolution", 4), ("resolved that", 4), ("board meeting", 2), ("format 3", 3), ("governing body", 1)],
     ["issue_date", "institution_name"], None, False, None),
    ("noc", "No Objection Certificate (affiliating University / Board)",
     [("no objection certificate", 5), ("noc", 3), ("format 2", 3), ("no objection", 3)],
     ["issue_date", "issuing_authority", "institution_name"], None, False, None),
    ("affiliation_document", "Affiliation document",
     [("affiliation", 4), ("affiliated to", 3), ("provisional affiliation", 4), ("continuation of affiliation", 4)],
     ["institution_name", "issuing_authority", "issue_date", "academic_year"], None, True, None),
    ("accreditation_record", "Accreditation / recognition record",
     [("accreditation", 4), ("accredited", 3), ("national board of accreditation", 4), ("naac", 3), ("nba", 2)],
     ["institution_name", "issuing_authority", "issue_date", "valid_until"], None, True, None),
    ("faculty_record", "Faculty and staff record",
     [("faculty", 3), ("teaching staff", 3), ("designation", 1), ("cadre ratio", 3), ("student faculty ratio", 3),
      ("salary sheet", 3)], ["institution_name", "faculty_count"], None, False, None),
    ("faculty_qualification", "Faculty qualification document",
     [("degree certificate", 3), ("ph.d", 2), ("doctor of philosophy", 3), ("master of technology", 2),
      ("qualification", 2), ("marks statement", 2)], ["issue_date", "issuing_authority"], None, False, None),
    ("land_document", "Land / ownership document",
     [("sale deed", 5), ("lease deed", 5), ("gift deed", 4), ("settlement deed", 4), ("encumbrance", 3),
      ("survey no", 2), ("land use", 3), ("conversion certificate", 3)], ["issue_date", "issuing_authority"], None, False, None),
    ("building_plan", "Building plan",
     [("building plan", 5), ("site plan", 3), ("floor plan", 3), ("sanctioned plan", 4), ("council of architecture", 3),
      ("architect", 1)], ["issue_date", "issuing_authority"], None, False, None),
    ("occupancy_certificate", "Occupancy / Completion certificate",
     [("occupancy certificate", 5), ("completion certificate", 5), ("building license", 3), ("form d", 2),
      ("occupation certificate", 5)], ["certificate_number", "issue_date", "issuing_authority", "institution_name"], None, True, None),
    ("structural_stability_certificate", "Structural stability certificate",
     [("structural stability", 6), ("structurally stable", 4), ("fit for human habitation", 4), ("structural engineer", 3)],
     ["issue_date", "valid_until", "issuing_authority"], None, True, None),
    ("fire_safety_certificate", "Fire safety certificate",
     [("fire safety certificate", 6), ("fire no objection", 5), ("fire noc", 5), ("fire service", 3),
      ("fire and emergency", 3), ("fire prevention", 3), ("fire safety", 3)],
     ["certificate_number", "issue_date", "valid_until", "issuing_authority", "institution_name"], None, True, None),
    ("site_location_map", "Satellite / site location map",
     [("latitude", 4), ("longitude", 4), ("satellite", 3), ("google earth", 3), ("location map", 3)],
     ["coordinates"], None, False, None),
    ("laboratory_equipment_record", "Laboratory and equipment record",
     [("laboratory", 3), ("equipment", 3), ("invoice", 2), ("lab ", 1), ("workshop", 2)], ["issue_date"], None, False, None),
    ("library_record", "Library and infrastructure record",
     [("library", 4), ("books", 2), ("journals", 3), ("volumes", 2), ("e-journal", 3)], [], None, False, None),
    ("student_intake_record", "Student intake and academic information",
     [("admitted", 3), ("enrolment", 3), ("enrollment", 3), ("students admitted", 4), ("intake", 2)],
     ["academic_year"], None, False, None),
    ("audited_financial_statement", "Audited financial statement",
     [("balance sheet", 5), ("audited", 4), ("statement of accounts", 4), ("income and expenditure", 4),
      ("chartered accountant", 3), ("auditor", 2)], ["issue_date", "financial_year"], None, False, None),
    ("bank_certificate", "Bank certificate / financial status",
     [("bank", 2), ("branch manager", 4), ("fixed deposit", 3), ("certificate 3", 3), ("solvency", 4)],
     ["issue_date", "issuing_authority"], None, False, None),
    ("fee_payment_receipt", "Fee payment receipt",
     [("receipt", 4), ("payment", 3), ("transaction id", 3), ("processing fee", 3), ("amount paid", 3)],
     ["issue_date"], None, False, None),
    ("registration_document", "Trust / Society / Company registration",
     [("registration", 3), ("memorandum of association", 5), ("certificate of incorporation", 5),
      ("registrar of societies", 4), ("trust deed", 4), ("section 8", 3)], ["issue_date", "issuing_authority"], None, False, None),
    ("governance_document", "Governance document (BoG / BoM)",
     [("board of governors", 5), ("board of management", 5), ("governing body", 3)], [], None, False, None),
    ("compliance_declaration", "Compliance declaration / undertaking",
     [("undertaking", 4), ("declaration", 3), ("hereby declare", 3), ("mandatory disclosure", 4)],
     ["issue_date", "institution_name"], None, False, None),
    ("inspection_report", "Inspection / expert visit report",
     [("expert visit", 5), ("inspection report", 5), ("visit committee", 4), ("scrutiny committee", 3), ("deficiency", 2)],
     ["issue_date", "institution_name"], None, False, None),
    ("other", "Other supporting document", [], [], None, False, None),
]


def _seed_categories(db) -> None:
    existing = {c.key for c in db.execute(select(DocumentCategory)).scalars()}
    for order, (key, label, kws, fields, sig, validity, notes) in enumerate(_CATEGORIES):
        if key in existing:
            continue
        db.add(
            DocumentCategory(
                key=key,
                label=label,
                description="Platform default category (not an official classification). Keywords are configurable.",
                keywords=[{"pattern": p, "weight": w} for p, w in kws],
                expected_fields=fields,
                signature_expected=sig,
                validity_applicable=validity,
                notes=notes,
                builtin=True,
                sort_order=order,
            )
        )
    db.flush()
    existing = {c.key for c in db.execute(select(DocumentCategory)).scalars()}
    for i, (key, label, kws) in enumerate(_EXTRA):
        if key not in existing:
            db.add(DocumentCategory(key=key, label=label, keywords=[{"pattern": p, "weight": w} for p, w in kws], expected_fields=[],
                                    description="Compliance evidence slot (organisational category, not an official requirement by itself).",
                                    builtin=True, sort_order=200 + i))
    db.flush()
    for group, keys in GROUPS.items():
        for k in keys:
            c = db.get(DocumentCategory, k)
            if c is not None and c.group_name is None:
                c.group_name, c.icon = group, ICONS[group]


GROUPS = {
    "Approval & legal": ("approval_letter", "application_form", "affidavit", "resolution", "noc", "affiliation_document",
                         "accreditation_record", "registration_document", "governance_document", "compliance_declaration", "inspection_report"),
    "Land & building": ("land_document", "building_plan", "occupancy_certificate", "structural_stability_certificate", "site_location_map"),
    "Safety & certificates": ("fire_safety_certificate", "food_safety_certificate", "general_insurance", "safety_security_measures",
                              "barrier_free_environment", "potable_water_supply"),
    "Finance": ("audited_financial_statement", "bank_certificate", "fee_payment_receipt", "digital_payments"),
    "Academics & faculty": ("faculty_record", "faculty_qualification", "student_intake_record", "teacher_training_policy",
                            "abc_registration", "skill_development_courses", "internships_projects", "gate_encouragement", "hackathon_participation"),
    "Committees & cells": ("online_grievance", "anti_ragging_committee", "grievance_redressal_committee", "sc_st_committee", "iqac", "ipr_cell"),
    "Facilities": ("laboratory_equipment_record", "library_record", "fabrication_lab_equipment", "auditorium", "projectors_in_classrooms",
                   "public_announcement_system", "transport_facility", "solar_and_street_lights", "erp_system", "display_of_courses"),
    "Disclosures & other": ("mandatory_disclosure", "government_schemes", "mou", "campus_photos", "other"),
}
ICONS = {"Approval & legal": "stamp", "Land & building": "building", "Safety & certificates": "shield", "Finance": "wallet",
         "Academics & faculty": "graduation", "Committees & cells": "users", "Facilities": "flask", "Disclosures & other": "folder"}

# Mandatory-disclosure / compliance evidence slots: organisational categories, not requirements by themselves.
_EXTRA = [
    ("online_grievance", "Online grievance redressal", [("online grievance", 5), ("grievance redressal", 3), ("grievance portal", 4)]),
    ("anti_ragging_committee", "Anti-ragging committee", [("anti-ragging", 5), ("anti ragging", 5), ("ragging", 3)]),
    ("grievance_redressal_committee", "Grievance redressal committee (GRC)", [("grievance redressal committee", 6)]),
    ("sc_st_committee", "SC/ST committee", [("sc/st", 5), ("scheduled caste", 4), ("sc st committee", 5)]),
    ("iqac", "IQAC", [("iqac", 6), ("internal quality assurance", 6)]),
    ("barrier_free_environment", "Barrier-free environment", [("barrier free", 6), ("barrier-free", 6), ("divyang", 3)]),
    ("teacher_training_policy", "Teacher training policy", [("teacher training", 6), ("faculty development", 3)]),
    ("mou", "MOUs", [("memorandum of understanding", 6), ("mou", 4)]),
    ("abc_registration", "Academic Bank of Credits (ABC)", [("academic bank of credits", 6), ("abc id", 4)]),
    ("safety_security_measures", "Safety and security measures", [("cctv", 4), ("security guard", 4), ("safety and security", 5)]),
    ("food_safety_certificate", "Food safety certificate", [("fssai", 6), ("food safety", 6)]),
    ("digital_payments", "Digital payments", [("digital payment", 6), ("upi", 3)]),
    ("mandatory_disclosure", "Mandatory disclosure (website screenshot)", [("mandatory disclosure", 6)]),
    ("potable_water_supply", "Potable water supply", [("potable water", 6), ("drinking water", 5), ("water test", 4)]),
    ("government_schemes", "Implementation of GOI schemes", [("scheme", 3), ("government of india", 2)]),
    ("skill_development_courses", "Skill development courses", [("skill development", 6)]),
    ("fabrication_lab_equipment", "Fabrication lab equipment", [("fabrication", 6), ("fab lab", 6)]),
    ("solar_and_street_lights", "Solar panels and street lights (with bill)", [("solar", 6), ("street light", 6)]),
    ("public_announcement_system", "Public announcement system", [("public address", 6), ("announcement system", 6)]),
    ("erp_system", "ERP system", [("enterprise resource planning", 6), ("erp", 4)]),
    ("gate_encouragement", "GATE preparation efforts", [("gate exam", 6), ("graduate aptitude test", 6)]),
    ("hackathon_participation", "Hackathon participation", [("hackathon", 6)]),
    ("internships_projects", "Internships & IKS / disaster management projects", [("internship", 5), ("indian knowledge system", 5), ("disaster management", 5)]),
    ("transport_facility", "Transport facility", [("transport facility", 6), ("college bus", 4)]),
    ("projectors_in_classrooms", "Projectors in classrooms", [("projector", 6)]),
    ("auditorium", "Auditorium", [("auditorium", 6)]),
    ("display_of_courses", "Display of courses at entrance", [("display board", 5)]),
    ("general_insurance", "General insurance", [("insurance policy", 6), ("general insurance", 6)]),
    ("ipr_cell", "IPR cell", [("intellectual property", 6), ("ipr cell", 6)]),
    ("campus_photos", "Campus photographs", []),
]


_INTEGRATIONS = [
    dict(
        key="official_qr_pages",
        name="Official issuer pages (QR links on certificates)",
        purpose=("Open the verification page that a certificate's QR code links to — only on official government addresses — "
                 "and check which of the document's values (certificate/reference number, institution name, dates) the issuer shows."),
        kind="official_record",
        adapter="issuer_qr_generic",
        provider="Issuing government departments",
        source_owner="The issuing authority named on each certificate",
        auth_method="none",
        enabled=True,
        config={"allowed_host_suffixes": [".gov.in", ".nic.in"], "allowed_hosts": [],
                "note": ".gov.in and .nic.in names are issued only to government bodies. Add other official hosts to allowed_hosts after confirming them."},
        claim_types=["issuer_record"],
        coverage_notes=("Works for issuer pages that show the record as plain HTML, JSON or PDF. Pages that need JavaScript or a captcha "
                        "return 'unable to verify' — check those manually. Values missing from a page are never reported as a mismatch."),
        required_permissions="None (public verification pages linked by issuers).",
        privacy_notes="Only the link printed in the QR code is opened; no document content is sent.",
    ),
    dict(
        key="aicte_qr_status",
        name="AICTE approval-status page (QR link on letters)",
        purpose=("Compare an approval letter with the issuer-hosted AICTE record reached through the QR code printed on "
                 "the letter (institution name, IDs, academic year, status and approved intake)."),
        kind="official_record",
        adapter="aicte_qr_status",
        provider="All India Council for Technical Education",
        source_owner="AICTE",
        base_url="https://approvalstatus.aicte.gov.in",
        auth_method="none",
        enabled=True,
        config={"allowed_hosts": ["approvalstatus.aicte.gov.in"], "path_prefix": "/api/getInstituteDetails/",
                "provenance": "Host observed in QR codes printed on AICTE Extension of Approval letters for 2026-27."},
        claim_types=["approval_record"],
        documentation_url=None,
        coverage_notes=("Available only for letters that carry an AICTE QR code. The page is an HTML view without published "
                        "API documentation; the parser reads its labelled table and fails closed when the layout is not recognised. "
                        "Checks run only when an officer requests them, and only allowlisted hosts are contacted."),
        required_permissions="None (public verification page linked by the issuer).",
        privacy_notes="Only the QR token printed on the letter is sent to AICTE; no document content leaves the platform.",
    ),
    dict(
        key="aicte_official_records",
        name="AICTE institution & approval records",
        purpose="Confirm institution identity, approval status and approved intake against AICTE's own records.",
        kind="official_record",
        adapter="generic_rest",
        provider="All India Council for Technical Education",
        source_owner="AICTE",
        auth_method="none",
        claim_types=["institution_identity", "approval_status", "approved_intake"],
        documentation_url=None,
        coverage_notes=(
            "No publicly documented AICTE verification API was found. The public 'Approved Institutes' dashboard "
            "on facilities.aicte-india.org has no published API and is not accessed automatically. Configure this "
            "integration only with authorised API documentation and credentials issued by AICTE."
        ),
        required_permissions="Authorised API access issued by AICTE (to be confirmed).",
        privacy_notes="Only identifiers (Permanent ID / application ID) should be sent; no document contents.",
    ),
    dict(
        key="digilocker_requester",
        name="DigiLocker / API Setu (Requester)",
        purpose="Verify certificates issued into DigiLocker by their issuing authorities.",
        kind="official_record",
        adapter="generic_rest",
        provider="Ministry of Electronics & IT (DigiLocker / API Setu)",
        source_owner="Issuing authorities via DigiLocker",
        auth_method="bearer",
        claim_types=["certificate_authenticity"],
        documentation_url="https://www.digilocker.gov.in/assets/FAQ%20DL%20EL_onboarding.pdf",
        coverage_notes=(
            "Requires onboarding as a Requester organisation on API Setu, OAuth2 client credentials and, for "
            "citizen documents, user consent. Coverage is limited to documents whose issuers publish to DigiLocker."
        ),
        required_permissions="Approved API Setu Requester registration; OAuth2 client credentials.",
        rate_limit_notes="API Setu applies rate limits per client (see onboarding FAQ).",
        privacy_notes="Consent-based access; follow DigiLocker terms. Do not send full documents.",
    ),
    dict(
        key="roboflow",
        name="Computer vision inference provider",
        purpose="Run configured inspection models on photographs and sampled video frames.",
        kind="vision_model_provider",
        adapter="roboflow",
        provider="Roboflow",
        source_owner="Model owner (your organisation)",
        base_url="https://serverless.roboflow.com",
        auth_method="bearer",
        secret_env_var="ROBOFLOW_API_KEY",
        claim_types=[],
        documentation_url="https://docs.roboflow.com/deploy/serverless-hosted-api-v2/use-with-the-rest-api",
        coverage_notes="Only models you configure (project, version, classes) are used. Inference runs server-side.",
        required_permissions="A workspace API key with inference access to the configured projects.",
        privacy_notes=(
            "Images are sent to the provider for inference. Confirm the privacy assessment and data-sharing "
            "authorisation before enabling for confidential evidence."
        ),
    ),
    dict(
        key="clamav",
        name="Malware scanner",
        purpose="Scan uploaded files for malware before processing.",
        kind="malware_scanner",
        adapter="clamav",
        provider="ClamAV (clamd)",
        source_owner="Self-hosted",
        auth_method="none",
        claim_types=[],
        documentation_url="https://docs.clamav.net/manual/Usage/Scanning.html",
        coverage_notes="Configured with CLAMD_HOST / CLAMD_PORT environment variables.",
    ),
    dict(
        key="smtp",
        name="Email delivery",
        purpose="Deliver password reset and invitation messages.",
        kind="email",
        adapter="smtp",
        provider="SMTP relay",
        source_owner="Your organisation",
        auth_method="none",
        claim_types=[],
        coverage_notes="Configured with SMTP_* environment variables.",
    ),
]


def _seed_integrations(db) -> None:
    existing = {i.key for i in db.execute(select(Integration)).scalars()}
    for spec in _INTEGRATIONS:
        if spec["key"] in existing:
            continue
        db.add(Integration(builtin=True, connection_status="untested", **({"enabled": False} | spec)))


def import_requirements(db, path: Path, created_by_id: str | None = None) -> int:
    data = json.loads(path.read_text(encoding="utf-8"))
    src = data.get("source", {})
    years = src.get("applies_to_academic_years", [])
    known_keys = {c.key for c in db.execute(select(DocumentCategory)).scalars()}
    existing = {r.import_key for r in db.execute(select(DocumentRequirement)).scalars() if r.import_key}
    added = 0
    for req in data.get("requirements", []):
        if req["import_key"] in existing:
            continue
        if req["category_key"] not in known_keys:
            raise ValueError(f"Unknown document category in requirements file: {req['category_key']}")
        db.add(
            DocumentRequirement(
                import_key=req["import_key"],
                category_key=req["category_key"],
                title=req["title"],
                application_type=req["application_type"],
                institution_category=req.get("institution_category"),
                academic_years=req.get("academic_years", years),
                mandatory=bool(req.get("mandatory", True)),
                condition=req.get("condition"),
                validity_rule=req.get("validity_rule", {}),
                source_reference=req["source_reference"],
                source_url=req.get("source_url", src.get("retrieved_copy_url")),
                source_page=req.get("source_page"),
                source_excerpt=req.get("source_excerpt"),
                created_by_id=created_by_id,
            )
        )
        added += 1
    return added


def _seed_analysis_workflow(db) -> None:
    """Register the configured document & image analysis workflow when an API key is available (idempotent)."""
    import os

    from ..config import get_settings
    from ..models import ModelConfig

    s = get_settings()
    if s.private_mode or not (s.roboflow_workspace and s.document_text_workflow and os.environ.get("ROBOFLOW_API_KEY")):
        return  # private mode never sets up an outside AI service
    integ = db.execute(select(Integration).where(Integration.adapter == "roboflow")).scalar_one_or_none()
    if integ is None:
        return
    m = db.execute(select(ModelConfig).where(ModelConfig.project_id == s.roboflow_workspace,
                                             ModelConfig.model_version == s.document_text_workflow)).scalar_one_or_none()
    if m is None:
        db.add(ModelConfig(name="Document & image analysis", integration_id=integ.id, project_id=s.roboflow_workspace,
                           model_version=s.document_text_workflow, task_type="workflow", enabled=True, classes=[], class_rules={},
                           input_formats=["jpeg", "png"], facility_categories=[], status="untested",
                           description="Page reading, signatures/stamps, photographs and facility condition"))
        log.info("Registered the document & image analysis workflow %s.", s.document_text_workflow)
    if not integ.enabled:
        integ.enabled = True
        integ.secret_env_var = integ.secret_env_var or "ROBOFLOW_API_KEY"


def ensure_reference_configuration() -> None:
    try:
        with session_scope() as db:
            _seed_categories(db)
            _seed_integrations(db)
            db.flush()
            _seed_analysis_workflow(db)
            ref = REFERENCE_DIR / "aph_2024_27_requirements.json"
            if ref.exists():
                n = import_requirements(db, ref)
                if n:
                    log.info("Imported %d handbook-referenced document requirements (unconfirmed).", n)
    except Exception as exc:  # noqa: BLE001
        # Most likely the database has not been migrated yet.
        log.warning("Reference configuration not seeded: %s", exc)
