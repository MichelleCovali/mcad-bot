"""Versioned schema migrations; never change an already deployed migration."""

from sqlalchemy import select, text

from mcad.models import SchemaVersion, utcnow
from .v0001_initial import upgrade as initial_schema
from .v0002_candidate_feedback import upgrade as candidate_feedback
from .v0003_ai_monitor import upgrade as ai_monitor
from .v0004_candidate_alerts import upgrade as candidate_alerts

MIGRATIONS = [
    (1, initial_schema),
    (2, candidate_feedback),
    (3, ai_monitor),
    (4, candidate_alerts),
]

def migrate(engine):
    with engine.begin() as connection:
        if engine.dialect.name == "postgresql":
            connection.execute(text("SELECT pg_advisory_xact_lock(681223941)"))
        SchemaVersion.__table__.create(connection, checkfirst=True)
        applied = set(connection.execute(select(SchemaVersion.version)).scalars())
        for version, upgrade in MIGRATIONS:
            if version not in applied:
                upgrade(connection)
                connection.execute(SchemaVersion.__table__.insert().values(version=version, applied_at=utcnow()))
