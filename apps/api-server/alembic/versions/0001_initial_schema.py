"""initial schema: users, roles, cameras, camera_groups, camera_events, system_logs, audit_logs

Revision ID: 0001
Revises:
Create Date: 2026-11-16
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSONType = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")
BigIntPK = sa.BigInteger().with_variant(sa.Integer(), "sqlite")
TABLES = ["roles", "users", "camera_groups", "cameras", "camera_events", "system_logs", "audit_logs"]

BUILTIN_ROLES = [
    ("administrator", "Full system access", ["*"]),
    (
        "operator",
        "Operate cameras and handle events",
        ["cameras:read", "cameras:control", "events:read", "events:ack", "devices:read"],
    ),
    ("viewer", "Read-only access", ["cameras:read", "events:read", "devices:read"]),
]


def _ts(name: str, nullable: bool = False) -> sa.Column:
    return sa.Column(
        name, sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=nullable
    )


def upgrade() -> None:
    op.create_table(
        "roles",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(50), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("permissions", JSONType, nullable=False),
        sa.Column("is_system", sa.Boolean(), nullable=False),
        _ts("created_at"),
        sa.PrimaryKeyConstraint("id", name="pk_roles"),
        sa.UniqueConstraint("name", name="uq_roles_name"),
    )

    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("full_name", sa.String(255)),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("role_id", sa.Uuid(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True)),
        _ts("created_at"),
        _ts("updated_at"),
        sa.PrimaryKeyConstraint("id", name="pk_users"),
        sa.UniqueConstraint("email", name="uq_users_email"),
        sa.ForeignKeyConstraint(
            ["role_id"], ["roles.id"], name="fk_users_role_id_roles", ondelete="RESTRICT"
        ),
    )
    op.create_index("ix_users_role_id", "users", ["role_id"])

    op.create_table(
        "camera_groups",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("description", sa.Text()),
        _ts("created_at"),
        _ts("updated_at"),
        sa.PrimaryKeyConstraint("id", name="pk_camera_groups"),
        sa.UniqueConstraint("name", name="uq_camera_groups_name"),
    )

    op.create_table(
        "cameras",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("location", sa.String(255)),
        sa.Column("source_type", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("is_enabled", sa.Boolean(), nullable=False),
        sa.Column("group_id", sa.Uuid()),
        sa.Column("resolution_width", sa.Integer()),
        sa.Column("resolution_height", sa.Integer()),
        sa.Column("fps", sa.Float()),
        sa.Column("last_connected_at", sa.DateTime(timezone=True)),
        sa.Column("last_frame_at", sa.DateTime(timezone=True)),
        _ts("created_at"),
        _ts("updated_at"),
        sa.PrimaryKeyConstraint("id", name="pk_cameras"),
        sa.ForeignKeyConstraint(
            ["group_id"],
            ["camera_groups.id"],
            name="fk_cameras_group_id_camera_groups",
            ondelete="SET NULL",
        ),
        sa.CheckConstraint(
            "source_type IN ('rtsp', 'onvif', 'usb', 'file', 'ffmpeg')",
            name="ck_cameras_source_type_valid",
        ),
        sa.CheckConstraint(
            "status IN ('online', 'offline', 'connecting', 'reconnecting', 'error', 'disabled')",
            name="ck_cameras_status_valid",
        ),
        sa.CheckConstraint("fps IS NULL OR fps >= 0", name="ck_cameras_fps_non_negative"),
        sa.CheckConstraint(
            "(resolution_width IS NULL OR resolution_width > 0) AND "
            "(resolution_height IS NULL OR resolution_height > 0)",
            name="ck_cameras_resolution_positive",
        ),
    )
    op.create_index("ix_cameras_status", "cameras", ["status"])
    op.create_index("ix_cameras_group_id", "cameras", ["group_id"])

    op.create_table(
        "camera_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("camera_id", sa.Uuid()),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        _ts("received_at"),
        sa.Column("zone_id", sa.String(64)),
        sa.Column("track_id", sa.String(64)),
        sa.Column("metadata", JSONType, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_camera_events"),
        sa.ForeignKeyConstraint(
            ["camera_id"], ["cameras.id"], name="fk_camera_events_camera_id_cameras", ondelete="SET NULL"
        ),
        sa.CheckConstraint(
            "source IN ('standard', 'ai', 'system')", name="ck_camera_events_source_valid"
        ),
    )
    op.create_index("ix_camera_events_occurred_at", "camera_events", ["occurred_at"])
    op.create_index(
        "ix_camera_events_camera_id_occurred_at", "camera_events", ["camera_id", "occurred_at"]
    )
    op.create_index(
        "ix_camera_events_event_type_occurred_at", "camera_events", ["event_type", "occurred_at"]
    )

    op.create_table(
        "system_logs",
        sa.Column("id", BigIntPK, autoincrement=True, nullable=False),
        sa.Column("level", sa.String(10), nullable=False),
        sa.Column("component", sa.String(64), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("context", JSONType, nullable=False),
        _ts("created_at"),
        sa.PrimaryKeyConstraint("id", name="pk_system_logs"),
        sa.CheckConstraint(
            "level IN ('DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL')",
            name="ck_system_logs_level_valid",
        ),
    )
    op.create_index("ix_system_logs_level", "system_logs", ["level"])
    op.create_index("ix_system_logs_component", "system_logs", ["component"])
    op.create_index("ix_system_logs_created_at", "system_logs", ["created_at"])

    op.create_table(
        "audit_logs",
        sa.Column("id", BigIntPK, autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Uuid()),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("entity_type", sa.String(64)),
        sa.Column("entity_id", sa.String(64)),
        sa.Column("details", JSONType, nullable=False),
        sa.Column("ip_address", sa.String(45)),
        _ts("created_at"),
        sa.PrimaryKeyConstraint("id", name="pk_audit_logs"),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_audit_logs_user_id_users", ondelete="SET NULL"
        ),
    )
    op.create_index("ix_audit_logs_user_id", "audit_logs", ["user_id"])
    op.create_index("ix_audit_logs_action", "audit_logs", ["action"])
    op.create_index("ix_audit_logs_created_at", "audit_logs", ["created_at"])

    # Seed through a text-typed view of the table so the same code works online and in
    # offline (--sql) mode; PostgreSQL casts the JSON text to JSONB on insert.
    roles_seed = sa.table(
        "roles",
        sa.column("id", sa.Uuid()),
        sa.column("name", sa.String()),
        sa.column("description", sa.Text()),
        sa.column("permissions", sa.Text()),
        sa.column("is_system", sa.Boolean()),
    )
    op.bulk_insert(
        roles_seed,
        [
            {
                "id": uuid.uuid5(uuid.NAMESPACE_URL, f"aci:role:{name}"),
                "name": name,
                "description": desc,
                "permissions": json.dumps(perms),
                "is_system": True,
            }
            for name, desc, perms in BUILTIN_ROLES
        ],
    )

    if op.get_bind().dialect.name == "postgresql":
        # Supabase exposes the `public` schema through its REST API (anon/authenticated roles).
        # Enabling RLS with no policies blocks that path; the API server connects as the
        # table owner and is unaffected.
        for table in TABLES:
            op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')


def downgrade() -> None:
    for table in reversed(TABLES):
        op.drop_table(table)
