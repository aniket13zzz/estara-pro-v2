"""Customer identity resolution across phone, WhatsApp, email and web session.

Rules (brief §8, stage 3 prompt, ADR 0010):

- Identifiers are normalised first: phones to E.164 (the default country code is taken from the
  client's own ``escalation.human_transfer_number``), emails to lowercase.
- A claim is *verified* when the channel itself proves it: the caller ID of a call, the sender
  of a WhatsApp message, an email address confirmed by the provider, our own web-session cookie.
  A phone or email typed into a chat is *unverified*.
- Auto-merge happens only on an exact verified match: the same E.164 phone (voice and WhatsApp
  numbers count as the same phone) or the same verified email, against an identity that is
  itself verified.
- Anything weaker (an unverified claim that equals someone else's identifier, or verified
  identifiers that point at two different customers) never merges. The customer is flagged as a
  possible duplicate for staff, and the conflict is reported.
- Names are never identifiers.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import IdentityType
from app.db.models import Customer, CustomerIdentity

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")
PHONE_TYPES: frozenset[IdentityType] = frozenset({IdentityType.PHONE, IdentityType.WHATSAPP})


TWO_DIGIT_CODES: frozenset[str] = frozenset(
    "20 27 30 31 32 33 34 36 39 40 41 43 44 45 46 47 48 49 51 52 53 54 55 56 57 58 60 61 62 63 "
    "64 65 66 81 82 84 86 90 91 92 93 94 95 98".split()
)


def country_code_of(e164: str) -> str:
    """Country calling code of an E.164 number, used only to complete local numbers.

    The template takes it from the client's own ``escalation.human_transfer_number``.
    """
    digits = e164.lstrip("+")
    if digits[:1] in {"1", "7"}:
        return digits[:1]
    if digits[:2] in TWO_DIGIT_CODES:
        return digits[:2]
    return digits[:3]


def normalise_phone(raw: str, default_country_code: str = "91") -> str | None:
    """Return an E.164 number, or None if ``raw`` is not a plausible phone number.

    Indian mobile numbers are accepted in the usual written forms: +91 98300 12345,
    098300 12345, 9830012345, 0091-98300-12345, 91 9830012345.
    """
    text = raw.strip()
    plus = text.startswith("+")
    digits = re.sub(r"\D", "", text)
    if not digits:
        return None
    if plus:
        number = digits
    elif digits.startswith("00"):
        number = digits[2:]
    elif default_country_code == "91" and len(digits) == 11 and digits.startswith("0"):
        number = "91" + digits[1:]
    elif default_country_code == "91" and len(digits) == 12 and digits.startswith("91"):
        number = digits
    elif len(digits) == 10:
        number = default_country_code + digits
    else:
        return None
    if not (8 <= len(number) <= 15) or number.startswith("0"):
        return None
    if number.startswith("91") and not re.fullmatch(r"91[1-9]\d{9}", number):
        return None
    return "+" + number


def normalise_email(raw: str) -> str | None:
    value = raw.strip().lower()
    return value if EMAIL_RE.match(value) else None


@dataclass(frozen=True)
class IdentityClaim:
    """An identifier seen on a channel. ``verified`` means the channel itself proved it."""

    type: IdentityType
    value: str
    verified: bool


@dataclass
class Resolution:
    customer: Customer
    created: bool
    possible_duplicates: list[uuid.UUID] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def normalise_claim(claim: IdentityClaim, default_cc: str) -> IdentityClaim | None:
    if claim.type in PHONE_TYPES:
        value = normalise_phone(claim.value, default_cc)
    elif claim.type == IdentityType.EMAIL:
        value = normalise_email(claim.value)
    else:
        value = claim.value.strip() or None
    return IdentityClaim(claim.type, value, claim.verified) if value else None


def _same_kind(a: IdentityType) -> tuple[IdentityType, ...]:
    """Identity types that name the same real-world identifier (phone == WhatsApp number)."""
    return tuple(PHONE_TYPES) if a in PHONE_TYPES else (a,)


async def _matches(db: AsyncSession, claim: IdentityClaim) -> list[CustomerIdentity]:
    rows = await db.execute(
        select(CustomerIdentity).where(
            CustomerIdentity.identity_type.in_(_same_kind(claim.type)),
            CustomerIdentity.value == claim.value,
        )
    )
    return list(rows.scalars())


async def _owner_of_verified(db: AsyncSession, claim: IdentityClaim) -> uuid.UUID | None:
    rows = await db.execute(
        select(CustomerIdentity.customer_id).where(
            CustomerIdentity.identity_type == claim.type,
            CustomerIdentity.value == claim.value,
            CustomerIdentity.verified.is_(True),
        )
    )
    return rows.scalar_one_or_none()


async def resolve(
    db: AsyncSession,
    claims: list[IdentityClaim],
    *,
    primary: IdentityType,
    default_country_code: str,
    now: datetime,
    name: str | None = None,
) -> Resolution:
    """Find or create the customer for these claims. Flushes; the caller commits.

    ``primary`` is the identifier type of the channel the message arrived on (phone for voice,
    whatsapp for WhatsApp, email for email, web_session for chat). When verified identifiers
    disagree, the customer owning the primary identifier wins and the others are flagged.
    """
    normalised = [c for c in (normalise_claim(c, default_country_code) for c in claims) if c]
    if not normalised:
        raise ValueError("no usable identifier in the claims")

    # 1. Exact verified matches: verified claim == verified identity.
    verified_owner: dict[IdentityClaim, uuid.UUID] = {}
    weak_owners: set[uuid.UUID] = set()
    for claim in normalised:
        for row in await _matches(db, claim):
            if claim.verified and row.verified:
                verified_owner[claim] = row.customer_id
            else:
                weak_owners.add(row.customer_id)

    owners = set(verified_owner.values())
    notes: list[str] = []
    customer: Customer | None = None
    created = False
    if len(owners) == 1:
        customer = await db.get(Customer, owners.pop())
    elif len(owners) > 1:
        primary_owner = next(
            (o for c, o in verified_owner.items() if c.type in _same_kind(primary)), None
        )
        chosen = primary_owner or sorted(owners)[0]
        customer = await db.get(Customer, chosen)
        notes.append("verified identifiers belong to different customers; not merged")
        weak_owners |= owners - {chosen}

    if customer is None:
        customer = Customer(name=name)
        db.add(customer)
        await db.flush()
        created = True
    elif name and not customer.name:
        customer.name = name

    # 2. Record new identifiers on this customer.
    existing = {
        (i.identity_type, i.value, i.verified)
        for i in (
            await db.execute(
                select(CustomerIdentity).where(CustomerIdentity.customer_id == customer.id)
            )
        ).scalars()
    }
    for claim in normalised:
        if (claim.type, claim.value, claim.verified) in existing:
            continue
        if claim.verified:
            owner = await _owner_of_verified(db, claim)
            if owner is not None and owner != customer.id:
                notes.append(f"{claim.type.value} is verified on another customer; flagged")
                weak_owners.add(owner)
                continue
            if owner == customer.id:
                continue
        elif (claim.type, claim.value, True) in existing:
            continue  # already known as verified on this customer
        db.add(
            CustomerIdentity(
                customer_id=customer.id,
                identity_type=claim.type,
                value=claim.value,
                verified=claim.verified,
                verified_at=now if claim.verified else None,
            )
        )
        existing.add((claim.type, claim.value, claim.verified))

    # 3. Flag uncertain matches for staff. Never merge.
    weak_owners.discard(customer.id)
    if weak_owners:
        if customer.possible_duplicate_of is None:
            customer.possible_duplicate_of = sorted(weak_owners)[0]
            customer.duplicate_flagged_at = now
        notes.append("possible duplicate flagged for staff review")
    await db.flush()
    return Resolution(customer, created, sorted(weak_owners), notes)
