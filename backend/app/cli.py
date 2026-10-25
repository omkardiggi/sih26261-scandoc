"""Operator CLI: key generation, secure officer provisioning, worker, backup/restore, audit verification.

Usage: python -m app.cli <command> [options]
"""

from __future__ import annotations

import argparse
import getpass
import shutil
import sqlite3
import sys
import tarfile
from datetime import datetime
from pathlib import Path

from .config import BACKEND_DIR, get_settings
from .security.crypto import generate_key_b64


def cmd_generate_keys(_args) -> None:
    print("# Add these to backend/.env (keep them secret; losing STORAGE_ENCRYPTION_KEY makes stored files unreadable)")
    for name in ("STORAGE_ENCRYPTION_KEY", "SECRETS_ENCRYPTION_KEY", "SIGNING_KEY"):
        print(f"{name}={generate_key_b64()}")


def cmd_init_env(args) -> None:
    env = BACKEND_DIR / ".env"
    if env.exists() and not args.force:
        print(f"{env} already exists; not overwritten (use --force to replace).")
        return
    example = (BACKEND_DIR.parent / ".env.example").read_text(encoding="utf-8")
    keys = {n: generate_key_b64() for n in ("STORAGE_ENCRYPTION_KEY", "SECRETS_ENCRYPTION_KEY", "SIGNING_KEY")}
    lines = []
    for line in example.splitlines():
        name = line.split("=", 1)[0].strip()
        lines.append(f"{name}={keys[name]}" if name in keys else line)
    env.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Created {env} with freshly generated keys.")


def _session():
    from .db import session_factory

    return session_factory()()


def cmd_create_officer(args) -> None:
    """Create an officer interactively. The password is read from the terminal and never echoed or logged."""
    from . import audit
    from .services.officers import ProvisioningError, create_officer

    password = getpass.getpass("Password (min 12 chars, 3 character classes): ")
    if password != getpass.getpass("Repeat password: "):
        sys.exit("Passwords do not match.")
    with _session() as db:
        try:
            officer = create_officer(db, email=args.email, full_name=args.name, designation=args.designation, password=password)
        except ProvisioningError as exc:
            sys.exit(str(exc))
        audit.record(db, "officer.provisioned_cli", entity_type="officer", entity_id=officer.id,
                     summary=f"Officer account created via operator CLI for {officer.email}")
        db.commit()
        print(f"Officer account created for {officer.email}.")


def cmd_issue_link(args) -> None:
    """Create an officer (if needed) and print a single-use setup/reset link to the operator's terminal."""
    from sqlalchemy import select

    from . import audit
    from .models import Officer
    from .services.officers import ProvisioningError, create_officer, issue_token, link_for

    with _session() as db:
        officer = db.execute(select(Officer).where(Officer.email == args.email.strip().lower())).scalar_one_or_none()
        if officer is None:
            if not args.name:
                sys.exit("No such officer. Provide --name to create the account.")
            try:
                officer = create_officer(db, email=args.email, full_name=args.name, designation=args.designation)
            except ProvisioningError as exc:
                sys.exit(str(exc))
        purpose = "password_reset" if officer.password_hash else "invite"
        raw = issue_token(db, officer, purpose)
        audit.record(db, f"officer.{purpose}_issued_cli", entity_type="officer", entity_id=officer.id,
                     summary="Single-use link issued via operator CLI")
        db.commit()
        print(f"Single-use {'setup' if purpose == 'invite' else 'password reset'} link for {officer.email} "
              f"(valid {'48 hours' if purpose == 'invite' else '1 hour'}):\n{link_for(purpose, raw)}")


def cmd_worker(_args) -> None:
    import logging
    import time

    from .jobs.worker import start_workers, stop_workers

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    start_workers()
    print("Worker running. Press Ctrl+C to stop.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        stop_workers()


def cmd_run_jobs(args) -> None:
    from .jobs.worker import run_pending_jobs

    print(f"Processed {run_pending_jobs(args.limit)} job(s).")


def cmd_verify_audit(_args) -> None:
    from .audit import verify_chain

    with _session() as db:
        r = verify_chain(db)
    print(("Audit chain intact" if r["ok"] else "AUDIT CHAIN BROKEN") + f" — {r['events_checked']} events checked.")
    if not r["ok"]:
        print(r["first_break"])
        sys.exit(2)


def cmd_backup(args) -> None:
    s = get_settings()
    if not s.database_url.startswith("sqlite:///"):
        sys.exit("For PostgreSQL use pg_dump (see docs/DEPLOYMENT.md); this command backs up SQLite deployments.")
    out_dir = Path(args.out or BACKEND_DIR / "var" / "backups")
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    db_path = Path(s.database_url.removeprefix("sqlite:///"))
    tmp_db = out_dir / f"app-{stamp}.db"
    with sqlite3.connect(db_path) as src, sqlite3.connect(tmp_db) as dst:
        src.backup(dst)  # consistent online snapshot
    archive = out_dir / f"backup-{stamp}.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(tmp_db, arcname="app.db")
        tar.add(s.storage_dir, arcname="storage")
    tmp_db.unlink()
    print(f"Backup written to {archive}. Files remain encrypted; keep backend/.env keys backed up separately and securely.")


def cmd_restore(args) -> None:
    s = get_settings()
    archive = Path(args.archive)
    if not archive.exists():
        sys.exit("Archive not found.")
    if not args.yes:
        sys.exit("Restoring replaces the current database and storage. Re-run with --yes to confirm.")
    db_path = Path(s.database_url.removeprefix("sqlite:///"))
    staging = BACKEND_DIR / "var" / f"restore-{datetime.now():%Y%m%d%H%M%S}"
    with tarfile.open(archive, "r:gz") as tar:
        tar.extractall(staging, filter="data")
    if db_path.exists():
        shutil.move(str(db_path), str(db_path) + ".pre-restore")
    shutil.move(str(staging / "app.db"), str(db_path))
    if s.storage_dir.exists():
        shutil.move(str(s.storage_dir), str(s.storage_dir) + ".pre-restore")
    shutil.move(str(staging / "storage"), str(s.storage_dir))
    shutil.rmtree(staging, ignore_errors=True)
    print("Restore complete. Previous data kept with the .pre-restore suffix.")


def cmd_retention(_args) -> None:
    """Purge expired sessions, old login attempts and used tokens per configured retention."""
    from datetime import timedelta

    from sqlalchemy import delete

    from .models import AuthToken, LoginAttempt, OfficerSession, utcnow

    s = get_settings()
    now = utcnow()
    with _session() as db:
        a = db.execute(delete(OfficerSession).where(OfficerSession.expires_at < now - timedelta(days=s.session_retention_days))).rowcount
        b = db.execute(delete(LoginAttempt).where(LoginAttempt.created_at < now - timedelta(days=s.login_attempt_retention_days))).rowcount
        c = db.execute(delete(AuthToken).where(AuthToken.expires_at < now - timedelta(days=30))).rowcount
        db.commit()
    tmp = s.storage_dir.parent / "tmp"
    if tmp.exists():
        for f in tmp.iterdir():
            if f.is_file() and (datetime.now().timestamp() - f.stat().st_mtime) > 86400:
                f.unlink()
    print(f"Removed {a} expired sessions, {b} old login attempts, {c} expired tokens. Evidence and audit records are retained.")


def cmd_add_workflow_model(args) -> None:
    """Register a Roboflow Workflow, enable the provider (key read from ROBOFLOW_API_KEY) and run a real test request."""
    from sqlalchemy import select

    from . import audit
    from .integrations.roboflow import test_model
    from .models import Integration, ModelConfig, utcnow

    with _session() as db:
        integ = db.execute(select(Integration).where(Integration.adapter == "roboflow")).scalar_one_or_none()
        if integ is None:
            sys.exit("Inference provider integration missing; start the app once to seed configuration.")
        m = db.execute(select(ModelConfig).where(ModelConfig.project_id == args.workspace, ModelConfig.model_version == args.workflow)).scalar_one_or_none()
        if m is None:
            m = ModelConfig(name=args.name, integration_id=integ.id, project_id=args.workspace, model_version=args.workflow,
                            task_type="workflow", description=args.description, classes=[], class_rules={}, enabled=True,
                            input_formats=["jpeg", "png"], facility_categories=[])
            db.add(m)
        integ.enabled = True
        integ.secret_env_var = integ.secret_env_var or "ROBOFLOW_API_KEY"
        db.flush()
        res = test_model(integ, m)
        m.last_test_at = utcnow()
        m.status = "operational" if res.ok else ("auth_failed" if res.status == "auth_failed" else "failed")
        m.last_error = None if res.ok else res.message
        if res.ok:
            integ.connection_status, integ.last_success_at = "operational", utcnow()
        audit.record(db, "model.configured_cli", entity_type="model_config", entity_id=m.id, outcome=m.status, summary=res.message[:300],
                     model_info={"workspace": args.workspace, "workflow": args.workflow})
        db.commit()
        print(f"{args.name}: {m.status} — {res.message}")


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="python -m app.cli")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("generate-keys").set_defaults(fn=cmd_generate_keys)
    ie = sub.add_parser("init-env")
    ie.add_argument("--force", action="store_true")
    ie.set_defaults(fn=cmd_init_env)
    co = sub.add_parser("create-officer")
    co.add_argument("--email", required=True)
    co.add_argument("--name", required=True)
    co.add_argument("--designation")
    co.set_defaults(fn=cmd_create_officer)
    il = sub.add_parser("issue-link", help="Print a single-use account setup or password reset link")
    il.add_argument("--email", required=True)
    il.add_argument("--name")
    il.add_argument("--designation")
    il.set_defaults(fn=cmd_issue_link)
    sub.add_parser("worker").set_defaults(fn=cmd_worker)
    rj = sub.add_parser("run-jobs")
    rj.add_argument("--limit", type=int, default=100)
    rj.set_defaults(fn=cmd_run_jobs)
    sub.add_parser("verify-audit").set_defaults(fn=cmd_verify_audit)
    bk = sub.add_parser("backup")
    bk.add_argument("--out")
    bk.set_defaults(fn=cmd_backup)
    rs = sub.add_parser("restore")
    rs.add_argument("archive")
    rs.add_argument("--yes", action="store_true")
    rs.set_defaults(fn=cmd_restore)
    sub.add_parser("retention").set_defaults(fn=cmd_retention)
    aw = sub.add_parser("add-workflow-model", help="Register a Roboflow Workflow for inspection analysis")
    aw.add_argument("--name", required=True)
    aw.add_argument("--workspace", required=True)
    aw.add_argument("--workflow", required=True)
    aw.add_argument("--description")
    aw.set_defaults(fn=cmd_add_workflow_model)
    args = p.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
