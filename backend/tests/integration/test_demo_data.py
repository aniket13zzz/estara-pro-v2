"""The larger FAKE demo inventory loads through the real Data Center pipeline without errors."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import ClientConfig
from app.core.settings import Settings
from app.db.models import Project, Unit
from app.seed import DEFAULT_INVENTORY, seed_inventory
from tests.helpers import ADMIN, make_users


async def test_demo_inventory_loads_and_is_the_seed_default(
    db: AsyncSession, settings: Settings, client_config: ClientConfig
) -> None:
    from app.auth.service import get_user_by_email

    assert DEFAULT_INVENTORY.parts[-2:] == ("sample_data", "kolkata")
    await make_users(db)
    admin = await get_user_by_email(db, ADMIN)
    assert admin is not None
    messages = await seed_inventory(db, settings=settings, config=client_config, admin=admin,
                                    folder=DEFAULT_INVENTORY)  # fmt: skip
    assert len(messages) == 7 and all("Imported and approved" in m for m in messages), messages
    assert await db.scalar(select(func.count()).select_from(Project)) == 19
    assert await db.scalar(select(func.count()).select_from(Unit)) == 129  # 100+ homes
    for project in (await db.execute(select(Project))).scalars():
        assert project.name.endswith("(Sample)")
        assert (project.description or "").startswith("SAMPLE DATA (synthetic_demo_data).")
    localities = set((await db.execute(select(Project.locality))).scalars())
    assert {
        "Howrah",
        "Rajarhat",
        "Salt Lake Sector V",
        "New Town Action Area I",
        "New Town Action Area II",
        "New Town Action Area III",
    } <= localities

    # The brief's conversation: "I need a 3BHK." / "Anywhere around Kolkata." / "about 1 crore".
    from app.leads.requirements import Requirements
    from app.matching.service import Matcher
    from app.truth.staleness import FixedClock
    from tests.conftest import NOW

    CLOCK = FixedClock(NOW)

    anywhere = await Matcher(db, client_config, CLOCK).search(
        Requirements(bhk=[3], locations=["Kolkata"])
    )
    assert len(anywhere) == 3 and len({o.locality for o in anywhere}) >= 2
    budget = await Matcher(db, client_config, CLOCK).search(
        Requirements(bhk=[3], locations=["Kolkata"], budget_max_inr=10_000_000)
    )
    assert budget and all(o.price and o.price.value["min"] <= 10_000_000 for o in budget)
