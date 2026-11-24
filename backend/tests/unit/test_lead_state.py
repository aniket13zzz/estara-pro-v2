"""ADR 0007 transition table: every allowed transition, and the forbidden ones."""

from __future__ import annotations

import itertools

import pytest

from app.core.enums import LeadState
from app.leads.state import BOTH, STAFF, SYSTEM, TERMINAL, TRANSITIONS, Actor, allowed

S = LeadState

# ADR 0007 "Allowed transitions", row by row: (from, to, who).
ADR_TABLE: list[tuple[LeadState, LeadState, frozenset[Actor]]] = [
    (S.NEW_ENQUIRY, S.ENGAGED, SYSTEM),
    (S.NEW_ENQUIRY, S.QUALIFIED, SYSTEM),
    (S.NEW_ENQUIRY, S.HANDED_OFF, BOTH),
    (S.NEW_ENQUIRY, S.LOST, STAFF),
    (S.ENGAGED, S.QUALIFIED, SYSTEM),
    (S.ENGAGED, S.NURTURING, BOTH),
    (S.ENGAGED, S.HANDED_OFF, BOTH),
    (S.ENGAGED, S.LOST, STAFF),
    (S.QUALIFIED, S.VISIT_REQUESTED, BOTH),
    (S.QUALIFIED, S.NURTURING, BOTH),
    (S.QUALIFIED, S.HANDED_OFF, BOTH),
    (S.QUALIFIED, S.WON, STAFF),
    (S.QUALIFIED, S.LOST, STAFF),
    (S.VISIT_REQUESTED, S.VISIT_CONFIRMED, STAFF),
    (S.VISIT_REQUESTED, S.NURTURING, BOTH),
    (S.VISIT_REQUESTED, S.HANDED_OFF, BOTH),
    (S.VISIT_REQUESTED, S.WON, STAFF),
    (S.VISIT_REQUESTED, S.LOST, STAFF),
    (S.VISIT_CONFIRMED, S.VISIT_REQUESTED, BOTH),
    (S.VISIT_CONFIRMED, S.NURTURING, BOTH),
    (S.VISIT_CONFIRMED, S.HANDED_OFF, BOTH),
    (S.VISIT_CONFIRMED, S.WON, STAFF),
    (S.VISIT_CONFIRMED, S.LOST, STAFF),
    (S.HANDED_OFF, S.VISIT_REQUESTED, BOTH),
    (S.HANDED_OFF, S.VISIT_CONFIRMED, STAFF),
    (S.HANDED_OFF, S.NURTURING, STAFF),
    (S.HANDED_OFF, S.WON, STAFF),
    (S.HANDED_OFF, S.LOST, STAFF),
    (S.NURTURING, S.ENGAGED, SYSTEM),
    (S.NURTURING, S.QUALIFIED, SYSTEM),
    (S.NURTURING, S.VISIT_REQUESTED, BOTH),
    (S.NURTURING, S.HANDED_OFF, BOTH),
    (S.NURTURING, S.WON, STAFF),
    (S.NURTURING, S.LOST, STAFF),
]


def test_code_table_equals_the_adr() -> None:
    assert {(f, t): who for f, t, who in ADR_TABLE} == TRANSITIONS


@pytest.mark.parametrize(("from_state", "to_state", "who"), ADR_TABLE)
def test_allowed_transitions(
    from_state: LeadState, to_state: LeadState, who: frozenset[Actor]
) -> None:
    for actor in Actor:
        assert allowed(from_state, to_state, actor) is (actor in who)


@pytest.mark.parametrize("target", [S.VISIT_CONFIRMED, S.WON, S.LOST])
def test_system_never_confirms_visits_or_closes_leads(target: LeadState) -> None:
    assert not any(allowed(f, target, Actor.SYSTEM) for f in LeadState)


@pytest.mark.parametrize(
    ("from_state", "to_state"),
    [
        (S.WON, S.ENGAGED),
        (S.LOST, S.QUALIFIED),
        (S.NEW_ENQUIRY, S.VISIT_CONFIRMED),
        (S.ENGAGED, S.VISIT_REQUESTED),
        (S.QUALIFIED, S.ENGAGED),
        (S.VISIT_REQUESTED, S.QUALIFIED),
        (S.HANDED_OFF, S.ENGAGED),
        (S.NURTURING, S.NEW_ENQUIRY),
    ],
)
def test_forbidden_transitions(from_state: LeadState, to_state: LeadState) -> None:
    assert not allowed(from_state, to_state, Actor.SYSTEM)
    assert not allowed(from_state, to_state, Actor.STAFF)


def test_terminal_states_have_no_exits() -> None:
    for terminal, target in itertools.product(TERMINAL, LeadState):
        assert (terminal, target) not in TRANSITIONS
