"""Live call 2026-11-14: the voice model sent bhk as 2, a string for locations and "flat" for
apartment; strict checks refused them and the caller heard "no information". Never again."""

from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from tests.integration.test_native_voice import native, result_of, tool_request, use  # noqa: F401

pytestmark = pytest.mark.usefixtures("inventory")
SAID = [("user", "मुझे 2 BHK फ्लैट चाहिए, न्यू टाउन में")]


@pytest.mark.parametrize("args", [
    {"locations": "New Town", "bhk": 2},
    {"bhk": ["2"], "property_type": "flat"},
    {"location": "New Town", "bhk": "2 BHK", "budget_text": ""},
    {"area": "New Town", "bhk": 2, "property_type": "Flat", "timeline": "3 months"},
    {"bhk": "two", "property_type": "spaceship"},
])  # fmt: skip
async def test_common_argument_shapes_never_fail(
    native: tuple[FastAPI, AsyncClient],  # noqa: F811
    args: dict[str, object],
) -> None:
    _, client = native
    [out] = await use(client, tool_request(("t1", "search_properties", args), said=SAID))
    result = result_of(out)
    assert result.get("status") != "error", result
    assert result["matches"] or result.get("alternatives")


async def test_unknown_area_names_get_our_areas(native: tuple[FastAPI, AsyncClient]) -> None:  # noqa: F811
    _, client = native
    args = {"locations": ["न्यू टाउन"], "bhk": [2]}
    [out] = await use(client, tool_request(("t1", "search_properties", args), said=SAID))
    result = result_of(out)
    assert result["unknown_areas"] == ["न्यू टाउन"]
    assert "New Town Action Area II" in result["our_areas"]
    assert "search_properties again" in result["area_instruction"]
    # A recognised name (even misheard) gets no note.
    [ok] = await use(client, tool_request(("t2", "search_properties", {"locations": ["Newton"]}),
                                          said=SAID))  # fmt: skip
    assert "unknown_areas" not in json.loads(ok["result"])
