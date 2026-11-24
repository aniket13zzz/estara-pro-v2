"""Truth Layer: serves only approved, valid, non-stale, non-contradicted facts and refuses
everything else with a reason code. Plus the daily staleness alert job."""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import ClientConfig
from app.core.enums import FactType, RecordStatus
from app.datacenter.entities import ENTITIES
from app.db.models import Alert, Unit
from app.jobs.staleness import run_staleness_check
from app.truth.service import Reason, TruthFact, TruthService, Unavailable
from app.truth.staleness import FixedClock
from tests.conftest import NOW
from tests.helpers import (
    ADMIN,
    csv_bytes,
    import_and_approve,
    later,
    load_sample,
    login,
    make_users,
    sample_csv,
    upload_ok,
)


@pytest.fixture
async def sample(api: AsyncClient, db: AsyncSession) -> dict[str, str]:
    await make_users(db)
    headers = await login(api, ADMIN)
    await load_sample(api, headers)
    return headers


def truth(db: AsyncSession, config: ClientConfig, days_later: int = 0) -> TruthService:
    db.expire_all()
    return TruthService(db, config, FixedClock(later(days_later)))


def refused(result: TruthFact | Unavailable, reason: Reason) -> Unavailable:
    assert isinstance(result, Unavailable), result
    assert result.reason == reason, result.detail
    return result


# ------------------------------------------------------------------------------ served


async def test_every_fact_type_is_served_from_approved_data(
    sample: dict[str, str], db: AsyncSession, client_config: ClientConfig
) -> None:
    t = truth(db, client_config)
    results = {
        FactType.PRICE: await t.get(FactType.PRICE, "SG", unit_number="B-1402"),
        FactType.AVAILABILITY: await t.get(FactType.AVAILABILITY, "SG", unit_number="B-801"),
        FactType.OFFER: await t.get(FactType.OFFER, "SG"),
        FactType.PAYMENT_PLAN: await t.get(FactType.PAYMENT_PLAN, "SH"),
        FactType.POSSESSION: await t.get(FactType.POSSESSION, "SG"),
        FactType.AREA: await t.get(FactType.AREA, "SG", unit_number="B-1402"),
        FactType.AMENITY: await t.get(FactType.AMENITY, "SG"),
    }
    assert set(results) == set(FactType)  # every fact type is covered here
    for ft, r in results.items():
        assert isinstance(r, TruthFact), (ft, r)
        assert set(r.spoken) == {"en", "hi"}
        assert r.source and r.approved_at == NOW
    assert results[FactType.PRICE].spoken["hi"] == "एक करोड़ बीस लाख रुपये"
    assert results[FactType.AVAILABILITY].spoken["en"] == "sold"
    assert results[FactType.PAYMENT_PLAN].spoken["en"] == (
        "Ready to Move Plan: 10 percent at Booking, 90 percent at Registration"
    )
    assert results[FactType.AREA].spoken["hi"] == "बारह सौ पचास स्क्वायर फ़ीट"
    assert results[FactType.AMENITY].spoken == {
        "en": "Clubhouse, Gym and Swimming pool",
        "hi": "Clubhouse, Gym और Swimming pool",
    }
    d = results[FactType.PRICE].as_dict()
    assert {"value", "display", "spoken", "source", "approved_at"} <= set(d)


# ------------------------------------------------------------------------------ missing


async def test_missing(
    sample: dict[str, str], db: AsyncSession, client_config: ClientConfig
) -> None:
    t = truth(db, client_config)
    refused(await t.price("ZZ"), Reason.MISSING)
    refused(await t.price("SG", unit_number="Z-9"), Reason.MISSING)
    refused(await t.offers("SL"), Reason.MISSING)  # no offers for this project
    refused(await t.area("SG"), Reason.MISSING)  # needs a unit or unit type
    refused(await t.amenities("ZZ"), Reason.MISSING)


async def test_offer_not_yet_valid_is_not_served(
    api: AsyncClient, sample: dict[str, str], db: AsyncSession, client_config: ClientConfig
) -> None:
    future = csv_bytes(
        "project_code,title,discount_inr,valid_from", "SL,Launch Offer,100000,2026-11-01"
    )
    await import_and_approve(api, sample, "offers.csv", future)
    r = refused(await truth(db, client_config).offers("SL"), Reason.MISSING)
    assert "not valid until 2026-11-01" in r.detail
    served = await truth(db, client_config, days_later=25).offers("SL")
    assert isinstance(served, TruthFact)


# -------------------------------------------------------------------------------- stale


async def test_stale_by_age(
    sample: dict[str, str], db: AsyncSession, client_config: ClientConfig
) -> None:
    days = client_config.truth.stale_after_days
    ok = await truth(db, client_config, days_later=days - 1).price("SG", unit_number="A-1203")
    assert isinstance(ok, TruthFact)
    t = truth(db, client_config, days_later=days + 1)
    r = refused(await t.price("SG", unit_number="A-1203"), Reason.STALE)
    assert f"approved more than {days} days ago" in r.detail
    for ft in FactType:
        result = await t.get(ft, "SG", unit_number="A-1203")
        assert isinstance(result, Unavailable) and result.reason == Reason.STALE, ft


async def test_stale_by_valid_to(
    sample: dict[str, str], db: AsyncSession, client_config: ClientConfig
) -> None:
    # Isolate valid_to: allow a year before age makes anything stale.
    cfg = client_config.model_copy(
        update={"truth": client_config.truth.model_copy(update={"stale_after_days": 365})}
    )
    # The sample Puja Offer runs until 2026-12-31 (NOW is 2026-10-12).
    assert isinstance(await truth(db, cfg, 80).offers("SG"), TruthFact)
    r = refused(await truth(db, cfg, 81).offers("SG"), Reason.STALE)
    assert "stale" in r.detail


async def test_reapproval_clears_staleness(
    api: AsyncClient,
    sample: dict[str, str],
    db: AsyncSession,
    client_config: ClientConfig,
) -> None:
    db.expire_all()
    await db.execute(update(Unit).values(approved_at=NOW - timedelta(days=60)))
    await db.commit()
    refused(await truth(db, client_config).price("SG", unit_number="A-1203"), Reason.STALE)
    await import_and_approve(api, sample, "units.csv", sample_csv("units"))
    assert isinstance(await truth(db, client_config).price("SG", unit_number="A-1203"), TruthFact)


async def test_stale_facts_served_when_not_blocked_by_config(
    sample: dict[str, str], db: AsyncSession, client_config: ClientConfig
) -> None:
    truth_cfg = client_config.truth.model_copy(update={"block_on_stale": [FactType.OFFER]})
    relaxed = client_config.model_copy(update={"truth": truth_cfg})
    t = truth(db, relaxed, days_later=45)
    assert isinstance(await t.price("SG", unit_number="A-1203"), TruthFact)
    refused(await truth(db, relaxed, days_later=90).offers("SG"), Reason.STALE)


# --------------------------------------------------------------------------- unapproved


async def test_unapproved(api: AsyncClient, db: AsyncSession, client_config: ClientConfig) -> None:
    await make_users(db)
    h = await login(api, ADMIN)
    await upload_ok(api, h, "projects.csv", sample_csv("projects"))  # validated, not approved
    t = truth(db, client_config)
    r = refused(await t.possession("SG"), Reason.UNAPPROVED)
    assert "waiting for approval" in r.detail
    refused(await t.price("SG"), Reason.UNAPPROVED)


async def test_pending_price_change_does_not_hide_behind_old_price(
    api: AsyncClient,
    sample: dict[str, str],
    db: AsyncSession,
    client_config: ClientConfig,
) -> None:
    # A unit-specific price list uploaded but not approved: price is unavailable, not guessed.
    plist = csv_bytes(
        "project_code,unit_type_code,unit_number,base_price_inr", "SG,,A-1203,7200000"
    )
    await upload_ok(api, sample, "price_lists.csv", plist)
    refused(await truth(db, client_config).price("SG", unit_number="A-1203"), Reason.UNAPPROVED)


# ------------------------------------------------------------------------ contradictory


async def test_unit_price_contradicts_a_later_price_list(
    api: AsyncClient,
    sample: dict[str, str],
    db: AsyncSession,
    client_config: ClientConfig,
) -> None:
    # Same-batch contradictions are blocked by validation; across batches the Truth Layer refuses.
    plist = csv_bytes(
        "project_code,unit_type_code,unit_number,base_price_inr", "SG,,A-1203,7500000"
    )
    await import_and_approve(api, sample, "price_lists.csv", plist)
    r = refused(
        await truth(db, client_config).price("SG", unit_number="A-1203"), Reason.CONTRADICTORY
    )
    assert "7200000" in r.detail and "7500000" in r.detail


async def test_agreeing_unit_price_and_price_list(
    api: AsyncClient,
    sample: dict[str, str],
    db: AsyncSession,
    client_config: ClientConfig,
) -> None:
    plist = csv_bytes(
        "project_code,unit_type_code,unit_number,base_price_inr", "SG,,A-1203,7200000"
    )
    await import_and_approve(api, sample, "price_lists.csv", plist)
    fact = await truth(db, client_config).price("SG", unit_number="A-1203")
    assert isinstance(fact, TruthFact)
    assert {s.table for s in fact.source} == {"units", "price_lists"}


async def test_two_approved_versions_are_contradictory(
    sample: dict[str, str], db: AsyncSession, client_config: ClientConfig
) -> None:
    db.expire_all()
    archived = await db.scalar(select(Unit).where(Unit.unit_number == "A-1203"))
    assert archived is not None
    clone = Unit(
        project_id=archived.project_id,
        unit_number="A-1203",
        bhk=2,
        property_type="apartment",
        price_inr=7_000_000,
        availability="available",
        status=RecordStatus.APPROVED,
        approved_by=archived.approved_by,
        approved_at=NOW,
        version=99,
    )
    db.add(clone)
    await db.commit()
    t = truth(db, client_config)
    r = refused(await t.price("SG", unit_number="A-1203"), Reason.CONTRADICTORY)
    assert "2 approved versions" in r.detail
    refused(await t.availability("SG", unit_number="A-1203"), Reason.CONTRADICTORY)


async def test_truth_api_returns_structured_unavailable(
    api: AsyncClient, sample: dict[str, str]
) -> None:
    r = await api.get("/api/v1/truth/facts", params={"fact_type": "price", "project_code": "ZZ"})
    body = r.json()
    assert body["status"] == "unavailable" and body["reason"] == "missing"
    assert "value" not in body and "spoken" not in body
    r = await api.get("/api/v1/truth/facts", params={"fact_type": "prices", "project_code": "SG"})
    assert r.status_code == 422  # one fact-type enum everywhere


# ---------------------------------------------------------------------- staleness alert


async def test_daily_job_raises_and_resolves_the_alert(
    api: AsyncClient,
    sample: dict[str, str],
    db: AsyncSession,
    client_config: ClientConfig,
) -> None:
    result = await run_staleness_check(db, client_config, NOW)
    assert result == {"stale": {}, "total": 0, "alert_open": False}

    result = await run_staleness_check(db, client_config, later(31))
    assert result["alert_open"] is True and result["stale"]["units"] == 14
    alerts = (await api.get("/api/v1/alerts")).json()
    assert len(alerts) == 1 and alerts[0]["kind"] == "stale_data"
    assert "not being told to customers" in alerts[0]["message"]

    # Running again updates the same alert rather than adding another.
    await run_staleness_check(db, client_config, later(32))
    assert len((await api.get("/api/v1/alerts")).json()) == 1

    # Fresh approvals resolve it.
    db.expire_all()
    for entity in ENTITIES:
        await db.execute(update(entity.model).values(approved_at=later(32)))
    await db.commit()
    result = await run_staleness_check(db, client_config, later(33))
    assert result["alert_open"] is False
    assert (await api.get("/api/v1/alerts")).json() == []
    db.expire_all()
    resolved = await db.scalar(select(Alert))
    assert resolved is not None and resolved.resolved_at is not None


def test_validity_uses_client_timezone(client_config: ClientConfig) -> None:
    from datetime import UTC, datetime

    from app.truth.staleness import StalenessPolicy

    policy = StalenessPolicy(30, "Asia/Kolkata")
    # 20:00 UTC on 31 Dec is already 1 Jan in Kolkata: an offer valid to 31 Dec is over.
    now = datetime(2026, 12, 31, 20, 0, tzinfo=UTC)
    assert policy.is_stale(now, date(2026, 12, 31), now) is True
    assert policy.is_stale(now, date(2027, 1, 1), now) is False
