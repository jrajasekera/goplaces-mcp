"""Input validation, exercised through the public handlers.

Every handler converts exceptions into a machine-readable error payload, so a
validation failure is observable as ``error.type == "validation"`` plus the
offending field name.
"""

from __future__ import annotations

import pytest

from goplaces_mcp import tools

from .conftest import call


def assert_validation(result: dict, field: str) -> None:
    assert result["error"]["type"] == "validation", result
    assert result["error"]["field"] == field, result


@pytest.mark.parametrize(
    ("handler", "args", "field"),
    [
        (tools.goplaces_search, {}, "query"),
        (tools.goplaces_search, {"query": "   "}, "query"),
        (tools.goplaces_search, {"query": "a", "limit": 0}, "limit"),
        (tools.goplaces_search, {"query": "a", "limit": 21}, "limit"),
        (tools.goplaces_search, {"query": "a", "limit": "many"}, "limit"),
        (tools.goplaces_search, {"query": "a", "min_rating": 6}, "min_rating"),
        (tools.goplaces_search, {"query": "a", "price_levels": [9]}, "price_levels"),
        (tools.goplaces_search, {"query": "a", "price_levels": ["cheap"]}, "price_levels"),
        (tools.goplaces_search, {"query": "a", "lat": 47.6}, "location_bias"),
        (tools.goplaces_search, {"query": "a", "lat": 91, "lng": 0, "radius_m": 10}, "location_bias.lat"),
        (tools.goplaces_search, {"query": "a", "lat": 0, "lng": 181, "radius_m": 10}, "location_bias.lng"),
        (tools.goplaces_search, {"query": "a", "lat": 0, "lng": 0, "radius_m": 0}, "location_bias.radius_m"),
        (tools.goplaces_search, {"query": "a", "lat": 0, "lng": 0, "radius_m": 50001}, "location_bias.radius_m"),
        (tools.goplaces_nearby, {"lat": 1.0}, "location_restriction"),
        (tools.goplaces_autocomplete, {}, "input"),
        (tools.goplaces_details, {}, "place_id"),
        (tools.goplaces_details, {"place_id": "a/b"}, "place_id"),
        (tools.goplaces_photo, {}, "name"),
        (tools.goplaces_photo, {"name": "places/A/photos"}, "name"),
        (tools.goplaces_photo, {"name": "places/A/photos/B"}, "max_width_px"),
        (tools.goplaces_photo, {"name": "places/A/photos/B", "max_width_px": 9999}, "max_width_px"),
        (tools.goplaces_resolve, {}, "location_text"),
        (tools.goplaces_resolve, {"location_text": "x", "limit": 11}, "limit"),
        (tools.goplaces_directions, {"to_text": "b"}, "from"),
        (tools.goplaces_directions, {"from_text": "a"}, "to"),
        (tools.goplaces_directions, {"from_text": "a", "from_lat": 1, "from_lng": 2, "to_text": "b"}, "from"),
        (tools.goplaces_directions, {"from_text": "a", "to_text": "b", "mode": "teleport"}, "mode"),
        (tools.goplaces_directions, {"from_text": "a", "to_text": "b", "units": "furlongs"}, "units"),
        (tools.goplaces_directions, {"from_text": "a", "to_text": "b", "mode": "walk", "compare_mode": "walk"}, "compare_mode"),
        (tools.goplaces_directions, {"from_text": "a", "to_text": "b", "departure_time": "tomorrow"}, "departure_time"),
        (tools.goplaces_directions, {"from_text": "a", "to_text": "b", "mode": "drive", "arrival_time": "2030-05-10T18:57:00Z"}, "arrival_time"),
        (tools.goplaces_directions, {"from_text": "a", "to_text": "b", "departure_time": "2030-05-10T18:57:00Z", "arrival_time": "2030-05-10T19:57:00Z"}, "time"),
        (tools.goplaces_directions, {"from_text": "a", "to_text": "b", "mode": "walk", "avoid_tolls": True}, "route_modifiers"),
        (tools.goplaces_route_search, {"from_text": "a", "to_text": "b"}, "query"),
        (tools.goplaces_route_search, {"query": "q", "to_text": "b"}, "from_text"),
        (tools.goplaces_route_search, {"query": "q", "from_text": "a"}, "to_text"),
        (tools.goplaces_route_search, {"query": "q", "from_text": "a", "to_text": "b", "radius_m": 0}, "radius_m"),
        (tools.goplaces_route_search, {"query": "q", "from_text": "a", "to_text": "b", "mode": "hover"}, "mode"),
    ],
)
def test_rejects_bad_input_before_calling_google(google, handler, args, field) -> None:
    assert_validation(call(handler, args), field)
    assert google.requests == [], "validation must fail before any network call"


def test_missing_api_key_is_a_validation_error(monkeypatch) -> None:
    monkeypatch.delenv("GOOGLE_PLACES_API_KEY", raising=False)
    assert_validation(call(tools.goplaces_search, {"query": "coffee"}), "GOOGLE_PLACES_API_KEY")


def test_place_id_accepts_the_places_prefix(google) -> None:
    google.reply("/places/ABC", {"id": "ABC", "displayName": {"text": "Somewhere"}})
    assert call(tools.goplaces_details, {"place_id": "places/ABC"})["place_id"] == "ABC"
    assert google.last_request().path.endswith("/places/ABC")
