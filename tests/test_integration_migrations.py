"""Exercise both pre-integration version ledgers against the combined schema."""
from datetime import datetime

import pytest
from sqlalchemy import create_engine, inspect, select, text

from mcad.migrations import MIGRATIONS, migrate
from mcad.migrations.v0005_candidate_relationships import upgrade as legacy_relationships
from mcad.migrations.v0006_incident_revisions import upgrade as legacy_revisions
from mcad.models import Incident, SchemaVersion


def check_history_upgrade(engine, history):
    now = datetime(2025, 1, 1)
    with engine.begin() as connection:
        SchemaVersion.__table__.create(connection)
        steps = list(MIGRATIONS[:4])
        if history == "feature":
            steps += [(5, legacy_relationships), (6, legacy_revisions)]
        else:
            steps += MIGRATIONS[4:8]
        for version, upgrade in steps:
            upgrade(connection)
            connection.execute(SchemaVersion.__table__.insert().values(version=version, applied_at=now))

        connection.execute(Incident.__table__.insert().values(
            id="old-incident", title="Existing incident", status="published", created_at=now, updated_at=now,
        ))
        connection.execute(text("INSERT INTO candidates (id, source_url, title, summary, matched_keywords, status, baseline, first_seen_at) VALUES "
                                "('old-report', 'https://example.invalid/review', 'Existing report', '', '[]', 'pending', false, :now)"), {"now": now})
        if history == "feature":
            connection.execute(text("UPDATE candidates SET relationship_type='possible_update', related_incident_id='old-incident' "
                                    "WHERE id='old-report'"))
            connection.execute(text("INSERT INTO incident_revisions (id, incident_id, candidate_id, reason, before_snapshot, after_snapshot, evidence, created_at) "
                                    "VALUES ('old-revision', 'old-incident', 'old-report', 'Original review reason', '{}', '{}', '{}', :now)"), {"now": now})
        else:
            connection.execute(text("INSERT INTO admin_users (id, email, password_hash, role, active, created_at) "
                                    "VALUES ('old-account', 'reviewer@example.invalid', 'fixture-only', 'reviewer', true, :now)"), {"now": now})
            connection.execute(text("INSERT INTO learning_prompt_versions "
                                    "(id, summary, discovery_instructions, verification_instructions, created_by_ai, created_at) "
                                    "VALUES ('old-prompt', 'Original learning', '', '', false, :now)"), {"now": now})

    migrate(engine)
    migrate(engine)

    with engine.connect() as connection:
        assert list(connection.scalars(select(SchemaVersion.version).order_by(SchemaVersion.version))) == list(range(1, 11))
        assert {"admin_users", "admin_sessions", "telegram_feedback", "learning_prompt_versions", "incident_revisions"} <= set(inspect(connection).get_table_names())
        assert connection.scalar(text("SELECT title FROM incidents WHERE id='old-incident'")) == "Existing incident"
        relationship = connection.scalar(text("SELECT relationship_type FROM candidates WHERE id='old-report'"))
        if history == "feature":
            assert relationship == "possible_update"
            assert connection.scalar(text("SELECT reason FROM incident_revisions WHERE id='old-revision'")) == "Original review reason"
        else:
            assert relationship == "unclassified"
            assert connection.scalar(text("SELECT email FROM admin_users WHERE id='old-account'")) == "reviewer@example.invalid"
            assert connection.scalar(text("SELECT summary FROM learning_prompt_versions WHERE id='old-prompt'")) == "Original learning"


@pytest.mark.parametrize("history", ["main", "feature"])
def test_upgrade_from_existing_branch_history(history):
    engine = create_engine("sqlite:///:memory:")
    try:
        check_history_upgrade(engine, history)
    finally:
        engine.dispose()
