"""Identity resolution: merge only on an exact verified identifier; flag everything weaker."""

from __future__ import annotations

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import IdentityType
from app.customers.identity import (
    IdentityClaim,
    country_code_of,
    normalise_email,
    normalise_phone,
    resolve,
)
from app.db.models import Customer, CustomerIdentity
from tests.conftest import NOW

PHONE = IdentityType.PHONE
WA = IdentityType.WHATSAPP
EMAIL = IdentityType.EMAIL
WEB = IdentityType.WEB_SESSION


async def _resolve(db: AsyncSession, *claims: IdentityClaim, primary: IdentityType, name=None):  # type: ignore[no-untyped-def]
    r = await resolve(
        db, list(claims), primary=primary, default_country_code="91", now=NOW, name=name
    )
    await db.commit()
    return r


async def _customers(db: AsyncSession) -> int:
    return int(await db.scalar(select(func.count()).select_from(Customer)) or 0)


# --------------------------------------------------------------------------- normalising


@pytest.mark.parametrize(
    "raw",
    [
        "+91 98300 12345",
        "098300 12345",
        "9830012345",
        "0091-98300-12345",
        "91 9830012345",
        "+91-983-001-2345",
    ],
)
def test_indian_phone_forms_normalise_to_e164(raw: str) -> None:
    assert normalise_phone(raw) == "+919830012345"


@pytest.mark.parametrize("raw", ["12345", "abc", "+91 0830012345", "", "98300-1234"])
def test_bad_phones_are_rejected(raw: str) -> None:
    assert normalise_phone(raw) is None


def test_other_country_codes_and_emails() -> None:
    assert normalise_phone("+44 20 7946 0958") == "+442079460958"
    assert normalise_phone("2079460958", "44") == "+442079460958"
    assert country_code_of("+919000000002") == "91"
    assert country_code_of("+14155550100") == "1"
    assert normalise_email("  Rahul@Example.COM ") == "rahul@example.com"
    assert normalise_email("not-an-email") is None


# ------------------------------------------------------------------------------ merging


async def test_call_then_whatsapp_from_same_number_is_one_customer(db: AsyncSession) -> None:
    first = await _resolve(db, IdentityClaim(PHONE, "+919830012345", True), primary=PHONE)
    second = await _resolve(db, IdentityClaim(WA, "919830012345", True), primary=WA)
    assert first.created and not second.created
    assert first.customer.id == second.customer.id
    assert await _customers(db) == 1


async def test_same_verified_email_merges(db: AsyncSession) -> None:
    a = await _resolve(db, IdentityClaim(EMAIL, "Asha@Example.com", True), primary=EMAIL)
    b = await _resolve(db, IdentityClaim(EMAIL, "asha@example.com", True), primary=EMAIL)
    assert a.customer.id == b.customer.id


async def test_same_web_session_returns_same_customer(db: AsyncSession) -> None:
    a = await _resolve(db, IdentityClaim(WEB, "sess-1", True), primary=WEB)
    b = await _resolve(db, IdentityClaim(WEB, "sess-1", True), primary=WEB)
    assert a.customer.id == b.customer.id


async def test_new_identifiers_are_added_to_the_matched_customer(db: AsyncSession) -> None:
    a = await _resolve(db, IdentityClaim(PHONE, "9830012345", True), primary=PHONE)
    b = await _resolve(
        db,
        IdentityClaim(PHONE, "9830012345", True),
        IdentityClaim(EMAIL, "asha@example.com", True),
        primary=PHONE,
    )
    assert a.customer.id == b.customer.id
    rows = (await db.execute(select(CustomerIdentity.identity_type))).scalars().all()
    assert sorted(rows) == [EMAIL, PHONE]


# ------------------------------------------------------------------------ must not merge


async def test_phone_typed_into_web_chat_does_not_merge(db: AsyncSession) -> None:
    caller = await _resolve(
        db, IdentityClaim(PHONE, "9830012345", True), primary=PHONE, name="Asha"
    )
    # A web visitor types the same number. Anyone can type any number: never merge.
    visitor = await _resolve(
        db,
        IdentityClaim(WEB, "sess-9", True),
        IdentityClaim(PHONE, "9830012345", False),
        primary=WEB,
    )
    assert visitor.created and visitor.customer.id != caller.customer.id
    assert visitor.customer.possible_duplicate_of == caller.customer.id
    assert visitor.possible_duplicates == [caller.customer.id]
    assert await _customers(db) == 2
    # The caller's verified identity is untouched; the typed one is stored unverified.
    rows = (
        (
            await db.execute(
                select(CustomerIdentity.customer_id, CustomerIdentity.verified).where(
                    CustomerIdentity.value == "+919830012345"
                )
            )
        )
        .tuples()
        .all()
    )
    assert sorted(rows, key=lambda r: r[1]) == [
        (visitor.customer.id, False),
        (caller.customer.id, True),
    ]


async def test_unverified_email_does_not_merge(db: AsyncSession) -> None:
    a = await _resolve(db, IdentityClaim(EMAIL, "asha@example.com", True), primary=EMAIL)
    b = await _resolve(
        db,
        IdentityClaim(WEB, "sess-2", True),
        IdentityClaim(EMAIL, "asha@example.com", False),
        primary=WEB,
    )
    assert b.customer.id != a.customer.id
    assert b.customer.possible_duplicate_of == a.customer.id


async def test_verified_claim_does_not_merge_into_an_unverified_identity(
    db: AsyncSession,
) -> None:
    # Someone typed this number in chat first; then the real owner calls.
    typed = await _resolve(
        db,
        IdentityClaim(WEB, "sess-3", True),
        IdentityClaim(PHONE, "9830012345", False),
        primary=WEB,
    )
    caller = await _resolve(db, IdentityClaim(PHONE, "9830012345", True), primary=PHONE)
    assert caller.created and caller.customer.id != typed.customer.id
    assert caller.customer.possible_duplicate_of == typed.customer.id


async def test_same_name_different_phone_stays_separate(db: AsyncSession) -> None:
    a = await _resolve(db, IdentityClaim(PHONE, "9830012345", True), primary=PHONE, name="Rahul")
    b = await _resolve(db, IdentityClaim(PHONE, "9830054321", True), primary=PHONE, name="Rahul")
    assert a.customer.id != b.customer.id
    assert b.customer.possible_duplicate_of is None  # names are not identifiers


async def test_conflicting_verified_identifiers_flag_instead_of_merging(db: AsyncSession) -> None:
    by_phone = await _resolve(db, IdentityClaim(PHONE, "9830012345", True), primary=PHONE)
    by_email = await _resolve(db, IdentityClaim(EMAIL, "asha@example.com", True), primary=EMAIL)
    both = await _resolve(
        db,
        IdentityClaim(PHONE, "9830012345", True),
        IdentityClaim(EMAIL, "asha@example.com", True),
        primary=PHONE,
    )
    assert both.customer.id == by_phone.customer.id  # the channel's own identifier wins
    assert both.possible_duplicates == [by_email.customer.id]
    assert both.customer.possible_duplicate_of == by_email.customer.id
    assert any("not merged" in n for n in both.notes)
    assert await _customers(db) == 2
    # The email stays verified on its original owner only.
    owners = (
        (
            await db.execute(
                select(CustomerIdentity.customer_id).where(
                    CustomerIdentity.value == "asha@example.com"
                )
            )
        )
        .scalars()
        .all()
    )
    assert owners == [by_email.customer.id]


async def test_claims_without_usable_identifiers_are_refused(db: AsyncSession) -> None:
    with pytest.raises(ValueError, match="no usable identifier"):
        await resolve(
            db,
            [IdentityClaim(PHONE, "123", True)],
            primary=PHONE,
            default_country_code="91",
            now=NOW,
        )


async def test_database_keeps_verified_identifiers_unique(db: AsyncSession) -> None:
    a = await _resolve(db, IdentityClaim(PHONE, "9830012345", True), primary=PHONE)
    db.add(
        CustomerIdentity(
            customer_id=a.customer.id, identity_type=PHONE, value="+919830012345", verified=True
        )
    )
    with pytest.raises(Exception, match="uq_customer_identities_verified"):
        await db.commit()
