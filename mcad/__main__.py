import argparse
import getpass
import warnings
from sqlalchemy.exc import SQLAlchemyError

from .auth import AccountConflict, create_admin, reset_password
from .db import get_engine, get_session_factory
from .migrations import migrate


def main():
    parser = argparse.ArgumentParser(prog="python -m mcad")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("migrate", help="Apply database migrations")
    commands.add_parser("create-admin", help="Create an administrator account").add_argument("--email", required=True)
    commands.add_parser("reset-password", help="Change an account password and revoke its sessions").add_argument("--email", required=True)
    args = parser.parse_args()
    if args.command == "migrate":
        try:
            migrate(get_engine())
        except (SQLAlchemyError, ValueError, OSError):
            raise SystemExit("MCAD migration failed. Check the database connection and migration configuration.") from None
        print("MCAD database schema is up to date.")
        return

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            password = getpass.getpass("Password: ")
            confirmation = getpass.getpass("Repeat password: ")
    except (getpass.GetPassWarning, EOFError, KeyboardInterrupt):
        raise SystemExit("An interactive terminal is required for password entry.") from None
    if password != confirmation:
        raise SystemExit("Passwords do not match.")

    try:
        factory = get_session_factory(get_engine())
        if args.command == "create-admin":
            create_admin(factory, args.email, password)
        else:
            reset_password(factory, args.email, password)
    except (ValueError, AccountConflict) as error:
        raise SystemExit(str(error)) from None
    except (SQLAlchemyError, OSError):
        raise SystemExit("Account update failed. Check the database connection and apply migrations first.") from None
    print("Administrator created." if args.command == "create-admin" else "Password changed. Previous sessions were revoked.")


if __name__ == "__main__":
    main()
