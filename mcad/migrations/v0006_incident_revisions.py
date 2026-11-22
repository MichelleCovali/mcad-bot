"""Create history storage for reviewed incident updates."""
from sqlalchemy import (
    Column, DateTime, ForeignKey, Index,
    JSON, MetaData, String, Table, Text,
)


metadata = MetaData()

# References to existing tables; these are not created here.
Table(
    "incidents", metadata,
    Column("id", String(36), primary_key=True),
)
Table(
    "candidates", metadata,
    Column("id", String(36), primary_key=True),
)

revisions = Table(
    "incident_revisions",
    metadata,
    Column("id", String(36), primary_key=True),
    Column(
        "incident_id", String(36),
        ForeignKey("incidents.id"), nullable=False,
    ),
    Column(
        "candidate_id", String(36),
        ForeignKey("candidates.id"), nullable=False, unique=True,
    ),
    Column("reason", Text, nullable=False),
    Column("before_snapshot", JSON, nullable=False),
    Column("after_snapshot", JSON, nullable=False),
    Column("evidence", JSON, nullable=False),
    Column("created_at", DateTime, nullable=False),
)

Index(
    "ix_incident_revisions_incident_id",
    revisions.c.incident_id,
)


def upgrade(connection):
    revisions.create(connection, checkfirst=True)