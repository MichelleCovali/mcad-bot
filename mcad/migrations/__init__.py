"""Versioned schema migrations; never change an already deployed migration."""
from sqlalchemy import select, text

from mcad.models import SchemaVersion, utcnow
from .v0001_initial import upgrade as initial_schema

MIGRATIONS = [(1, initial_schema)]


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
