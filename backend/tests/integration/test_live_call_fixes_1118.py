"""Live website call 2026-11-18 (four screenshots): every lookup failed, "any available location"
was kept as a place name, the callback could not be made, and a browser call was "transferred".

Each test walks the conversation through the HTTP addresses Vapi's API Request tools call
(assistant B), with one call id, like the real call."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Call, CustomerIdentity, Handoff, Lead
from app.leads import requirements as reqs
from tests.integration.test_native_voice import native  # noqa: F401
from tests.integration.test_voice import AUTH

pytestmark = pytest.mark.usefixtures("inventory")
URL = "/api/v1/webhooks/vapi/api"


async def tool(client: AsyncClient, name: str, args: dict[str, Any], call: str = "web-1",
               caller: str | None = None) -> dict[str, Any]:  # fmt: skip
    query = f"?call_id={call}" + (f"&caller={caller}" if caller else "")
    r = await client.post(f"{URL}/{name}{query}", json=args, headers=AUTH)
    assert r.status_code == 200, r.text
    out: dict[str, Any] = r.json()
    return out


async def saved(db: AsyncSession) -> reqs.Requirements:
    lead = (await db.execute(select(Lead))).scalar_one()
    return await reqs.load(db, lead)


async def test_the_screenshot_call_now_finds_homes(
    native: tuple[FastAPI, AsyncClient],  # noqa: F811
    db: AsyncSession,
) -> None:
    _, c = native
    # "I need a 3BHK." / "Whatever. Order the available location."
    first = await tool(c, "search_properties", {"bhk": "3", "locations": "any available location"})
    assert first["matches"] and "unknown_areas" not in first
    assert all("differs" not in m for m in first["matches"])  # any area means no area mismatch
    # "My budget is around 10 CR." / "Next month."
    later = {"bhk": [3], "budget_text": "10 crore", "timeline_months": "next month"}
    second = await tool(c, "search_properties", later)
    assert second["matches"]
    req = await saved(db)
    assert req.bhk == [3] and req.locations == [] and req.timeline_months == 1
    assert req.budget_max_inr == 100_000_000


async def test_any_location_clears_an_area_said_earlier(
    native: tuple[FastAPI, AsyncClient],  # noqa: F811
    db: AsyncSession,
) -> None:
    _, c = native
    await tool(c, "search_properties", {"bhk": [3], "locations": ["Howrah"]})
    assert (await saved(db)).locations == ["Howrah"]
    out = await tool(c, "search_properties", {"any_location": True})
    assert (await saved(db)).locations == [] and out["matches"]
    assert out["exact_match"] is True


async def test_changed_budget_and_area_win(
    native: tuple[FastAPI, AsyncClient],  # noqa: F811
    db: AsyncSession,
) -> None:
    _, c = native
    await tool(c, "search_properties", {"bhk": [2], "locations": ["New Town"],
                                        "budget_text": "80 lakh"})  # fmt: skip
    await tool(c, "search_properties", {"budget_text": "1.2 crore", "locations": ["Rajarhat"]})
    req = await saved(db)
    assert req.budget_max_inr == 12_000_000 and req.locations == ["Rajarhat"] and req.bhk == [2]


SPOKEN = [("next month", 1), ("Immediately", 0), ("six months", 6), ("3", 3), ("soon-ish", None)]


@pytest.mark.parametrize(("said", "months"), SPOKEN)
async def test_spoken_move_in_times(
    native: tuple[FastAPI, AsyncClient],  # noqa: F811
    db: AsyncSession,
    said: str,
    months: int | None,
) -> None:
    _, c = native
    await tool(c, "search_properties", {"bhk": [2], "timeline_months": said})
    assert (await saved(db)).timeline_months == months  # an unclear phrase is dropped, not an error


async def test_web_callback_asks_checks_and_saves_the_number(
    native: tuple[FastAPI, AsyncClient],  # noqa: F811
    db: AsyncSession,
) -> None:
    _, c = native
    assert (await tool(c, "request_callback", {}))["status"] == "needs_number"
    assert (await tool(c, "request_callback", {"phone": "12345"}))["status"] == "invalid_number"
    assert (await tool(c, "request_callback", {"phone": "1234567890"}))[
        "status"
    ] == "invalid_number"
    assert await db.scalar(select(func.count()).select_from(Handoff)) == 0  # nothing yet
    done = await tool(c, "request_callback", {"phone": "98300 12345", "preferred_time": "evening"})
    assert done["status"] == "callback_saved" and done["number_ends_with"] == "2345"
    handoff = (await db.execute(select(Handoff))).scalar_one()
    assert handoff.reason == "callback_requested"
    assert handoff.context["details"] == {"phone": "+919830012345", "preferred_time": "evening"}
    assert "They prefer: evening." in handoff.context["recommended_next_action"]
    identity = (await db.execute(select(CustomerIdentity))).scalar_one()
    assert (identity.value, identity.verified) == ("+919830012345", False)  # said, not proved
    lead = (await db.execute(select(Lead))).scalar_one()
    assert lead.state.value == "handed_off"


async def test_phone_callback_uses_the_calling_number(
    native: tuple[FastAPI, AsyncClient],  # noqa: F811
    db: AsyncSession,
) -> None:
    _, c = native
    done = await tool(c, "request_callback", {}, call="phone-1", caller="+919000000077")
    assert done["status"] == "callback_saved" and done["number_ends_with"] == "0077"


async def test_a_callback_that_cannot_be_saved_is_never_called_booked(
    native: tuple[FastAPI, AsyncClient],  # noqa: F811
    db: AsyncSession,
) -> None:
    _, c = native
    r = await c.post(f"{URL}/request_callback", json={"phone": "9830012345"}, headers=AUTH)
    out = r.json()
    assert out["status"] == "not_saved" and "Never say it is booked" in out["instruction"]
    assert await db.scalar(select(func.count()).select_from(Handoff)) == 0


async def test_without_a_call_id_a_search_still_answers_and_saves_nothing(
    native: tuple[FastAPI, AsyncClient],  # noqa: F811
    db: AsyncSession,
) -> None:
    _, c = native
    r = await c.post(f"{URL}/search_properties", json={"bhk": [3]}, headers=AUTH)
    assert r.json()["matches"]  # real homes instead of "I can't retrieve options"
    assert await db.scalar(select(func.count()).select_from(Call)) == 0
    assert await db.scalar(select(func.count()).select_from(Lead)) == 0


async def test_a_website_call_is_offered_a_callback_not_a_transfer(
    native: tuple[FastAPI, AsyncClient],  # noqa: F811
) -> None:
    _, c = native
    web = await tool(c, "transfer_to_human", {"reason": "human_requested"})
    assert web["transfer_now"] is False and "request_callback" in web["instruction"]
    phone = await tool(c, "transfer_to_human", {"reason": "human_requested"}, call="phone-2",
                       caller="+919000000078")  # fmt: skip
    assert "transfer_now" in phone and "website call" not in phone.get("instruction", "")
