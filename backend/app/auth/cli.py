"""Command-line user management.

    python -m app.auth.cli create-user --email admin@example.com --name "Admin" --role client_admin

The password is read from ESTARA_NEW_USER_PASSWORD or prompted for, never passed as an argument
(arguments end up in shell history and process lists).
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import os
import sys

from app.audit.service import record
from app.auth.service import create_user, get_user_by_email
from app.core.enums import Role
from app.core.settings import Settings
from app.db.session import make_engine, make_sessionmaker


async def _create(email: str, name: str, role: Role, password: str, *, if_missing: bool) -> int:
    settings = Settings()
    engine = make_engine(settings.database_url)
    try:
        async with make_sessionmaker(engine)() as db:
            existing = await get_user_by_email(db, email)
            if existing is not None:
                if if_missing:
                    print(f"User {email} already exists; nothing to do.")
                    return 0
                print(f"User {email} already exists.", file=sys.stderr)
                return 1
            user = await create_user(db, email=email, name=name, password=password, role=role)
            await record(
                db,
                "user.created",
                actor_type="cli",
                entity_type="user",
                entity_id=user.id,
                details={"role": role.value},
            )
            await db.commit()
            print(f"Created {role.value} user {email}.")
            return 0
    finally:
        await engine.dispose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.auth.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    cu = sub.add_parser("create-user")
    cu.add_argument("--email", required=True)
    cu.add_argument("--name", required=True)
    cu.add_argument("--role", required=True, choices=[r.value for r in Role])
    cu.add_argument("--if-missing", action="store_true", help="succeed if the user exists")
    args = parser.parse_args(argv)

    password = os.environ.get("ESTARA_NEW_USER_PASSWORD") or getpass.getpass("Password: ")
    try:
        return asyncio.run(
            _create(args.email, args.name, Role(args.role), password, if_missing=args.if_missing)
        )
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
