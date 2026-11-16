"""Admin CLI.

    uv run python -m app.cli create-user --email admin@example.com --role administrator
    uv run python -m app.cli check-db

The password is read interactively (or from ACI_NEW_USER_PASSWORD for automation)
and is never printed or logged.
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys

from app.core.config import get_settings
from app.core.errors import AppError
from app.db.session import get_db, ping_database


def _read_password() -> str:
    env_pw = os.environ.get("ACI_NEW_USER_PASSWORD")
    if env_pw:
        return env_pw
    pw = getpass.getpass("Password: ")
    if pw != getpass.getpass("Repeat password: "):
        sys.exit("Passwords do not match")
    return pw


def cmd_create_user(args: argparse.Namespace) -> int:
    from app.services.auth import create_user

    password = _read_password()
    if len(password) < 10:
        print("Password must be at least 10 characters", file=sys.stderr)
        return 2
    gen = get_db(get_settings())
    db = next(gen)
    try:
        user = create_user(db, email=args.email, password=password, role_name=args.role, full_name=args.name)
        user_id = user.id
        next(gen, None)  # commit
    except AppError as exc:
        gen.close()
        print(f"Error: {exc.message}", file=sys.stderr)
        return 1
    print(f"Created user {args.email} ({args.role}) id={user_id}")
    return 0


def cmd_check_db(_: argparse.Namespace) -> int:
    if not get_settings().database_configured:
        print("ACI_DATABASE_URL is not set", file=sys.stderr)
        return 1
    try:
        ping_database()
    except Exception as exc:  # noqa: BLE001
        from app.core.logging import redact

        print(f"Database connection FAILED: {redact(str(exc))}", file=sys.stderr)
        return 1
    print("Database connection OK")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="aci")
    sub = parser.add_subparsers(dest="command", required=True)

    p_user = sub.add_parser("create-user", help="Create a dashboard user")
    p_user.add_argument("--email", required=True)
    p_user.add_argument("--role", default="administrator", choices=["administrator", "operator", "viewer"])
    p_user.add_argument("--name", default=None)
    p_user.set_defaults(func=cmd_create_user)

    p_db = sub.add_parser("check-db", help="Verify database connectivity")
    p_db.set_defaults(func=cmd_check_db)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
