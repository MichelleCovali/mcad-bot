import argparse
from sqlalchemy.exc import SQLAlchemyError

from .db import get_engine
from .migrations import migrate


def main():
    parser = argparse.ArgumentParser(prog="python -m mcad")
    parser.add_argument("command", choices=["migrate"])
    args = parser.parse_args()
    if args.command == "migrate":
        try:
            migrate(get_engine())
        except (SQLAlchemyError, ValueError, OSError):
            raise SystemExit("MCAD migration failed. Check the database connection and migration configuration.") from None
        print("MCAD database schema is up to date.")


if __name__ == "__main__":
    main()
