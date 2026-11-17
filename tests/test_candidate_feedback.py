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
from mcad.service import upsert_incident


VERIFIED_CITATIONS = {"referenceUrls": ["https://authority.example/review", "https://press.example/review"],
                      "independentSourcesConfirmed": True}


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
        return self.client.post(f"/api/admin/candidates/{self.candidate_id}/approve", json=payload or {"title": "Human-reviewed fictional incident", **VERIFIED_CITATIONS}, headers=self.headers)

    def test_feedback_requires_authentication_and_valid_text(self):
        self.assertEqual(self.reject(headers={}).status_code, 401)
        self.assertEqual(self.client.get("/api/admin/candidates?status=rejected").status_code, 401)
        self.assertEqual(self.client.get("/api/admin/catalogs").status_code, 401)
        for payload in ([], "reason", {}, {"reason": []}, {"reason": "invalid"}, {"reason": "other", "notes": []}, {"reason": "other", "notes": "   "}, {"reason": "other", "notes": "x" * 2001}):
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
        response = self.approve({"title": "Human-reviewed title", **VERIFIED_CITATIONS, "rawFields": {"humanNote": "Confirmed manually", "_aiReview": {"evidence": "tampered"}}})
        self.assertEqual(response.status_code, 200)
        published = response.json()
        self.assertEqual(published["title"], "Human-reviewed title")
        self.assertEqual(published["method"], "")
        self.assertEqual(published["rawFields"]["humanNote"], "Confirmed manually")
        self.assertEqual(published["rawFields"]["_aiReview"]["evidence"], evidence)
        self.assertEqual(published["rawFields"]["_aiReview"]["verificationStatus"], "needs_review")

    def test_two_pages_syndicating_the_same_report_do_not_count_as_two_origins(self):
        a, b = "https://authority.example/report", "https://press.example/copy"
        with self.factory.begin() as session:
            candidate = session.get(Candidate, self.candidate_id)
            candidate.origin = "ai"
            candidate.source_url = a
            candidate.proposed_fields = {"referenceUrls": [a, b]}
            candidate.evidence = {"sources": [{"url": url, "openedByVerifier": True,
                "supports": ["Operator acknowledges incident"], "independenceGroup": "operator notice"} for url in (a, b)]}
        # Reformatting the copy's URL does not remove the known provenance.
        approved = self.approve({"title": "Reviewed title", "referenceUrls": ["http://www.press.example/copy?utm_campaign=tracking"],
            "independentSourcesConfirmed": True})
        self.assertEqual(approved.status_code, 422)
        self.assertIn("two independent sources", approved.json()["detail"])
        corrected = self.approve({"title": "Reviewed title", "referenceUrls": ["https://independent.example/report"],
            "independentSourcesConfirmed": True})
        self.assertEqual(corrected.status_code, 200)

    def test_update_needs_its_own_corroboration_and_extends_one_existing_incident(self):
        original = "https://background.example/original"
        a, b = "https://authority.example/update", "https://press.example/update"
        with self.factory.begin() as session:
            incident, _ = upsert_incident(session, {"title": "Original incident", "summary": "Original description", "sourceUrl": original,
                "latitude": 52, "longitude": 4, "rawFields": {"workbook": "indispensable"}}, notify=True)
            incident_id = incident.id
            candidate = session.get(Candidate, self.candidate_id)
            candidate.origin = "ai"
            candidate.source_url = a
            candidate.proposed_fields = {"referenceUrls": [original, a, b]}
            candidate.evidence = {"suggestedIncidentId": incident_id, "materialUpdate": {"reportedAt": "2025-07-02",
                "summary": "Port confirms operations resumed.", "supportingUrls": [a, b]},
                "sources": [{"url": url, "openedByVerifier": True, "supports": ["Confirmed fact"],
                             "independenceGroup": url} for url in (original, a, b)]}

        update = {"title": "Follow-up title", "summary": "Confirmed terminal restored", "independentSourcesConfirmed": True}
        missing_target = self.approve({**update, "referenceUrls": [a, b]})
        self.assertEqual(missing_target.status_code, 422)
        for references in ([original, a], [a, a + "?utm_medium=email"]):
            single = self.approve({**update, "updateIncidentId": incident_id, "referenceUrls": references})
            self.assertEqual(single.status_code, 422)
            self.assertIn("two independent sources", single.json()["detail"])

        request = {**update, "updateIncidentId": incident_id, "referenceUrls": [a, b]}
        first = self.approve(request)
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(self.approve(request).status_code, 200)
        published = self.client.get(f"/api/incidents/{incident_id}").json()
        self.assertEqual(published["id"], first.json()["id"])
        self.assertEqual(published["title"], "Original incident")
        self.assertEqual(published["summary"], "Original description")
        self.assertEqual((published["latitude"], published["longitude"]), (52, 4))
        self.assertEqual(published["rawFields"]["workbook"], "indispensable")
        self.assertEqual({source["url"] for source in published["sources"]}, {original, a, b})
        self.assertEqual(len(published["updates"]), 1)
        self.assertEqual(published["updates"][0]["summary"], "Confirmed terminal restored")
        self.assertEqual(published["updates"][0]["reportedAt"], "2025-07-02")
        self.assertEqual(self.count(Incident), 1)
        self.assertEqual(self.count(OutboxEvent), 1)

    def test_manual_publication_and_draft_transition_cannot_bypass_the_source_requirement(self):
        payload = {"title": "One cited incident", "sourceUrl": "https://one.example/one",
                   "referenceUrls": ["https://mirror.one.example/two"], "independentSourcesConfirmed": True}
        self.assertEqual(self.client.post("/api/incidents", json=payload, headers=self.headers).status_code, 422)
        draft = self.client.post("/api/incidents", json={**payload, "status": "pending"}, headers=self.headers)
        self.assertEqual(draft.status_code, 201)
        self.assertEqual(self.client.patch(f"/api/incidents/{draft.json()['id']}", json={"status": "published",
            "independentSourcesConfirmed": True}, headers=self.headers).status_code, 422)
        self.assertEqual(self.count(OutboxEvent), 0)

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
