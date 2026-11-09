"""Review transitions and provenance; no external requests or configured database."""
import importlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import func, select, text

from mcad.api import create_app
from mcad.config import Settings
from mcad.db import get_session_factory, make_engine
from mcad.migrations import migrate
from mcad.models import AIConfig, Candidate, Delivery, ImportBatch, Incident, OutboxEvent, SchemaVersion, utcnow


class CandidateFeedback(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.engine = make_engine(f"sqlite:///{Path(self.temp.name) / 'review.sqlite3'}")
        migrate(self.engine)
        self.factory = get_session_factory(self.engine)
        self.network = patch("requests.sessions.Session.request", side_effect=AssertionError("External HTTP is forbidden in review tests"))
        self.network.start()
        self.addCleanup(self.network.stop)
        self.client = TestClient(create_app(Settings(admin_api_token="test-review-token", enable_workers=False), self.engine))
        self.headers = {"Authorization": "Bearer test-review-token"}
        with self.factory.begin() as session:
            candidate = Candidate(title="Fictional port report", summary="Synthetic pending report", source_url="https://example.invalid/review-1")
            session.add(candidate)
            session.flush()
            self.candidate_id = candidate.id

    def tearDown(self):
        self.client.close()
        self.engine.dispose()
        self.temp.cleanup()

    def count(self, model):
        with self.factory() as session:
            return session.scalar(select(func.count()).select_from(model))

    def reject(self, payload=None, **kwargs):
        return self.client.post(f"/api/admin/candidates/{self.candidate_id}/reject", json=payload if payload is not None else {"reason": "insufficient_evidence", "notes": "  Source does not confirm an attack.  "}, headers=kwargs.get("headers", self.headers))

    def approve(self, payload=None):
        return self.client.post(f"/api/admin/candidates/{self.candidate_id}/approve", json=payload or {"title": "Human-reviewed fictional incident"}, headers=self.headers)

    def test_feedback_requires_authentication_and_valid_text(self):
        self.assertEqual(self.reject(headers={}).status_code, 401)
        self.assertEqual(self.client.get("/api/admin/candidates?status=rejected").status_code, 401)
        self.assertEqual(self.client.get("/api/admin/catalogs").status_code, 401)
        for payload in ([], "reason", {}, {"reason": []}, {"reason": "invalid"}, {"reason": "other", "notes": []}, {"reason": "other", "notes": "x" * 2001}):
            with self.subTest(payload_type=type(payload).__name__):
                self.assertEqual(self.reject(payload).status_code, 422)
        with self.factory() as session:
            self.assertEqual(session.get(Candidate, self.candidate_id).status, "pending")

    def test_rejection_persists_feedback_and_is_visible_in_history_without_alerts(self):
        response = self.reject()
        self.assertEqual(response.status_code, 200)
        pending = self.client.get("/api/admin/candidates", headers=self.headers).json()["items"]
        rejected = self.client.get("/api/admin/candidates?status=rejected", headers=self.headers).json()["items"]
        self.assertEqual(pending, [])
        self.assertEqual(len(rejected), 1)
        self.assertEqual(rejected[0]["rejectionReason"], "insufficient_evidence")
        self.assertEqual(rejected[0]["rejectionNotes"], "Source does not confirm an attack.")
        self.assertEqual(rejected[0]["origin"], "rss")
        self.assertEqual(self.client.get("/api/admin/candidates?status=unknown", headers=self.headers).status_code, 422)
        self.assertEqual(self.count(Incident), 0)
        self.assertEqual(self.count(OutboxEvent), 0)
        self.assertEqual(self.count(Delivery), 0)

    def test_rejected_candidate_cannot_be_published_or_rejected_again(self):
        self.assertEqual(self.reject().status_code, 200)
        self.assertEqual(self.approve().status_code, 409)
        self.assertEqual(self.reject({"reason": "other", "notes": "Do not overwrite"}).status_code, 409)
        self.assertEqual(self.count(Incident), 0)
        self.assertEqual(self.count(OutboxEvent), 0)
        with self.factory() as session:
            candidate = session.get(Candidate, self.candidate_id)
            self.assertEqual(candidate.status, "rejected")
            self.assertEqual(candidate.rejection_reason, "insufficient_evidence")

    def test_approved_candidate_is_idempotent_and_cannot_be_rejected(self):
        first = self.approve()
        second = self.approve({"title": "Attempted overwrite"})
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first.json()["id"], second.json()["id"])
        self.assertEqual(second.json()["title"], "Human-reviewed fictional incident")
        self.assertEqual(self.reject().status_code, 409)
        self.assertEqual(self.count(Incident), 1)
        self.assertEqual(self.count(OutboxEvent), 1)

    def test_failed_approval_rolls_back_the_review_transition(self):
        self.assertEqual(self.approve({"title": "Human reviewed", "latitude": 200, "longitude": 2}).status_code, 422)
        with self.factory() as session:
            self.assertEqual(session.get(Candidate, self.candidate_id).status, "pending")
        self.assertEqual(self.count(Incident), 0)
        self.assertEqual(self.count(OutboxEvent), 0)

    def test_ai_approval_keeps_server_evidence_without_applying_unreviewed_fields(self):
        evidence = {"assessment": "needs_review", "sources": [{"url": "https://example.invalid/original", "snippet": "Synthetic evidence."}], "uncertainties": ["Event date is unknown."]}
        with self.factory.begin() as session:
            candidate = session.get(Candidate, self.candidate_id)
            candidate.origin = "ai"
            candidate.proposed_fields = {"title": "Unreviewed proposed title", "method": "Unreviewed proposed method"}
            candidate.evidence = evidence
            candidate.verification_status = "needs_review"
        response = self.approve({"title": "Human-reviewed title", "rawFields": {"humanNote": "Confirmed manually", "_aiReview": {"evidence": "tampered"}}})
        self.assertEqual(response.status_code, 200)
        published = response.json()
        self.assertEqual(published["title"], "Human-reviewed title")
        self.assertEqual(published["method"], "")
        self.assertEqual(published["rawFields"]["humanNote"], "Confirmed manually")
        self.assertEqual(published["rawFields"]["_aiReview"]["evidence"], evidence)
        self.assertEqual(published["rawFields"]["_aiReview"]["verificationStatus"], "needs_review")

    def test_catalogs_follow_latest_workbook_without_inventing_values(self):
        empty = self.client.get("/api/admin/catalogs", headers=self.headers).json()
        self.assertEqual(len(empty["fields"]), 33)
        self.assertEqual(empty["catalogs"]["countries"], [])
        with self.factory.begin() as session:
            session.add(ImportBatch(source_name="fictional.xlsx", source_sha256="f" * 64, row_count=0, metadata_json={"catalogs": {"countries": [{"id": "NL", "value": "Netherlands"}], "methods": [{"value": "Fixture method"}]}}))
        populated = self.client.get("/api/admin/catalogs", headers=self.headers).json()
        self.assertEqual(populated["sourceName"], "fictional.xlsx")
        self.assertEqual(populated["catalogs"]["methods"], [{"value": "Fixture method"}])


class PreviewFixture(unittest.TestCase):
    def test_import_has_no_database_side_effects_and_existing_files_are_refused(self):
        with patch("mcad.db.get_session_factory", side_effect=AssertionError("Do not connect on import")):
            import create_test_candidate
            importlib.reload(create_test_candidate)
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "existing.sqlite3"
            target.write_bytes(b"must remain unchanged")
            with self.assertRaises(SystemExit), patch("sys.stderr"):
                create_test_candidate.main(["--database", str(target)])
            self.assertEqual(target.read_bytes(), b"must remain unchanged")
        with self.assertRaises(SystemExit), patch("sys.stderr"):
            create_test_candidate.main(["--database", "postgresql://example.invalid/fixture.db"])


class UpgradeExistingReviewDatabase(unittest.TestCase):
    def test_v1_candidate_survives_upgrade_and_repeated_migration(self):
        from mcad.migrations.v0001_initial import upgrade
        engine = make_engine("sqlite+pysqlite:///:memory:")
        try:
            with engine.begin() as connection:
                SchemaVersion.__table__.create(connection)
                upgrade(connection)
                connection.execute(SchemaVersion.__table__.insert().values(version=1, applied_at=utcnow()))
                connection.execute(text("INSERT INTO candidates (id, source_url, title, summary, matched_keywords, status, baseline, first_seen_at) VALUES ('existing-fixture', 'https://example.invalid/existing', 'Existing fictional report', 'Preserve this summary', '[]', 'pending', 0, :now)"), {"now": utcnow().isoformat(sep=" ")})
            migrate(engine)
            migrate(engine)
            with get_session_factory(engine)() as session:
                candidate = session.get(Candidate, "existing-fixture")
                self.assertEqual(candidate.summary, "Preserve this summary")
                self.assertEqual(candidate.status, "pending")
                self.assertIsNone(candidate.rejection_reason)
                self.assertEqual(candidate.origin, "rss")
                self.assertEqual(candidate.evidence, {})
                self.assertFalse(session.get(AIConfig, 1).enabled)
                self.assertEqual(session.scalar(select(func.count()).select_from(Candidate)), 1)
        finally:
            engine.dispose()


if __name__ == "__main__":
    unittest.main()
