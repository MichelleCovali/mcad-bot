"""AI discovery deduplication uses isolated records and never calls providers."""
from copy import deepcopy
import unittest
from unittest.mock import patch

from sqlalchemy import func, select

from mcad.ai_monitor import _persist, _url_identity
from mcad.db import get_session_factory, make_engine
from mcad.migrations import migrate
from mcad.models import AIRun, Candidate, Incident, IncidentSource, OutboxEvent


class ResearchDeduplication(unittest.TestCase):
    def setUp(self):
        self.engine = make_engine("sqlite+pysqlite:///:memory:")
        migrate(self.engine)
        self.factory = get_session_factory(self.engine)
        self.http = patch("requests.sessions.Session.request", side_effect=AssertionError("External HTTP is forbidden"))
        self.request = self.http.start()

    def tearDown(self):
        self.request.assert_not_called()
        self.http.stop()
        self.engine.dispose()

    def proposal(self, title="A different headline for the same incident", year=2026, urls=None):
        urls = urls or ["https://authority.example.org/new-report", "https://newsroom.example.net/new-report"]
        return {"title": title, "source_url": urls[0], "summary": "Synthetic research example.",
            "origin": "ai", "status": "pending", "verification_status": "supported",
            "proposed_fields": {"title": title, "year": year, "sourceUrl": urls[0], "referenceUrls": urls},
            "evidence": {"sources": [{"url": url} for url in urls]}, "matched_keywords": []}

    def persist(self, proposal):
        with self.factory.begin() as session:
            run = AIRun()
            session.add(run)
            session.flush()
            run_id = run.id
        return _persist(self.factory, run_id, [deepcopy(proposal)])

    def test_two_existing_sources_with_changed_tracking_do_not_create_a_new_candidate(self):
        with self.factory.begin() as session:
            incident = Incident(title="Existing incident with an earlier headline", year=2026,
                source_url="https://authority.example.org/report?id=42&utm_source=old")
            incident.sources.append(IncidentSource(url="https://newsroom.example.net/report?article=7&fbclid=old"))
            session.add(incident)
        result = self.persist(self.proposal(urls=[
            "https://authority.example.org/report?utm_campaign=new&id=42&utm_source=telegram",
            "https://newsroom.example.net/report?gclid=new&article=7",
        ]))
        self.assertEqual(result["candidatesCreated"], 0)
        with self.factory() as session:
            self.assertEqual(session.scalar(select(func.count()).select_from(Candidate)), 0)
            self.assertEqual(session.scalar(select(func.count()).select_from(OutboxEvent)), 0)

    def test_identical_title_with_unknown_year_is_held_back_in_either_direction(self):
        for known_year, proposal_year in ((2026, None), (None, 2026)):
            with self.subTest(known_year=known_year, proposal_year=proposal_year):
                title = f"Fictional Port {known_year} {proposal_year} outage"
                with self.factory.begin() as session:
                    session.add(Incident(title=title, year=known_year))
                result = self.persist(self.proposal(title=f"  {title.upper()}  ", year=proposal_year))
                self.assertEqual(result["candidatesCreated"], 0)

    def test_real_query_identifiers_and_original_urls_are_preserved(self):
        with self.factory.begin() as session:
            session.add(Incident(title="Different event", year=2026,
                source_url="https://authority.example.org/report?id=41"))
        url = "https://authority.example.org/report?id=42&flag&source=wire&utm_source=telegram"
        self.assertEqual(_url_identity(url), "https://authority.example.org/report?id=42&flag&source=wire")
        self.assertEqual(_url_identity("https://authority.example.org/report?id=a%2Fb&token=x+y&utm_medium=mail"),
            "https://authority.example.org/report?id=a%2Fb&token=x+y")
        result = self.persist(self.proposal(urls=[url, "https://newsroom.example.net/report?id=42&fbclid=new"]))
        self.assertEqual(result["candidatesCreated"], 1)
        with self.factory() as session:
            candidate = session.scalar(select(Candidate))
            self.assertEqual(candidate.source_url, url)
            self.assertEqual(candidate.proposed_fields["referenceUrls"][0], url)

    def test_same_title_with_distinct_known_years_remains_a_possible_recurrence(self):
        with self.factory.begin() as session:
            session.add(Incident(title="Fictional repeated port outage", year=2025))
        result = self.persist(self.proposal(title="Fictional repeated port outage", year=2026))
        self.assertEqual(result["candidatesCreated"], 1)

    def test_known_source_with_rewritten_headline_and_new_corroboration_requires_identity_review(self):
        known = "https://authority.example.org/report?id=42"
        with self.factory.begin() as session:
            session.add(Incident(title="Existing original incident headline", year=2026, source_url=known))
        result = self.persist(self.proposal(title="Rewritten headline returned by research", urls=[
            known + "&utm_source=newsletter", "https://newsroom.example.net/new-corroboration",
        ]))
        self.assertEqual(result["candidatesCreated"], 1)
        with self.factory() as session:
            candidate = session.scalar(select(Candidate))
            self.assertEqual(candidate.verification_status, "needs_review")
            self.assertEqual(candidate.evidence["assessment"], "needs_review")
            self.assertEqual(candidate.status, "pending")
            self.assertTrue(any("distinct event" in item for item in candidate.evidence["uncertainties"]))
            self.assertEqual(candidate.source_url, "https://newsroom.example.net/new-corroboration")


if __name__ == "__main__":
    unittest.main()
