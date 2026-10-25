"""Officer account provisioning (all accounts share the single AICTE Officer role)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import audit
from .. import serializers as ser
from ..db import get_db
from ..deps import Auth, require_officer
from ..mailer import mail_transport, send_mail
from ..models import Officer, utcnow
from ..security.sessions import revoke_all
from ..services.officers import ProvisioningError, create_officer, issue_token, link_for

router = APIRouter(prefix="/api/officers", tags=["officers"])


class InviteIn(BaseModel):
    email: str = Field(max_length=320)
    full_name: str = Field(max_length=200)
    designation: str | None = Field(default=None, max_length=200)


class ReasonIn(BaseModel):
    reason: str = Field(min_length=3, max_length=2000)


@router.get("")
def list_officers(auth: Auth = Depends(require_officer), db: Session = Depends(get_db)) -> dict:
    rows = db.execute(select(Officer).order_by(Officer.full_name)).scalars().all()
    return {"items": [ser.officer(o, full=True) for o in rows]}


def _deliver(db: Session, officer: Officer, purpose: str, by: Officer) -> dict:
    raw = issue_token(db, officer, purpose, created_by=by)
    link = link_for(purpose, raw)
    if purpose == "invite":
        subject = "Your officer account invitation"
        body = (f"{by.full_name} has created an officer account for you.\n\n"
                f"Open this link within 48 hours to set your password:\n{link}\n")
    else:
        subject = "Password reset issued for your officer account"
        body = f"A password reset link was issued for your account.\n\nOpen within 1 hour:\n{link}\n"
    transport = mail_transport()
    sent = send_mail(officer.email, subject, body)
    out = {"delivery": transport if sent else "not_sent"}
    if transport != "smtp":
        # Without SMTP the issuing officer must hand the link over through a secure channel.
        out["link"] = link
        out["note"] = "Email delivery is not configured. Share this single-use link with the officer through a secure channel."
    return out


@router.post("", status_code=201)
def invite_officer(body: InviteIn, request: Request, auth: Auth = Depends(require_officer), db: Session = Depends(get_db)) -> dict:
    try:
        officer = create_officer(db, email=body.email, full_name=body.full_name, designation=body.designation, created_by=auth.officer)
    except ProvisioningError as exc:
        raise HTTPException(422, str(exc)) from exc
    delivery = _deliver(db, officer, "invite", auth.officer)
    audit.record(db, "officer.invited", officer=auth.officer, entity_type="officer", entity_id=officer.id,
                 after=ser.officer(officer), outcome=delivery["delivery"], ctx=auth.ctx)
    db.commit()
    return {"officer": ser.officer(officer, full=True), **delivery}


@router.post("/{officer_id}/reset-link")
def issue_reset(officer_id: str, request: Request, auth: Auth = Depends(require_officer), db: Session = Depends(get_db)) -> dict:
    officer = db.get(Officer, officer_id)
    if officer is None or not officer.is_active:
        raise HTTPException(404, "Active officer not found")
    purpose = "password_reset" if officer.password_hash else "invite"
    delivery = _deliver(db, officer, purpose, auth.officer)
    audit.record(db, f"officer.{purpose}_issued", officer=auth.officer, entity_type="officer", entity_id=officer.id,
                 outcome=delivery["delivery"], ctx=auth.ctx)
    db.commit()
    return delivery


@router.post("/{officer_id}/deactivate")
def deactivate(officer_id: str, body: ReasonIn, auth: Auth = Depends(require_officer), db: Session = Depends(get_db)) -> dict:
    officer = db.get(Officer, officer_id)
    if officer is None:
        raise HTTPException(404, "Officer not found")
    if officer.id == auth.officer.id:
        raise HTTPException(400, "You cannot deactivate your own account.")
    active = db.execute(select(func.count()).select_from(Officer).where(Officer.is_active.is_(True))).scalar_one()
    if officer.is_active and active <= 1:
        raise HTTPException(400, "At least one active officer account must remain.")
    officer.is_active = False
    officer.deactivated_at = utcnow()
    revoke_all(db, officer.id)
    audit.record(db, "officer.deactivated", officer=auth.officer, entity_type="officer", entity_id=officer.id,
                 reason=body.reason, ctx=auth.ctx)
    db.commit()
    return {"officer": ser.officer(officer, full=True)}


@router.post("/{officer_id}/reactivate")
def reactivate(officer_id: str, body: ReasonIn, auth: Auth = Depends(require_officer), db: Session = Depends(get_db)) -> dict:
    officer = db.get(Officer, officer_id)
    if officer is None:
        raise HTTPException(404, "Officer not found")
    officer.is_active = True
    officer.deactivated_at = None
    audit.record(db, "officer.reactivated", officer=auth.officer, entity_type="officer", entity_id=officer.id,
                 reason=body.reason, ctx=auth.ctx)
    db.commit()
    return {"officer": ser.officer(officer, full=True)}
