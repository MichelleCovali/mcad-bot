
"""Create a fictional review candidate in a NEW, explicitly selected SQLite file.

This utility never reads DATABASE_URL or credentials, and importing it has no
side effects. Existing databases are refused to protect real incident data.
"""
import argparse
from pathlib import Path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True, help="New local .sqlite3 file; must not already exist")
    args = parser.parse_args(argv)
    database = Path(args.database).expanduser().resolve()
    if "://" in args.database or database.suffix.lower() not in {".sqlite", ".sqlite3", ".db"}:
        parser.error("Choose a new local SQLite file, not a database URL.")
    database.parent.mkdir(parents=True, exist_ok=True)
    try:
        # Exclusive creation prevents a typo from opening an existing database.
        with database.open("xb"):
            pass
    except FileExistsError:
        parser.error("The database already exists. Use a new isolated preview file.")

    from sqlalchemy.engine import URL
    from mcad.db import get_session_factory, make_engine
    from mcad.migrations import migrate
    from mcad.models import Candidate

    engine = make_engine(URL.create("sqlite", database=str(database)).render_as_string(hide_password=False))
    try:
        migrate(engine)
        with get_session_factory(engine).begin() as session:
            session.add(Candidate(
                title="PREVIEW ONLY — fictional port cybersecurity report",
                summary="Fictional data for reviewing the MCAD interface. This is not a real incident.",
                source_url="https://example.invalid/mcad-preview-incident",
                matched_keywords=["maritime", "cyberattack"],
                status="pending",
            ))
    finally:
        engine.dispose()
    print(f"Fictional preview candidate created in {database}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

