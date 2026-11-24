"""Sample data loader: ``python -m app.seed``.

1. Creates the sample admin and sales users from SEED_* environment variables (skipped if they
   already exist).
2. Imports the fake sample inventory (``SAMPLE_INVENTORY_DIR``, default
   ``backend/sample_data/kolkata``) through the normal Data Center path — upload,
   validate, approve as the sample admin — one CSV at a time, parents first.

Running it again re-imports the same data as new versions (shown as "unchanged" and approved),
which refreshes its freshness.

``python -m app.seed --auto`` (run by the backend container on every start, docker-compose.yml)
keeps a demo machine ready for calls: it loads the demo homes when there is no inventory yet and
re-imports them when they are more than a week old (before they would go stale). It never runs
in production, never touches a database that holds any real (non-synthetic) project, and never
stops the backend from starting. One Python entry point keeps `make seed-sample` and the plain
`docker compose run` command (Windows) identical.
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.service import record
from app.auth.service import create_user, get_user_by_email
from app.core.config import ClientConfig, load_config
from app.core.enums import ImportBatchStatus, RecordStatus, Role
from app.core.settings import Settings
from app.datacenter import service
from app.datacenter.entities import ENTITIES
from app.db.models import Project, User
from app.db.session import make_engine, make_sessionmaker

SAMPLE_USERS: tuple[tuple[str, str, str, Role], ...] = (
    ("SEED_ADMIN_EMAIL", "SEED_ADMIN_PASSWORD", "Sample Admin", Role.CLIENT_ADMIN),
    ("SEED_STAFF_EMAIL", "SEED_STAFF_PASSWORD", "Sample Sales", Role.SALES_STAFF),
)
# The larger FAKE demo set (10 projects); tests use their own small fixture.
DEFAULT_INVENTORY = Path(__file__).resolve().parents[1] / "sample_data" / "kolkata"


SYNTHETIC_MARK = "synthetic_demo_data"
REFRESH_AFTER = timedelta(days=7)  # well inside truth.stale_after_days (30)


class SeedError(Exception):
    pass


async def seed_users(db: AsyncSession) -> list[str]:
    messages = []
    for email_var, password_var, name, role in SAMPLE_USERS:
        email = os.environ.get(email_var, "").strip()
        password = os.environ.get(password_var, "")
        if not email or not password:
            raise SeedError(f"Set {email_var} and {password_var} in .env")
        if await get_user_by_email(db, email) is not None:
            messages.append(f"User {email} already exists; skipped.")
            continue
        user = await create_user(db, email=email, name=name, password=password, role=role)
        await record(
            db,
            "user.created",
            actor_type="seed",
            entity_type="user",
            entity_id=user.id,
            details={"role": role.value},
        )
        messages.append(f"Created {role.value} user {email}.")
    await db.commit()
    return messages


def read_inventory(folder: Path) -> list[tuple[str, str, bytes]]:
    """(entity, filename, content) for each sample CSV present, parents first."""
    if not folder.is_dir():
        raise SeedError(f"Sample inventory not found: {folder} (set SAMPLE_INVENTORY_DIR)")
    files = []
    for entity in ENTITIES:
        path = folder / f"{entity.name}.csv"
        if path.is_file():
            files.append((entity.name, path.name, path.read_bytes()))
    return files


async def seed_inventory(
    db: AsyncSession, *, settings: Settings, config: ClientConfig, admin: User, folder: Path
) -> list[str]:
    messages = []
    for entity_name, filename, content in read_inventory(folder):
        batch = await service.create_batch(
            db,
            settings=settings,
            config=config,
            filename=filename,
            data=content,
            meta=service.UploadMeta(entity=entity_name),
            user_id=admin.id,
        )
        if batch.status != ImportBatchStatus.VALIDATED:
            problems = await service.findings(db, batch.id)
            details = "; ".join(f"row {p.row_number}: {p.message}" for p in problems[:5])
            raise SeedError(f"{filename} did not validate: {details}")
        await service.approve(db, batch.id, user_id=admin.id, now=datetime.now(UTC))
        summary = batch.summary["entities"].get(entity_name, {})
        messages.append(
            f"Imported and approved {filename}: {batch.row_count} rows "
            f"({summary.get('new', 0)} new, {summary.get('changed', 0)} changed, "
            f"{summary.get('unchanged', 0)} unchanged)."
        )
    return messages


async def run() -> list[str]:
    settings = Settings()
    config = load_config()
    folder = Path(os.environ.get("SAMPLE_INVENTORY_DIR") or DEFAULT_INVENTORY)
    engine = make_engine(settings.database_url)
    try:
        async with make_sessionmaker(engine)() as db:
            messages = await seed_users(db)
            admin = await get_user_by_email(db, os.environ["SEED_ADMIN_EMAIL"])
            if admin is None or admin.role != Role.CLIENT_ADMIN:
                raise SeedError("The SEED_ADMIN_EMAIL user must be a client_admin")
            messages += await seed_inventory(
                db, settings=settings, config=config, admin=admin, folder=folder
            )
            return messages
    finally:
        await engine.dispose()


async def auto_decision(db: AsyncSession, settings: Settings, now: datetime) -> str | None:
    """Why --auto should skip, or None to (re)load the demo homes."""
    if settings.environment == "production" and not settings.demo_mode:
        return "production: demo homes are never loaded automatically"
    approved = Project.status == RecordStatus.APPROVED
    real = await db.scalar(select(func.count()).select_from(Project).where(
        approved, ~func.coalesce(Project.description, "").contains(SYNTHETIC_MARK)))  # fmt: skip
    if real:
        return "real inventory is loaded: demo homes left alone"
    newest = await db.scalar(select(func.max(Project.approved_at)).where(approved))
    if newest is not None and now - newest < REFRESH_AFTER:
        return "demo homes are loaded and fresh"
    return None


async def run_auto() -> list[str]:
    settings = Settings()
    engine = make_engine(settings.database_url)
    try:
        async with make_sessionmaker(engine)() as db:
            reason = await auto_decision(db, settings, datetime.now(UTC))
    finally:
        await engine.dispose()
    if reason:
        return [f"Demo data: {reason}."]
    return ["Demo data: loading the synthetic demo homes...", *await run()]


def main(argv: list[str] | None = None) -> int:
    auto = "--auto" in (sys.argv[1:] if argv is None else argv)
    try:
        for line in asyncio.run(run_auto() if auto else run()):
            print(line)
    except (SeedError, ValueError, KeyError) as exc:
        print(f"Seed failed: {exc}", file=sys.stderr)
        # --auto runs on every backend start: a missing SEED_* value must not stop the backend.
        return 0 if auto else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
