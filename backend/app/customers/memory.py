"""Customer memory: what the agent should remember between conversations and channels.

Stored as JSON on ``customers.memory`` (plus ``preferred_language`` and ``last_contact_at``
columns). Requirements themselves live on the lead (``lead_requirements``); memory keeps a copy
of the latest known values so a returning customer can be greeted with context.

Keys: ``requirements``, ``preferences``, ``objections`` (types raised, oldest first),
``properties_discussed`` (project and unit type codes, most recent last, at most 10).
"""

from __future__ import annotations

from typing import Any

from app.db.models import Customer

MAX_PROPERTIES = 10


def _memory(customer: Customer) -> dict[str, Any]:
    return dict(customer.memory or {})


def remember_requirements(customer: Customer, requirements: dict[str, Any]) -> None:
    memory = _memory(customer)
    known = {k: v for k, v in requirements.items() if v not in (None, [], "")}
    memory["requirements"] = {**memory.get("requirements", {}), **known}
    customer.memory = memory


def remember_preference(customer: Customer, key: str, value: Any) -> None:
    memory = _memory(customer)
    memory["preferences"] = {**memory.get("preferences", {}), key: value}
    customer.memory = memory


def remember_objection(customer: Customer, objection_type: str) -> None:
    memory = _memory(customer)
    objections = list(memory.get("objections", []))
    if objection_type not in objections:
        objections.append(objection_type)
    memory["objections"] = objections
    customer.memory = memory


def remember_properties(customer: Customer, options: list[dict[str, str]]) -> None:
    memory = _memory(customer)
    seen = [p for p in memory.get("properties_discussed", []) if p not in options]
    memory["properties_discussed"] = (seen + options)[-MAX_PROPERTIES:]
    customer.memory = memory


def summary(customer: Customer) -> dict[str, Any]:
    memory = _memory(customer)
    return {
        "name": customer.name,
        "preferred_language": customer.preferred_language,
        "last_contact_at": customer.last_contact_at.isoformat()
        if customer.last_contact_at
        else None,
        "requirements": memory.get("requirements", {}),
        "preferences": memory.get("preferences", {}),
        "objections": memory.get("objections", []),
        "properties_discussed": memory.get("properties_discussed", []),
    }
