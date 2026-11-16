"""edge devices: devices, groups, credentials, enrollment tokens, heartbeats, sync logs,
configurations, event statistics; cameras/camera_events linked to devices

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-16
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSONType = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")
BigIntPK = sa.BigInteger().with_variant(sa.Integer(), "sqlite")
NEW_TABLES = [
    "device_groups",
    "edge_devices",
    "device_credentials",
    "device_enrollment_tokens",
    "device_heartbeats",
    "synchronization_logs",
    "device_configurations",
    "event_statistics",
]
DEVICE_STATUSES = "'online', 'offline', 'degraded', 'synchronizing', 'error', 'revoked', 'pending'"
CONFIG_STATUSES = "'pending', 'applied', 'partial', 'failed', 'superseded'"


def _ts(name: str, nullable: bool = False) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=nullable)


def _dt(name: str) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True))


def upgrade() -> None:
    op.create_table(
        "device_groups",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("description", sa.Text()),
        _ts("created_at"),
        _ts("updated_at"),
        sa.PrimaryKeyConstraint("id", name="pk_device_groups"),
        sa.UniqueConstraint("name", name="uq_device_groups_name"),
    )

    op.create_table(
        "edge_devices",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("group_id", sa.Uuid()),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("status_reason", sa.String(300)),
        _dt("status_changed_at"),
        sa.Column("agent_version", sa.String(40)),
        sa.Column("hardware", JSONType, nullable=False),
        sa.Column("metrics", JSONType, nullable=False),
        sa.Column("ai_status", JSONType, nullable=False),
        sa.Column("camera_status", JSONType, nullable=False),
        sa.Column("sync_status", JSONType, nullable=False),
        sa.Column("heartbeat_interval_s", sa.Integer(), nullable=False),
        _dt("last_heartbeat_at"),
        sa.Column("last_seen_ip", sa.String(45)),
        sa.Column("desired_config_version", sa.Integer(), nullable=False),
        sa.Column("applied_config_version", sa.Integer(), nullable=False),
        sa.Column("rotate_credentials", sa.Boolean(), nullable=False),
        _dt("registered_at"),
        _dt("revoked_at"),
        _ts("created_at"),
        _ts("updated_at"),
        sa.PrimaryKeyConstraint("id", name="pk_edge_devices"),
        sa.ForeignKeyConstraint(
            ["group_id"], ["device_groups.id"], name="fk_edge_devices_group_id_device_groups", ondelete="SET NULL"
        ),
        sa.CheckConstraint(f"status IN ({DEVICE_STATUSES})", name="ck_edge_devices_status_valid"),
    )
    op.create_index("ix_edge_devices_group_id", "edge_devices", ["group_id"])
    op.create_index("ix_edge_devices_status", "edge_devices", ["status"])
    op.create_index("ix_edge_devices_last_heartbeat_at", "edge_devices", ["last_heartbeat_at"])

    op.create_table(
        "device_credentials",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("device_id", sa.Uuid(), nullable=False),
        sa.Column("secret_hash", sa.String(64), nullable=False),
        sa.Column("origin", sa.String(20), nullable=False),
        _ts("created_at"),
        _dt("last_used_at"),
        _dt("expires_at"),
        _dt("revoked_at"),
        sa.PrimaryKeyConstraint("id", name="pk_device_credentials"),
        sa.UniqueConstraint("secret_hash", name="uq_device_credentials_secret_hash"),
        sa.ForeignKeyConstraint(
            ["device_id"], ["edge_devices.id"], name="fk_device_credentials_device_id_edge_devices", ondelete="CASCADE"
        ),
    )
    op.create_index("ix_device_credentials_device_id", "device_credentials", ["device_id"])

    op.create_table(
        "device_enrollment_tokens",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(120)),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("group_id", sa.Uuid()),
        sa.Column("created_by", sa.Uuid()),
        _ts("created_at"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("max_uses", sa.Integer(), nullable=False),
        sa.Column("uses", sa.Integer(), nullable=False),
        _dt("revoked_at"),
        sa.PrimaryKeyConstraint("id", name="pk_device_enrollment_tokens"),
        sa.UniqueConstraint("token_hash", name="uq_device_enrollment_tokens_token_hash"),
        sa.ForeignKeyConstraint(
            ["group_id"],
            ["device_groups.id"],
            name="fk_device_enrollment_tokens_group_id_device_groups",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name="fk_device_enrollment_tokens_created_by_users", ondelete="SET NULL"
        ),
    )

    op.create_table(
        "device_heartbeats",
        sa.Column("id", BigIntPK, autoincrement=True, nullable=False),
        sa.Column("device_id", sa.Uuid(), nullable=False),
        _ts("received_at"),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("agent_version", sa.String(40)),
        sa.Column("cameras_online", sa.Integer(), nullable=False),
        sa.Column("cameras_total", sa.Integer(), nullable=False),
        sa.Column("ai_mode", sa.String(20)),
        sa.Column("queue_pending", sa.Integer(), nullable=False),
        sa.Column("metrics", JSONType, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_device_heartbeats"),
        sa.ForeignKeyConstraint(
            ["device_id"], ["edge_devices.id"], name="fk_device_heartbeats_device_id_edge_devices", ondelete="CASCADE"
        ),
    )
    op.create_index(
        "ix_device_heartbeats_device_id_received_at", "device_heartbeats", ["device_id", "received_at"]
    )

    op.create_table(
        "synchronization_logs",
        sa.Column("id", BigIntPK, autoincrement=True, nullable=False),
        sa.Column("device_id", sa.Uuid()),
        sa.Column("kind", sa.String(30), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("received", sa.Integer(), nullable=False),
        sa.Column("accepted", sa.Integer(), nullable=False),
        sa.Column("duplicate", sa.Integer(), nullable=False),
        sa.Column("rejected", sa.Integer(), nullable=False),
        sa.Column("duration_ms", sa.Integer()),
        sa.Column("details", JSONType, nullable=False),
        _ts("created_at"),
        sa.PrimaryKeyConstraint("id", name="pk_synchronization_logs"),
        sa.ForeignKeyConstraint(
            ["device_id"],
            ["edge_devices.id"],
            name="fk_synchronization_logs_device_id_edge_devices",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_synchronization_logs_device_id_created_at", "synchronization_logs", ["device_id", "created_at"]
    )

    op.create_table(
        "device_configurations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("device_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("config", JSONType, nullable=False),
        sa.Column("checksum", sa.String(64), nullable=False),
        sa.Column("comment", sa.String(300)),
        sa.Column("rollback_of", sa.Integer()),
        sa.Column("created_by", sa.Uuid()),
        _ts("created_at"),
        sa.Column("status", sa.String(20), nullable=False),
        _dt("acknowledged_at"),
        sa.Column("ack", JSONType, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_device_configurations"),
        sa.UniqueConstraint("device_id", "version", name="uq_device_configurations_device_id_version"),
        sa.ForeignKeyConstraint(
            ["device_id"],
            ["edge_devices.id"],
            name="fk_device_configurations_device_id_edge_devices",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name="fk_device_configurations_created_by_users", ondelete="SET NULL"
        ),
        sa.CheckConstraint(f"status IN ({CONFIG_STATUSES})", name="ck_device_configurations_status_valid"),
    )
    op.create_index("ix_device_configurations_device_id", "device_configurations", ["device_id"])

    op.create_table(
        "event_statistics",
        sa.Column("id", BigIntPK, autoincrement=True, nullable=False),
        sa.Column("bucket_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("device_id", sa.Uuid(), nullable=False),
        sa.Column("camera_ref", sa.String(64), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_event_statistics"),
        sa.UniqueConstraint(
            "bucket_start", "device_id", "camera_ref", "event_type", name="uq_event_statistics_bucket"
        ),
        sa.ForeignKeyConstraint(
            ["device_id"], ["edge_devices.id"], name="fk_event_statistics_device_id_edge_devices", ondelete="CASCADE"
        ),
    )
    op.create_index("ix_event_statistics_bucket_start", "event_statistics", ["bucket_start"])

    # Existing tables: cameras become the registry reported by devices; events know their device.
    with op.batch_alter_table("cameras") as batch:
        batch.add_column(sa.Column("device_id", sa.Uuid()))
        batch.add_column(sa.Column("external_id", sa.String(64)))
        batch.add_column(sa.Column("ai_enabled", sa.Boolean()))
        batch.add_column(sa.Column("last_error", sa.String(300)))
        batch.create_foreign_key(
            "fk_cameras_device_id_edge_devices", "edge_devices", ["device_id"], ["id"], ondelete="CASCADE"
        )
        batch.create_unique_constraint("uq_cameras_device_id_external_id", ["device_id", "external_id"])
        batch.create_index("ix_cameras_device_id", ["device_id"])

    with op.batch_alter_table("camera_events") as batch:
        batch.add_column(sa.Column("device_id", sa.Uuid()))
        batch.add_column(sa.Column("camera_ref", sa.String(64)))
        batch.add_column(sa.Column("line_id", sa.String(64)))
        batch.create_foreign_key(
            "fk_camera_events_device_id_edge_devices", "edge_devices", ["device_id"], ["id"], ondelete="SET NULL"
        )
        batch.create_index("ix_camera_events_device_id_occurred_at", ["device_id", "occurred_at"])

    if op.get_bind().dialect.name == "postgresql":
        for table in NEW_TABLES:
            op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')


def downgrade() -> None:
    with op.batch_alter_table("camera_events") as batch:
        batch.drop_index("ix_camera_events_device_id_occurred_at")
        batch.drop_constraint("fk_camera_events_device_id_edge_devices", type_="foreignkey")
        batch.drop_column("line_id")
        batch.drop_column("camera_ref")
        batch.drop_column("device_id")
    with op.batch_alter_table("cameras") as batch:
        batch.drop_index("ix_cameras_device_id")
        batch.drop_constraint("uq_cameras_device_id_external_id", type_="unique")
        batch.drop_constraint("fk_cameras_device_id_edge_devices", type_="foreignkey")
        batch.drop_column("last_error")
        batch.drop_column("ai_enabled")
        batch.drop_column("external_id")
        batch.drop_column("device_id")
    for table in reversed(NEW_TABLES):
        op.drop_table(table)
