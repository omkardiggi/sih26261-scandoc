"""Append-only protection for audit events and officer decisions.

Revision ID: b1a2c3d4e5f6
Revises: 32ae8feb9a2f
"""

from alembic import op

revision = "b1a2c3d4e5f6"
down_revision = "32ae8feb9a2f"
branch_labels = None
depends_on = None

TABLES = ("audit_events", "decisions")


def upgrade() -> None:
    dialect = op.get_bind().dialect.name
    for t in TABLES:
        if dialect == "sqlite":
            for action in ("UPDATE", "DELETE"):
                op.execute(f"CREATE TRIGGER {t}_no_{action.lower()} BEFORE {action} ON {t} "
                           f"BEGIN SELECT RAISE(ABORT, '{t} is append-only'); END;")
        elif dialect == "postgresql":
            op.execute(f"CREATE OR REPLACE FUNCTION {t}_append_only() RETURNS trigger AS $$ BEGIN "
                       f"RAISE EXCEPTION '{t} is append-only'; END; $$ LANGUAGE plpgsql;")
            op.execute(f"CREATE TRIGGER {t}_no_modify BEFORE UPDATE OR DELETE ON {t} FOR EACH ROW EXECUTE FUNCTION {t}_append_only();")


def downgrade() -> None:
    dialect = op.get_bind().dialect.name
    for t in TABLES:
        if dialect == "sqlite":
            op.execute(f"DROP TRIGGER IF EXISTS {t}_no_update")
            op.execute(f"DROP TRIGGER IF EXISTS {t}_no_delete")
        elif dialect == "postgresql":
            op.execute(f"DROP TRIGGER IF EXISTS {t}_no_modify ON {t}")
            op.execute(f"DROP FUNCTION IF EXISTS {t}_append_only()")
