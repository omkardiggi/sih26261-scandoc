"""Document category groups (for the per-institution document checklist).

Revision ID: c2d3e4f5a6b7
Revises: b1a2c3d4e5f6
"""

import sqlalchemy as sa
from alembic import op

revision = "c2d3e4f5a6b7"
down_revision = "b1a2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("document_categories") as b:
        b.add_column(sa.Column("group_name", sa.String(80), nullable=True))
        b.add_column(sa.Column("icon", sa.String(40), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("document_categories") as b:
        b.drop_column("icon")
        b.drop_column("group_name")
