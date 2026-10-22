import os
from hashlib import sha256
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from mcad.import_workbook import (
    ParsedRecord,
    ParsedWorkbook,
    SOURCE_FIELDS,
    external_identity,
    import_workbook,
    parse_coordinates,
    parse_date_fields,
    parse_workbook,
    resolve_countries,
)


WORKBOOK = os.environ.get("MCAD_TEST_WORKBOOK")


class ParsingBoundaries(unittest.TestCase):
    def test_explicit_coordinates_only_and_no_swapping_or_centroid_fallback(self):
        self.assertEqual(parse_coordinates(" 42.70278000     9.45000000 "), {
            "status": "valid", "latitude": 42.70278, "longitude": 9.45, "separator": "whitespace",
        })
        self.assertEqual(parse_coordinates("-90,180")["latitude"], -90)
        for value in [None, "", "   "]:
            self.assertEqual(parse_coordinates(value)["status"], "missing")
        for value in ["91, 20", "40, 181", "1,2,3", "52°N 4°E", "NaN,4", "Houston, USA"]:
            result = parse_coordinates(value)
            self.assertEqual(result["status"], "invalid")
            self.assertIsNone(result["latitude"])
            self.assertIsNone(result["longitude"])

    def test_dates_preserve_unknown_month_and_range_without_inventing_day(self):
        unknown_month = parse_date_fields(2023)
        self.assertEqual(unknown_month["date_text"], "2023")
        self.assertEqual(unknown_month["date_precision"], "year")
        self.assertIsNone(unknown_month["date_start"])
        ranged = parse_date_fields(2014, 6, 2015)
        self.assertEqual(ranged["date_text"], "2014-06–2015")
        self.assertEqual(ranged["date_precision"], "range")
        self.assertEqual(ranged["year_end"], 2015)
        invalid = parse_date_fields(2024, 13, 2020)
        self.assertEqual(invalid["month"], "")
        self.assertIsNone(invalid["year_end"])
        self.assertCountEqual(invalid["issues"], ["invalid_month", "invalid_year_end"])

    def test_country_aliases_resolve_roles_without_inventing_region_country(self):
        catalog = [{"id": "US", "value": "United States"}, {"id": "NL", "value": "Netherlands"}]
        countries = resolve_countries("USA, The Netherlands, Asia Region, Uknown", catalog)
        self.assertEqual([item["id"] for item in countries], ["US", "NL", None, None])
        self.assertEqual(countries[0]["source"], "USA")
        self.assertEqual(countries[2]["status"], "unresolved")
        self.assertEqual(countries[3]["status"], "unknown")

    def test_identity_retains_duplicate_reference_and_survives_whitespace_or_row_reordering(self):
        first = external_identity(20220501, " Development fixture A ")
        second = external_identity(20220501, "Development fixture B")
        self.assertNotEqual(first, second)
        self.assertEqual(first, external_identity("20220501", "development   fixture a"))


class ImportTransactions(unittest.TestCase):
    def setUp(self):
        from mcad.db import get_session_factory, make_engine
        from mcad.models import Base

        self.engine = make_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.factory = get_session_factory(self.engine)

    def tearDown(self):
        self.engine.dispose()

    def test_failed_row_rolls_back_batch_and_prior_rows_and_emits_no_events(self):
        from sqlalchemy import func, select
        from mcad.models import ImportBatch, Incident, OutboxEvent
        from mcad.service import upsert_incident

        records = [ParsedRecord(index + 2, f"test-only:{index}", {
            "title": f"Synthetic transaction fixture {index}", "reference_number": str(index),
            "latitude": None, "longitude": None, "raw_fields": {},
        }, []) for index in range(2)]
        parsed = ParsedWorkbook("test-only.xlsx", "a" * 64, 2, records, [], {})
        calls = 0

        def fail_second(*args, **kwargs):
            nonlocal calls
            calls += 1
            self.assertFalse(kwargs["notify"])
            if calls == 2:
                raise ValueError("Synthetic import failure")
            return upsert_incident(*args, **kwargs)

        with patch("mcad.import_workbook.parse_workbook", return_value=parsed), patch("mcad.service.upsert_incident", side_effect=fail_second):
            with self.assertRaisesRegex(ValueError, "Synthetic import failure"):
                import_workbook("unused-test-path", self.factory)
        with self.factory() as session:
            for model in [ImportBatch, Incident, OutboxEvent]:
                self.assertEqual(session.scalar(select(func.count()).select_from(model)), 0)

    def test_quarantined_source_row_is_accounted_for_and_retained(self):
        from sqlalchemy import select
        from mcad.models import ImportBatch, ImportRow

        quarantine = [{"source_row": 2, "reason": "Missing Title", "raw_fields": {"Title": None, "Other source value": "retained"}}]
        parsed = ParsedWorkbook("test-only.xlsx", "b" * 64, 1, [], quarantine, {"quarantined_rows": quarantine})
        with patch("mcad.import_workbook.parse_workbook", return_value=parsed):
            report = import_workbook("unused-test-path", self.factory)
        self.assertEqual(report["accounted_rows"], 1)
        self.assertEqual(report["quarantined_rows"], 1)
        with self.factory() as session:
            row = session.scalar(select(ImportRow))
            self.assertIsNone(row.incident_id)
            self.assertEqual(row.raw_fields["Other source value"], "retained")
            self.assertEqual(session.scalar(select(ImportBatch)).row_count, 1)

    def test_revised_workbook_updates_existing_identity_and_retains_source_relations(self):
        from sqlalchemy import func, select
        from mcad.models import ImportBatch, ImportRow, Incident, IncidentSource, OutboxEvent

        def parsed(version):
            payload = {
                "title": "Synthetic revision fixture", "reference_number": "test-revision",
                "description": f"Revision {version}", "latitude": None, "longitude": None,
                "source_url": "https://example.invalid/source", "reference_urls": ["https://example.invalid/source"],
                "sources": [{"url": "https://example.invalid/source", "short_name": f"Citation {version}"}],
                "raw_fields": {"Revision": version},
            }
            return ParsedWorkbook("test-only.xlsx", str(version) * 64, 1, [ParsedRecord(2, "test-revision:stable", payload, [])], [], {})

        with patch("mcad.import_workbook.parse_workbook", side_effect=[parsed(1), parsed(2)]):
            first = import_workbook("unused-test-path", self.factory)
            second = import_workbook("unused-test-path", self.factory)
        self.assertEqual(first["created_records"], 1)
        self.assertEqual(second["created_records"], 0)
        self.assertEqual(second["existing_records"], 1)
        with self.factory() as session:
            for model, count in [(Incident, 1), (IncidentSource, 1), (ImportBatch, 2), (ImportRow, 2), (OutboxEvent, 0)]:
                self.assertEqual(session.scalar(select(func.count()).select_from(model)), count)
            incident = session.scalar(select(Incident))
            self.assertEqual(incident.description, "Revision 2")
            self.assertEqual(incident.sources[0].short_name, "Citation 2")


@unittest.skipUnless(WORKBOOK, "Set MCAD_TEST_WORKBOOK to the private source for the opt-in real-workbook checks")
class ActualWorkbookIntegrity(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.path = Path(WORKBOOK)
        cls.original_sha = sha256(cls.path.read_bytes()).hexdigest()
        cls.parsed = parse_workbook(cls.path)

    def test_accounts_for_all_440_rows_including_rows_outside_source_auto_filter(self):
        report = self.parsed.report()
        self.assertEqual(report["source_rows"], 440)
        self.assertEqual(report["accepted_rows"], 440)
        self.assertEqual(report["accounted_rows"], 440)
        self.assertEqual(report["quarantined_rows"], 0)
        self.assertEqual(max(record.source_row for record in self.parsed.records), 441)
        self.assertEqual(len({record.external_id for record in self.parsed.records}), 440)
        self.assertEqual(len(self.parsed.metadata_json["duplicate_references"]), 9)

    def test_preserves_all_fields_formula_links_and_catalog_provenance(self):
        first = self.parsed.records[0].payload
        raw = first["raw_fields"]
        self.assertEqual(len(SOURCE_FIELDS), 33)
        self.assertIn("Incident Country ", raw)
        self.assertIn("      Reference URL", raw)
        self.assertEqual(raw["Month"], 9)
        self.assertIsInstance(raw["Reference number"], int)
        self.assertTrue(raw["_cells"]["K2"]["formula"].startswith("=HYPERLINK("))
        self.assertIn("cached_value", raw["_cells"]["K2"])
        self.assertEqual(len(self.parsed.metadata_json["catalogs"]["countries"]), 249)
        self.assertEqual(len(self.parsed.metadata_json["lists_rows"]), 251)
        self.assertEqual(len(self.parsed.metadata_json["catalogs"]["methods"]), 13)
        self.assertEqual(len(self.parsed.metadata_json["native_metadata"]["Database"]["hyperlinks"]), 228)
        self.assertEqual(len(self.parsed.metadata_json["native_metadata"]["lists"]["tables"]), 4)
        self.assertEqual(sha256(self.path.read_bytes()).hexdigest(), self.original_sha)

    def test_uses_426_source_coordinates_and_preserves_14_unmapped_records(self):
        report = self.parsed.report()
        self.assertEqual(report["mapped_rows"], 426)
        self.assertEqual(report["unmapped_rows"], 14)
        bastia = next(record.payload for record in self.parsed.records if record.source_row == 287)
        self.assertEqual((bastia["latitude"], bastia["longitude"]), (42.70278, 9.45))
        self.assertEqual(bastia["location_precision"], "source_provided")
        self.assertTrue(all(record.payload["location_precision"] == "unknown" for record in self.parsed.records if record.payload["latitude"] is None))
        disputed = next(record for record in self.parsed.records if record.source_row == 63)
        self.assertEqual(disputed.payload["year"], 2016)
        self.assertIn("reference_year_mismatch", disputed.issues)
        self.assertTrue(all(record.payload["date_start"] is None for record in self.parsed.records))

    def test_database_import_is_lossless_repeatable_and_silent(self):
        from sqlalchemy import func, select
        from mcad.db import get_session_factory, make_engine
        from mcad.models import Base, Delivery, ImportBatch, ImportRow, Incident, Location, OutboxEvent

        engine = make_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(engine)
        factory = get_session_factory(engine)
        try:
            with tempfile.TemporaryDirectory() as directory:
                report_path = Path(directory) / "report.json"
                first = import_workbook(self.path, factory, report_path)
                second = import_workbook(self.path, factory)
                self.assertTrue(report_path.exists())
            self.assertEqual(first["created_records"], 440)
            self.assertFalse(first["reused_batch"])
            self.assertEqual(second["created_records"], 0)
            self.assertTrue(second["reused_batch"])
            with factory() as session:
                for model, count in [(Incident, 440), (ImportRow, 440), (ImportBatch, 1), (Location, 440), (OutboxEvent, 0), (Delivery, 0)]:
                    self.assertEqual(session.scalar(select(func.count()).select_from(model)), count)
                duplicate = session.scalars(select(Incident).where(Incident.reference_number == "20220501")).all()
                self.assertEqual(len(duplicate), 2)
                self.assertNotEqual(duplicate[0].external_id, duplicate[1].external_id)
                self.assertEqual(session.scalar(select(ImportBatch)).metadata_json["catalogs"]["countries"][0]["id"], "AF")
                self.assertTrue(all(incident.notification_suppressed for incident in session.scalars(select(Incident))))
            self.assertEqual(sha256(self.path.read_bytes()).hexdigest(), self.original_sha)
        finally:
            engine.dispose()
