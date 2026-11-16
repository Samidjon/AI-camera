"""system settings table; role permission catalog update (MVP 07)

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-16
"""

from __future__ import annotations

import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSONType = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")

OPERATOR_V3 = ["cameras:read", "cameras:control", "events:read", "events:ack", "events:export",
               "observations:read", "devices:read"]  # fmt: skip
OPERATOR_V1 = ["cameras:read", "cameras:control", "events:read", "events:ack", "devices:read"]


def _set_operator(perms: list[str]) -> None:
    offline = context.is_offline_mode()
    perms_type = sa.Text() if offline else JSONType
    roles = sa.table("roles", sa.column("name", sa.String()), sa.column("permissions", perms_type))
    op.execute(
        roles.update()
        .where(roles.c.name == "operator")
        .values(permissions=json.dumps(perms) if offline else perms)
    )


def upgrade() -> None:
    op.create_table(
        "system_settings",
        sa.Column("key", sa.String(64), nullable=False),
        sa.Column("value", JSONType, nullable=False),
        sa.Column("updated_by", sa.Uuid()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("key", name="pk_system_settings"),
        sa.ForeignKeyConstraint(
            ["updated_by"], ["users.id"], name="fk_system_settings_updated_by_users", ondelete="SET NULL"
        ),
    )
    # Operators may export events and see anonymous observations (privacy: viewers may not).
    _set_operator(OPERATOR_V3)
    if op.get_bind().dialect.name == "postgresql":
        op.execute('ALTER TABLE "system_settings" ENABLE ROW LEVEL SECURITY')


def downgrade() -> None:
    _set_operator(OPERATOR_V1)
    op.drop_table("system_settings")
