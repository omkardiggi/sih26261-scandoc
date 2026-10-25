"""Explicit response serializers (keeps secrets and internal fields out of API output)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from . import models as m


def iso(dt: datetime | None) -> str | None:
    return dt.isoformat(timespec="seconds") + "Z" if dt else None


def officer(o: m.Officer | None, *, full: bool = False) -> dict | None:
    if o is None:
        return None
    data = {"id": o.id, "email": o.email, "full_name": o.full_name, "designation": o.designation, "role": "AICTE Officer"}
    if full:
        data.update(
            is_active=o.is_active,
            mfa_enabled=o.mfa_enabled,
            has_password=o.password_hash is not None,
            last_login_at=iso(o.last_login_at),
            created_at=iso(o.created_at),
            password_changed_at=iso(o.password_changed_at),
        )
    return data


def institution(i: m.Institution, *, extra: dict | None = None) -> dict:
    data = {
        "id": i.id,
        "name": i.name,
        "aicte_permanent_id": i.aicte_permanent_id,
        "category": i.category,
        "institution_type": i.institution_type,
        "region": i.region,
        "state": i.state,
        "district": i.district,
        "address": i.address,
        "pincode": i.pincode,
        "website": i.website,
        "email": i.email,
        "phone": i.phone,
        "management_body": i.management_body,
        "establishment_year": i.establishment_year,
        "affiliating_body": i.affiliating_body,
        "awarding_body": i.awarding_body,
        "academic_year": i.academic_year,
        "approval_status": i.approval_status,
        "approval_status_evidence": i.approval_status_evidence,
        "verification_status": i.verification_status,
        "last_verified_at": iso(i.last_verified_at),
        "extra_fields": i.extra_fields or {},
        "version": i.version,
        "is_archived": i.is_archived,
        "created_at": iso(i.created_at),
        "updated_at": iso(i.updated_at),
    }
    if extra:
        data.update(extra)
    return data


def program(p: m.Program) -> dict:
    return {
        "id": p.id,
        "institution_id": p.institution_id,
        "academic_year": p.academic_year,
        "level": p.level,
        "program": p.program,
        "course": p.course,
        "shift": p.shift,
        "affiliating_body": p.affiliating_body,
        "approved_intake": p.approved_intake,
        "nri_status": p.nri_status,
        "foreign_status": p.foreign_status,
        "status": p.status,
        "source_type": p.source_type,
        "source_document_id": p.source_document_id,
        "source_reference": p.source_reference,
        "compliance": p.compliance or {},
        "created_at": iso(p.created_at),
        "updated_at": iso(p.updated_at),
    }


def record(r: m.InstitutionRecord) -> dict:
    return {
        "id": r.id,
        "institution_id": r.institution_id,
        "kind": r.kind,
        "title": r.title,
        "academic_year": r.academic_year,
        "record_date": iso(r.record_date),
        "summary": r.summary,
        "data": r.data or {},
        "source_type": r.source_type,
        "source_document_id": r.source_document_id,
        "source_reference": r.source_reference,
        "supersedes_id": r.supersedes_id,
        "recorded_by_id": r.recorded_by_id,
        "recorded_at": iso(r.recorded_at),
    }


def case(c: m.VerificationCase, *, institution_name: str | None = None, extra: dict | None = None) -> dict:
    data = {
        "id": c.id,
        "reference": c.reference,
        "institution_id": c.institution_id,
        "institution_name": institution_name,
        "academic_year": c.academic_year,
        "application_type": c.application_type,
        "title": c.title,
        "status": c.status,
        "priority": c.priority,
        "due_date": iso(c.due_date),
        "notes": c.notes,
        "assigned_to_id": c.assigned_to_id,
        "opened_by_id": c.opened_by_id,
        "opened_at": iso(c.opened_at),
        "updated_at": iso(c.updated_at),
        "closed_at": iso(c.closed_at),
        "last_evaluated_at": iso(c.last_evaluated_at),
    }
    if extra:
        data.update(extra)
    return data


def version(v: m.DocumentVersion) -> dict:
    return {
        "id": v.id,
        "document_id": v.document_id,
        "version_no": v.version_no,
        "original_filename": v.original_filename,
        "detected_type": v.detected_type,
        "size_bytes": v.size_bytes,
        "sha256": v.sha256,
        "page_count": v.page_count,
        "pdf_encrypted": v.pdf_encrypted,
        "active_content": v.active_content or [],
        "malware_status": v.malware_status,
        "malware_detail": v.malware_detail,
        "duplicate_of_version_id": v.duplicate_of_version_id,
        "upload_note": v.upload_note,
        "uploaded_by_id": v.uploaded_by_id,
        "uploaded_at": iso(v.uploaded_at),
        "processing_status": v.processing_status,
        "processing_error": v.processing_error,
        "processed_at": iso(v.processed_at),
        "text_layer": v.text_layer,
        "ocr_used": v.ocr_used,
        "mean_ocr_confidence": v.mean_ocr_confidence,
        "metadata_info": v.metadata_info,
    }


def document(d: m.Document, *, extra: dict | None = None) -> dict:
    data = {
        "id": d.id,
        "institution_id": d.institution_id,
        "case_id": d.case_id,
        "category_key": d.category_key,
        "category_source": d.category_source,
        "classification": d.classification,
        "title": d.title,
        "academic_year": d.academic_year,
        "issuing_authority": d.issuing_authority,
        "status": d.status,
        "status_reason": d.status_reason,
        "check_summary": d.check_summary,
        "review_status": d.review_status,
        "reviewed_by_id": d.reviewed_by_id,
        "reviewed_at": iso(d.reviewed_at),
        "current_version_id": d.current_version_id,
        "is_historical": d.is_historical,
        "created_by_id": d.created_by_id,
        "created_at": iso(d.created_at),
        "updated_at": iso(d.updated_at),
    }
    if extra:
        data.update(extra)
    return data


def field(f: m.ExtractedField, corrections: list[m.FieldCorrection] | None = None) -> dict:
    corr = [
        {
            "id": c.id,
            "corrected_value": c.corrected_value,
            "reason": c.reason,
            "corrected_by_id": c.corrected_by_id,
            "corrected_at": iso(c.corrected_at),
        }
        for c in (corrections or [])
    ]
    return {
        "id": f.id,
        "version_id": f.version_id,
        "document_id": f.document_id,
        "field_key": f.field_key,
        "label": f.label,
        "value": f.value,
        "normalized_value": f.normalized_value,
        "original_text": f.original_text,
        "page_no": f.page_no,
        "bbox": f.bbox,
        "confidence": f.confidence,
        "confidence_basis": f.confidence_basis,
        "method": f.method,
        "validation_status": f.validation_status,
        "validation_message": f.validation_message,
        "group_index": f.group_index,
        "corrections": corr,
        "effective_value": corr[-1]["corrected_value"] if corr else f.value,
    }


def validation(r: m.ValidationResult) -> dict:
    return {
        "id": r.id,
        "run_id": r.run_id,
        "document_id": r.document_id,
        "version_id": r.version_id,
        "rule_key": r.rule_key,
        "rule_label": r.rule_label,
        "category": r.category,
        "status": r.status,
        "message": r.message,
        "details": r.details or {},
        "evidence": r.evidence or [],
        "created_at": iso(r.created_at),
    }


def integrity(c: m.IntegrityCheck) -> dict:
    return {
        "id": c.id,
        "run_id": c.run_id,
        "version_id": c.version_id,
        "check_type": c.check_type,
        "dimension": c.dimension,
        "status": c.status,
        "title": c.title,
        "summary": c.summary,
        "details": c.details or {},
        "evidence": c.evidence or [],
        "created_at": iso(c.created_at),
    }


def comparison(c: m.ComparisonResult) -> dict:
    return {
        "id": c.id,
        "run_id": c.run_id,
        "field_key": c.field_key,
        "label": c.label,
        "status": c.status,
        "values": c.values or [],
        "explanation": c.explanation,
        "created_at": iso(c.created_at),
    }


def attempt(a: m.ExternalVerificationAttempt) -> dict:
    return {
        "id": a.id,
        "integration_id": a.integration_id,
        "method": a.method,
        "institution_id": a.institution_id,
        "case_id": a.case_id,
        "document_id": a.document_id,
        "field_id": a.field_id,
        "claim_type": a.claim_type,
        "claim": a.claim or {},
        "request_summary": a.request_summary,
        "outcome": a.outcome,
        "reason": a.reason,
        "next_action": a.next_action,
        "http_status": a.http_status,
        "duration_ms": a.duration_ms,
        "source_name": a.source_name,
        "source_url": a.source_url,
        "response_sha256": a.response_sha256,
        "matched_values": a.matched_values,
        "evidence": a.evidence or [],
        "attempted_by_id": a.attempted_by_id,
        "attempted_at": iso(a.attempted_at),
    }


def integration(i: m.Integration) -> dict:
    from .integrations.secrets import secret_status

    return {
        "id": i.id,
        "key": i.key,
        "name": i.name,
        "purpose": i.purpose,
        "kind": i.kind,
        "adapter": i.adapter,
        "provider": i.provider,
        "source_owner": i.source_owner,
        "base_url": i.base_url,
        "auth_method": i.auth_method,
        "auth_header_name": i.auth_header_name,
        "secret_env_var": i.secret_env_var,
        "secret": secret_status(i),
        "config": i.config or {},
        "claim_types": i.claim_types or [],
        "documentation_url": i.documentation_url,
        "required_permissions": i.required_permissions,
        "coverage_notes": i.coverage_notes,
        "rate_limit_notes": i.rate_limit_notes,
        "privacy_notes": i.privacy_notes,
        "enabled": i.enabled,
        "connection_status": i.connection_status,
        "last_test_at": iso(i.last_test_at),
        "last_success_at": iso(i.last_success_at),
        "last_failure_at": iso(i.last_failure_at),
        "last_failure_reason": i.last_failure_reason,
        "builtin": i.builtin,
        "updated_at": iso(i.updated_at),
    }


def finding(f: m.Finding) -> dict:
    return {
        "id": f.id,
        "institution_id": f.institution_id,
        "case_id": f.case_id,
        "inspection_id": f.inspection_id,
        "document_id": f.document_id,
        "media_id": f.media_id,
        "criterion_id": f.criterion_id,
        "source": f.source,
        "category": f.category,
        "title": f.title,
        "description": f.description,
        "severity": f.severity,
        "severity_basis": f.severity_basis,
        "status": f.status,
        "evidence": f.evidence or [],
        "requires_physical_verification": f.requires_physical_verification,
        "created_by_id": f.created_by_id,
        "created_at": iso(f.created_at),
        "updated_at": iso(f.updated_at),
        "resolved_by_id": f.resolved_by_id,
        "resolved_at": iso(f.resolved_at),
        "resolution_note": f.resolution_note,
    }


def corrective(c: m.CorrectiveAction) -> dict:
    return {
        "id": c.id,
        "institution_id": c.institution_id,
        "finding_id": c.finding_id,
        "case_id": c.case_id,
        "inspection_id": c.inspection_id,
        "title": c.title,
        "description": c.description,
        "due_date": iso(c.due_date),
        "status": c.status,
        "evidence_document_id": c.evidence_document_id,
        "resolution_note": c.resolution_note,
        "created_by_id": c.created_by_id,
        "created_at": iso(c.created_at),
        "updated_at": iso(c.updated_at),
        "closed_by_id": c.closed_by_id,
        "closed_at": iso(c.closed_at),
    }


def decision(d: m.Decision, officers: dict[str, m.Officer] | None = None) -> dict:
    by = officers.get(d.decided_by_id) if officers else None
    return {
        "id": d.id,
        "institution_id": d.institution_id,
        "case_id": d.case_id,
        "document_id": d.document_id,
        "inspection_id": d.inspection_id,
        "decision_type": d.decision_type,
        "outcome": d.outcome,
        "reason": d.reason,
        "evidence": d.evidence or [],
        "supersedes_id": d.supersedes_id,
        "decided_by_id": d.decided_by_id,
        "decided_by_name": by.full_name if by else None,
        "decided_at": iso(d.decided_at),
        "has_snapshot": d.evaluation_snapshot is not None,
    }


def job(j: m.Job) -> dict:
    return {
        "id": j.id,
        "type": j.type,
        "status": j.status,
        "progress": j.progress,
        "progress_message": j.progress_message,
        "attempts": j.attempts,
        "max_attempts": j.max_attempts,
        "error": j.error,
        "entity_type": j.entity_type,
        "entity_id": j.entity_id,
        "institution_id": j.institution_id,
        "created_at": iso(j.created_at),
        "started_at": iso(j.started_at),
        "finished_at": iso(j.finished_at),
        "result": j.result,
    }


def audit_event(e: m.AuditEvent) -> dict:
    return {
        "id": e.id,
        "occurred_at": iso(e.occurred_at),
        "officer_id": e.officer_id,
        "officer_email": e.officer_email,
        "action": e.action,
        "entity_type": e.entity_type,
        "entity_id": e.entity_id,
        "institution_id": e.institution_id,
        "case_id": e.case_id,
        "summary": e.summary,
        "before": e.before,
        "after": e.after,
        "reason": e.reason,
        "evidence": e.evidence,
        "external_source": e.external_source,
        "outcome": e.outcome,
        "hash": e.hash,
        "prev_hash": e.prev_hash,
    }


def page_of(items: list[Any], total: int, page: int, page_size: int) -> dict:
    return {"items": items, "total": total, "page": page, "page_size": page_size}
