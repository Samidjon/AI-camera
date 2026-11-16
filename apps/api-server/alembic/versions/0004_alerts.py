"""alerts: alert rules, alerts, notification deliveries (MVP 08)

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-16
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSONType = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")
BigIntPK = sa.BigInteger().with_variant(sa.Integer(), "sqlite")
TABLES = ["alert_rules", "alerts", "notification_deliveries"]
KINDS = ("camera_offline", "camera_reconnected", "stream_error", "ai_offline", "ai_recovered", "model_error",
         "high_cpu", "high_memory", "high_temperature", "low_disk", "device_offline", "motion_detected",
         "zone_activity", "line_crossed")  # fmt: skip


def _in(values) -> str:
    return ", ".join(f"'{v}'" for v in values)


def _now(name: str) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)


def upgrade() -> None:
    op.create_table(
        "alert_rules",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("severity", sa.String(10), nullable=False),
        sa.Column("threshold", sa.Float()),
        sa.Column("device_id", sa.Uuid()),
        sa.Column("camera_ref", sa.String(64)),
        sa.Column("zone_id", sa.String(64)),
        sa.Column("line_id", sa.String(64)),
        sa.Column("cooldown_s", sa.Integer(), nullable=False),
        sa.Column("channels", JSONType, nullable=False),
        sa.Column("email_recipients", JSONType, nullable=False),
        sa.Column("created_by", sa.Uuid()),
        _now("created_at"),
        _now("updated_at"),
        sa.PrimaryKeyConstraint("id", name="pk_alert_rules"),
        sa.ForeignKeyConstraint(
            ["device_id"], ["edge_devices.id"], name="fk_alert_rules_device_id_edge_devices", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name="fk_alert_rules_created_by_users", ondelete="SET NULL"
        ),
        sa.CheckConstraint(f"kind IN ({_in(KINDS)})", name="ck_alert_rules_kind_valid"),
        sa.CheckConstraint("severity IN ('info', 'warning', 'critical')", name="ck_alert_rules_severity_valid"),
    )
    op.create_index("ix_alert_rules_kind", "alert_rules", ["kind"])

    op.create_table(
        "alerts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("rule_id", sa.Uuid()),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("severity", sa.String(10), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("device_id", sa.Uuid()),
        sa.Column("camera_ref", sa.String(64)),
        sa.Column("scope_key", sa.String(200), nullable=False),
        sa.Column("context", JSONType, nullable=False),
        sa.Column("status", sa.String(15), nullable=False),
        _now("created_at"),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True)),
        sa.Column("acknowledged_by", sa.Uuid()),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.PrimaryKeyConstraint("id", name="pk_alerts"),
        sa.ForeignKeyConstraint(
            ["rule_id"], ["alert_rules.id"], name="fk_alerts_rule_id_alert_rules", ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["device_id"], ["edge_devices.id"], name="fk_alerts_device_id_edge_devices", ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["acknowledged_by"], ["users.id"], name="fk_alerts_acknowledged_by_users", ondelete="SET NULL"
        ),
        sa.CheckConstraint("status IN ('open', 'acknowledged', 'resolved')", name="ck_alerts_status_valid"),
    )
    op.create_index("ix_alerts_rule_id_created_at", "alerts", ["rule_id", "created_at"])
    op.create_index("ix_alerts_status", "alerts", ["status"])
    op.create_index("ix_alerts_created_at", "alerts", ["created_at"])

    op.create_table(
        "notification_deliveries",
        sa.Column("id", BigIntPK, autoincrement=True, nullable=False),
        sa.Column("alert_id", sa.Uuid(), nullable=False),
        sa.Column("channel", sa.String(20), nullable=False),
        sa.Column("recipient", sa.String(255)),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("error", sa.String(300)),
        _now("created_at"),
        sa.Column("sent_at", sa.DateTime(timezone=True)),
        sa.PrimaryKeyConstraint("id", name="pk_notification_deliveries"),
        sa.ForeignKeyConstraint(
            ["alert_id"], ["alerts.id"], name="fk_notification_deliveries_alert_id_alerts", ondelete="CASCADE"
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'sent', 'failed', 'skipped')", name="ck_notification_deliveries_status_valid"
        ),
    )
    op.create_index("ix_notification_deliveries_alert_id", "notification_deliveries", ["alert_id"])

    if op.get_bind().dialect.name == "postgresql":
        for table in TABLES:
            op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')


def downgrade() -> None:
    for table in reversed(TABLES):
        op.drop_table(table)
