"""Leads (transitions logged, requirements, scoring), matching (order and reasons) and visit
slots, against the sample inventory."""

from __future__ import annotations

from datetime import date, time, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.service import create_user
from app.core.config import ClientConfig
from app.core.enums import Channel, LeadBand, LeadState, Role, VisitState
from app.db.models import Customer, Lead, LeadEvent, LeadMatch, Project, SiteVisit
from app.leads import requirements as reqs
from app.leads.requirements import Requirements
from app.leads.scoring import advance, evaluate
from app.leads.state import Actor, TransitionRejected, ensure_open_lead, transition
from app.matching.service import Matcher, save_matches
from app.truth.staleness import FixedClock
from app.visits.slots import available_slots
from tests.conftest import NOW, TEST_PASSWORD

CLOCK = FixedClock(NOW)
TWO_BHK_NEW_TOWN = Requirements(
    budget_max_inr=8_000_000, locations=["New Town"], bhk=[2], property_type="apartment"
)


async def _lead(db: AsyncSession) -> Lead:
    customer = Customer(name="Asha")
    db.add(customer)
    await db.flush()
    return await ensure_open_lead(db, customer, Channel.WEB_CHAT)


async def _events(db: AsyncSession, lead: Lead) -> list[tuple[str, str | None]]:
    rows = await db.execute(
        select(LeadEvent.event_type, LeadEvent.to_state)
        .where(LeadEvent.lead_id == lead.id)
        .order_by(LeadEvent.created_at)
    )
    return [(e, s.value if s else None) for e, s in rows.tuples()]


# ---------------------------------------------------------------------------- transitions


async def test_transitions_are_logged_and_rejections_keep_state(db: AsyncSession) -> None:
    lead = await _lead(db)
    assert lead.state == LeadState.NEW_ENQUIRY
    ok = await transition(db, lead, LeadState.ENGAGED, actor=Actor.SYSTEM, trigger="test")
    assert ok.ok and lead.state == LeadState.ENGAGED
    bad = await transition(db, lead, LeadState.WON, actor=Actor.SYSTEM, trigger="llm said so")
    assert not bad.ok and lead.state == LeadState.ENGAGED
    assert await _events(db, lead) == [
        ("created", "new_enquiry"),
        ("transition", "engaged"),
        ("transition_rejected", "won"),
    ]


async def test_staff_transitions_need_a_user_and_can_be_strict(db: AsyncSession) -> None:
    lead = await _lead(db)
    with pytest.raises(ValueError, match="acting user"):
        await transition(db, lead, LeadState.LOST, actor=Actor.STAFF, trigger="x")
    staff = await create_user(
        db, email="s@example.com", name="S", password=TEST_PASSWORD, role=Role.SALES_STAFF
    )
    with pytest.raises(TransitionRejected):
        await transition(
            db,
            lead,
            LeadState.VISIT_CONFIRMED,
            actor=Actor.STAFF,
            trigger="x",
            actor_user_id=staff.id,
            strict=True,
        )


async def test_one_open_lead_and_a_new_one_after_won(db: AsyncSession) -> None:
    lead = await _lead(db)
    customer = await db.get(Customer, lead.customer_id)
    assert customer is not None
    assert (await ensure_open_lead(db, customer, Channel.VOICE)).id == lead.id
    lead.state = LeadState.WON
    await db.flush()
    fresh = await ensure_open_lead(db, customer, Channel.VOICE)
    assert fresh.id != lead.id and fresh.state == LeadState.NEW_ENQUIRY


# --------------------------------------------------------------------------- requirements


async def test_requirements_merge_and_log(db: AsyncSession) -> None:
    lead = await _lead(db)
    _, changed = await reqs.save(
        db, lead, Requirements(budget_max_inr=8_000_000, bhk=[2]), source="test"
    )
    assert sorted(changed) == ["bhk", "budget_max_inr"]
    merged, changed = await reqs.save(
        db, lead, Requirements(locations=["Rajarhat"], loan="needed"), source="test"
    )
    assert merged.budget_max_inr == 8_000_000 and merged.locations == ["Rajarhat"]
    assert (await reqs.load(db, lead)).loan == "needed"
    # A new lower budget below the old minimum keeps only the newest statement.
    await reqs.save(db, lead, Requirements(budget_min_inr=9_000_000), source="t")
    merged, _ = await reqs.save(db, lead, Requirements(budget_max_inr=7_000_000), source="t")
    assert (merged.budget_min_inr, merged.budget_max_inr) == (None, 7_000_000)
    assert [e for e, _ in await _events(db, lead)].count("requirements_updated") == 4


async def test_advance_engages_then_qualifies(
    db: AsyncSession, client_config: ClientConfig
) -> None:
    lead = await _lead(db)
    await advance(db, lead, Requirements(bhk=[2]), client_config)
    assert lead.state == LeadState.ENGAGED
    await advance(db, lead, TWO_BHK_NEW_TOWN, client_config)
    assert lead.state == LeadState.QUALIFIED


async def test_one_message_can_qualify_directly(
    db: AsyncSession, client_config: ClientConfig
) -> None:
    lead = await _lead(db)
    await advance(db, lead, TWO_BHK_NEW_TOWN, client_config)
    assert lead.state == LeadState.QUALIFIED


# -------------------------------------------------------------------------------- scoring


async def test_scoring_from_config_weights(
    db: AsyncSession, inventory: None, client_config: ClientConfig
) -> None:
    lead = await _lead(db)
    ev = await evaluate(db, lead, TWO_BHK_NEW_TOWN, client_config, CLOCK, now=NOW)
    # budget_match 30 + location_match 20 of 100
    assert ev.rules == {
        "budget_match": True,
        "location_match": True,
        "timeline_under_6m": False,
        "loan_ready": False,
        "visit_requested": False,
    }
    assert (ev.score, ev.band, ev.qualified) == (50, LeadBand.WARM, True)
    assert lead.score == 50 and lead.band == LeadBand.WARM
    assert "budget_match met (30)" in ev.reason and "= 50/100, warm" in ev.reason

    hot = TWO_BHK_NEW_TOWN.model_copy(update={"timeline_months": 3, "loan": "pre_approved"})
    ev = await evaluate(db, lead, hot, client_config, CLOCK, now=NOW)
    assert (ev.score, ev.band) == (80, LeadBand.HOT)


async def test_scoring_misses(
    db: AsyncSession, inventory: None, client_config: ClientConfig
) -> None:
    lead = await _lead(db)
    # 30 lakh buys nothing in the sample data; Salt Lake has no project.
    ev = await evaluate(
        db,
        lead,
        Requirements(budget_max_inr=3_000_000, locations=["Salt Lake"], loan="needed"),
        client_config,
        CLOCK,
        now=NOW,
    )
    assert (ev.score, ev.band) == (0, LeadBand.COLD)
    assert ev.missing == ["bhk"] and not ev.qualified  # size, area, budget qualify (v1.8.0)


async def test_scoring_counts_a_visit_and_leaves_state_alone(
    db: AsyncSession, inventory: None, client_config: ClientConfig
) -> None:
    lead = await _lead(db)
    project = await db.scalar(select(Project).where(Project.code == "SG"))
    assert project is not None
    db.add(
        SiteVisit(
            lead_id=lead.id,
            customer_id=lead.customer_id,
            project_id=project.id,
            visit_date=date(2026, 10, 17),
            slot=time(11),
            state=VisitState.REQUESTED,
            requested_via=Channel.WEB_CHAT,
        )
    )
    await db.flush()
    ev = await evaluate(db, lead, Requirements(), client_config, CLOCK, now=NOW)
    assert ev.rules["visit_requested"] and ev.score == 20
    assert lead.state == LeadState.NEW_ENQUIRY


# ------------------------------------------------------------------------------- matching


async def test_ranking_order_and_reasons(
    db: AsyncSession, inventory: None, client_config: ClientConfig
) -> None:
    options = await Matcher(db, client_config, CLOCK).search(TWO_BHK_NEW_TOWN)
    assert [(o.project_code, o.unit_type_code, o.score) for o in options] == [
        ("SG", "2BHK-A", 95),  # in budget, in New Town
        ("SH", "2BHK-C", 65),  # in budget, Behala
        ("SL", "2BHK", 45),  # 85 lakh: slightly above an 80 lakh budget
    ]
    sg = options[0]
    assert sg.components == {"budget": 40, "location": 30, "bhk": 20, "timing": 5}
    assert sg.reasons["en"] == (
        "2 BHK Classic at Sunrise Greens (Sample), New Town Action Area II, "
        "72 to 78 lakh rupees, within your budget, in your preferred area, "
        "possession December 2027, 2 units available"
    )
    assert sg.reasons["hi"] == (
        "Sunrise Greens (Sample), New Town Action Area II में 2 BHK Classic, "
        "बहत्तर लाख से अठहत्तर लाख रुपये, आपके बजट में, आपके पसंद के area में, "
        "possession दिसंबर 2027, दो units उपलब्ध"
    )
    assert "slightly above your budget" in options[2].reasons["en"]
    assert "in Rajarhat, not in New Town" in options[2].reasons["en"]
    assert sg.as_tool_result("hi")["price"] == "बहत्तर लाख से अठहत्तर लाख रुपये"


async def test_timeline_prefers_ready_to_move(
    db: AsyncSession, inventory: None, client_config: ClientConfig
) -> None:
    req = Requirements(budget_max_inr=8_000_000, bhk=[2], timeline_months=3)
    options = await Matcher(db, client_config, CLOCK).search(req)
    assert options[0].project_code == "SH"  # possession March 2026: ready to move
    assert "ready to move" in options[0].reasons["en"]
    assert "later than your timeline" in options[1].reasons["en"]


async def test_budget_mismatch_returns_nothing(
    db: AsyncSession, inventory: None, client_config: ClientConfig
) -> None:
    assert (
        await Matcher(db, client_config, CLOCK).search(Requirements(budget_max_inr=3_000_000)) == []
    )


async def test_stale_prices_are_not_matched(
    db: AsyncSession, inventory: None, client_config: ClientConfig
) -> None:
    later = FixedClock(NOW + timedelta(days=45))  # every price is stale by then
    with_budget = await Matcher(db, client_config, later).search(TWO_BHK_NEW_TOWN)
    assert with_budget == []


async def test_matches_are_saved_on_the_lead(
    db: AsyncSession, inventory: None, client_config: ClientConfig
) -> None:
    lead = await _lead(db)
    options = await Matcher(db, client_config, CLOCK).search(TWO_BHK_NEW_TOWN)
    await save_matches(db, lead, options)
    rows = (await db.execute(select(LeadMatch).order_by(LeadMatch.rank))).scalars().all()
    assert [r.rank for r in rows] == [1, 2, 3]
    assert set(rows[0].reasons) == {"en", "hi"}


# ---------------------------------------------------------------------------------- slots


async def test_slots_follow_config(
    db: AsyncSession, inventory: None, client_config: ClientConfig
) -> None:
    # NOW is Monday 12 October 2026, 11:30 in Kolkata. Visits: Sat/Sun 11:00, 14:00, 16:00.
    slots = await available_slots(db, client_config, "SG", NOW, limit=4)
    assert [s.id for s in slots] == [
        "2026-10-17T11:00",
        "2026-10-17T14:00",
        "2026-10-17T16:00",
        "2026-10-18T11:00",
    ]
    assert all(s.remaining == 3 for s in slots)


async def test_slot_capacity_and_lead_time(
    db: AsyncSession, inventory: None, client_config: ClientConfig
) -> None:
    lead = await _lead(db)
    project = await db.scalar(select(Project).where(Project.code == "SG"))
    assert project is not None
    for _ in range(3):  # slot_capacity: 3
        db.add(
            SiteVisit(
                lead_id=lead.id,
                customer_id=lead.customer_id,
                project_id=project.id,
                visit_date=date(2026, 10, 17),
                slot=time(11),
                state=VisitState.REQUESTED,
                requested_via=Channel.WEB_CHAT,
            )
        )
    await db.flush()
    slots = await available_slots(db, client_config, "SG", NOW, limit=2)
    assert slots[0].id == "2026-10-17T14:00"
    # Another project's slots are unaffected.
    assert (await available_slots(db, client_config, "SH", NOW, limit=1))[0].id == (
        "2026-10-17T11:00"
    )
    # Saturday 17th 09:30 local: every slot that day is under 24 hours away.
    saturday_morning = NOW + timedelta(days=4, hours=22)
    first = (await available_slots(db, client_config, "SH", saturday_morning, limit=1))[0]
    assert first.id == "2026-10-18T11:00"
