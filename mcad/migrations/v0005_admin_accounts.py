"""Frozen additive account and session schema, shared by SQLite and PostgreSQL."""
from sqlalchemy import Boolean, CheckConstraint, Column, DateTime, ForeignKey, Integer, MetaData, String, Table


metadata = MetaData()
users = Table("admin_users", metadata,
    Column("id", String(36), primary_key=True),
    Column("email", String(254), unique=True, nullable=False),
    Column("password_hash", String(160), nullable=False),
    Column("role", String(16), nullable=False),
    Column("active", Boolean, nullable=False),
    Column("created_at", DateTime, nullable=False),
    CheckConstraint("role IN ('admin', 'reviewer')", name="admin_user_role"),
)
sessions = Table("admin_sessions", metadata,
    Column("token_hash", String(64), primary_key=True),
    Column("user_id", String(36), ForeignKey("admin_users.id", ondelete="CASCADE"), nullable=False, index=True),
    Column("expires_at", DateTime, nullable=False, index=True),
)
invites = Table("admin_invites", metadata,
    Column("id", String(36), primary_key=True),
    Column("email", String(254), unique=True, nullable=False),
    Column("token_hash", String(64), unique=True, nullable=False),
    Column("role", String(16), nullable=False),
    Column("created_at", DateTime, nullable=False),
    Column("expires_at", DateTime, nullable=False),
    Column("accepted_at", DateTime),
    Column("revoked_at", DateTime),
    CheckConstraint("role IN ('admin', 'reviewer')", name="admin_invite_role"),
)
attempts = Table("admin_auth_attempts", metadata,
    Column("key", String(64), primary_key=True),
    Column("attempts", Integer, nullable=False),
    Column("expires_at", DateTime, nullable=False, index=True),
)


def upgrade(connection):
    metadata.create_all(connection, checkfirst=True)
