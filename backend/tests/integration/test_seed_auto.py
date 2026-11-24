"""--auto keeps a demo machine ready for calls, and never touches production or real inventory."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.service import get_user_by_email
from app.core.config import ClientConfig
from app.core.settings import Settings
from app.seed import DEFAULT_INVENTORY, auto_decision, seed_inventory
from tests.helpers import ADMIN, make_users


async def test_an_empty_database_gets_the_demo_homes(db: AsyncSession, settings: Settings) -> None:
    assert await auto_decision(db, settings, datetime.now(UTC)) is None


async def test_never_in_production(db: AsyncSession, settings: Settings) -> None:
    live = settings.model_copy(update={"environment": "production"})
    assert "production" in (await auto_decision(db, live, datetime.now(UTC)) or "")


async def test_real_inventory_is_left_alone(db: AsyncSession, settings: Settings,
                                            inventory: None) -> None:  # fmt: skip
    # The test fixture's projects are not marked synthetic_demo_data: they stand for real data.
    reason = await auto_decision(db, settings, datetime.now(UTC) + timedelta(days=60))
    assert reason is not None and "real inventory" in reason


async def test_demo_homes_are_refreshed_before_they_go_stale(
    db: AsyncSession, settings: Settings, client_config: ClientConfig
) -> None:
    await make_users(db)
    admin = await get_user_by_email(db, ADMIN)
    assert admin is not None
    await seed_inventory(db, settings=settings, config=client_config, admin=admin,
                         folder=DEFAULT_INVENTORY)  # fmt: skip
    now = datetime.now(UTC)
    assert "fresh" in (await auto_decision(db, settings, now) or "")
    assert await auto_decision(db, settings, now + timedelta(days=8)) is None  # reload
