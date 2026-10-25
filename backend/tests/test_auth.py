"""Authentication, single role, provisioning and authorization (acceptance criteria 1-4, 25)."""

import re

from sqlalchemy import text

from conftest import PASSWORD, login, make_client, new_officer


def test_login_logout_and_protected_routes(app):
    email = new_officer()
    c = make_client(app)
    assert c.get("/api/institutions").status_code == 401  # unauthenticated blocked
    assert login(c, email, "wrong-password-123").status_code == 401
    r = login(c, email)
    assert r.status_code == 200 and r.json()["mfa_required"] is False
    assert c.get("/api/auth/me").json()["officer"]["role"] == "AICTE Officer"
    assert c.get("/api/institutions").status_code == 200
    assert c.post("/api/auth/logout").status_code == 200
    assert c.get("/api/institutions").status_code == 401


def test_session_cookie_flags(app):
    email = new_officer()
    c = make_client(app)
    r = login(c, email)
    cookie = r.headers.get("set-cookie", "")
    assert "HttpOnly" in cookie and "SameSite=strict" in cookie.replace("Strict", "strict")


def test_csrf_required_for_mutations(app):
    email = new_officer()
    c = make_client(app)
    login(c, email)
    token = c.headers.pop("X-CSRF-Token")
    assert c.post("/api/institutions", json={"name": "Synthetic CSRF Test"}).status_code == 403
    c.headers["X-CSRF-Token"] = token
    assert c.post("/api/institutions", json={"name": "Synthetic CSRF Test"}).status_code == 201


def test_single_role_enforced_by_database(app):
    from app.db import session_scope

    new_officer()
    with session_scope() as db:
        roles = {r for (r,) in db.execute(text("SELECT DISTINCT role FROM officers"))}
        assert roles == {"AICTE_OFFICER"}
        try:
            with db.begin_nested():
                db.execute(text("UPDATE officers SET role='ADMIN'"))
            raised = False
        except Exception:  # noqa: BLE001
            raised = True
        assert raised, "CHECK constraint must reject any other role"


def test_login_rate_limit(app):
    email = new_officer()
    c = make_client(app)
    for _ in range(5):
        assert login(c, email, "bad-password-xyz").status_code == 401
    assert login(c, email).status_code == 429  # locked even with the right password


def test_no_public_registration_and_invite_flow(app, officer_client):
    assert make_client(app).post("/api/officers", json={"email": "x@example.test", "full_name": "X"}).status_code == 401
    r = officer_client.post("/api/officers", json={"email": "invitee@example.test", "full_name": "Invited Person"})
    assert r.status_code == 201
    link = r.json()["link"]  # dev/test: no SMTP, link handed to the inviting officer
    token = re.search(r"token=([\w\-]+)", link).group(1)
    anon = make_client(app)
    assert anon.post("/api/auth/invite/accept", json={"token": token, "password": "short"}).status_code == 422
    assert anon.post("/api/auth/invite/accept", json={"token": token, "password": PASSWORD}).status_code == 200
    assert anon.post("/api/auth/invite/accept", json={"token": token, "password": PASSWORD}).status_code == 400  # single use
    assert login(anon, "invitee@example.test").status_code == 200


def test_password_reset_does_not_leak_accounts(app):
    email = new_officer()
    c = make_client(app)
    a = c.post("/api/auth/password-reset/request", json={"email": email})
    b = c.post("/api/auth/password-reset/request", json={"email": "nobody@example.test"})
    assert a.status_code == b.status_code == 202 and a.json()["message"] == b.json()["message"]


def test_mfa_enrolment_and_login(app):
    from app.security.totp import totp_now

    email = new_officer()
    c = make_client(app)
    login(c, email)
    secret = c.post("/api/auth/mfa/setup").json()["secret"]
    assert c.post("/api/auth/mfa/enable", json={"code": totp_now(secret)}).status_code == 200
    c2 = make_client(app)
    r = login(c2, email)
    assert r.json()["mfa_required"] is True
    assert c2.get("/api/institutions").status_code == 401  # MFA pending
    import time

    assert c2.post("/api/auth/mfa/verify", json={"code": totp_now(secret, at=time.time() + 30)}).status_code == 200
    assert c2.get("/api/institutions").status_code == 200


def test_totp_rfc6238_vector():
    import base64

    from app.security.totp import totp_now

    secret = base64.b32encode(b"12345678901234567890").decode()
    assert totp_now(secret, at=59) == "287082"  # RFC 6238 SHA1 test vector (last 6 digits of 94287082)


def test_self_service_signup_any_email(app):
    from app.config import get_settings

    c = make_client(app)
    assert c.get("/api/auth/signup-config").json()["enabled"] is True
    body = {"full_name": "Synthetic Signup", "email": "Synthetic.Signup.1@Gmail.com", "password": "Quartz-Lantern-2026!"}
    r = c.post("/api/auth/signup", json=body)
    assert r.status_code == 200, r.text
    assert r.json()["officer"]["email"] == "synthetic.signup.1@gmail.com"
    assert c.get("/api/auth/me").status_code == 200  # signed in straight away
    # the same address cannot register twice (any letter case); it can sign in
    assert make_client(app).post("/api/auth/signup", json=body | {"email": "SYNTHETIC.SIGNUP.1@gmail.com"}).status_code == 409
    assert login(make_client(app), "synthetic.signup.1@gmail.com", "Quartz-Lantern-2026!").status_code == 200
    # weak passwords and malformed addresses are refused
    assert make_client(app).post("/api/auth/signup", json=body | {"email": "synthetic.signup.2@gmail.com", "password": "short"}).status_code == 422
    assert make_client(app).post("/api/auth/signup", json=body | {"email": "not-an-email"}).status_code == 422
    # optional domain restriction
    s = get_settings()
    s.signup_allowed_domains = "example.gov.in"
    try:
        assert make_client(app).post("/api/auth/signup", json=body | {"email": "synthetic.signup.3@gmail.com"}).status_code == 422
    finally:
        s.signup_allowed_domains = ""
