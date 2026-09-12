"""Directions handling and the polyline helpers route search depends on."""

from __future__ import annotations

import pytest

from goplaces_mcp import tools

from .conftest import call

ROUTE = {
    "routes": [
        {
            "description": "I-5 S",
            "warnings": ["This route has tolls."],
            "legs": [
                {
                    "distanceMeters": 4800,
                    "duration": "1200s",
                    "localizedValues": {
                        "distance": {"text": "3.0 mi"},
                        "duration": {"text": "20 mins"},
                    },
                    "steps": [
                        {
                            "distanceMeters": 200,
                            "staticDuration": "60s",
                            "travelMode": "WALK",
                            "localizedValues": {
                                "distance": {"text": "0.1 mi"},
                                "staticDuration": {"text": "1 min"},
                            },
                            "navigationInstruction": {"instructions": "Head north", "maneuver": "DEPART"},
                        }
                    ],
                }
            ],
        }
    ]
}


def test_directions_maps_a_route(google) -> None:
    google.reply("/directions/v2:computeRoutes", ROUTE)
    result = call(tools.goplaces_directions, {"from_text": "A", "to_text": "B", "mode": "drive"})
    assert result["mode"] == "driving"
    assert result["summary"] == "I-5 S"
    assert result["distance_meters"] == 4800
    assert result["distance_text"] == "3.0 mi"
    assert result["duration_seconds"] == 1200
    assert result["duration_text"] == "20 mins"
    assert result["warnings"] == ["This route has tolls."]
    assert "steps" not in result


def test_directions_include_steps_is_opt_in(google) -> None:
    google.reply("/directions/v2:computeRoutes", ROUTE)
    result = call(tools.goplaces_directions, {"from_text": "A", "to_text": "B", "include_steps": True})
    step = result["steps"][0]
    assert step["instruction"] == "Head north"
    assert step["maneuver"] == "DEPART"
    assert step["distance_meters"] == 200
    assert step["duration_seconds"] == 60


def test_directions_compare_mode_issues_two_requests(google) -> None:
    google.reply("/directions/v2:computeRoutes", ROUTE)
    result = call(
        tools.goplaces_directions,
        {"from_text": "A", "to_text": "B", "mode": "walk", "compare_mode": "drive"},
    )
    assert [route["mode"] for route in result["routes"]] == ["walking", "driving"]
    bodies = [request.body for request in google.requests_to("/directions/v2:computeRoutes")]
    assert [body["travelMode"] for body in bodies] == ["WALK", "DRIVE"]


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        ({"from_text": "Pike Place"}, {"address": "Pike Place"}),
        ({"from_place_id": "places/P1"}, {"placeId": "P1"}),
        ({"from_lat": 47.6, "from_lng": -122.3}, {"location": {"latLng": {"latitude": 47.6, "longitude": -122.3}}}),
    ],
)
def test_origin_accepts_each_waypoint_form(google, args, expected) -> None:
    google.reply("/directions/v2:computeRoutes", ROUTE)
    call(tools.goplaces_directions, {**args, "to_text": "B"})
    assert google.last_request().body["origin"] == expected


def test_imperial_units_are_forwarded(google) -> None:
    google.reply("/directions/v2:computeRoutes", ROUTE)
    call(tools.goplaces_directions, {"from_text": "A", "to_text": "B", "units": "imperial"})
    assert google.last_request().body["units"] == "IMPERIAL"


def test_drive_avoid_modifiers_are_forwarded(google) -> None:
    google.reply("/directions/v2:computeRoutes", ROUTE)
    call(
        tools.goplaces_directions,
        {"from_text": "A", "to_text": "B", "mode": "drive", "avoid_tolls": True, "avoid_ferries": True},
    )
    modifiers = google.last_request().body["routeModifiers"]
    assert modifiers["avoidTolls"] is True
    assert modifiers["avoidFerries"] is True
    assert modifiers["avoidHighways"] is False


def test_empty_route_list_is_an_error(google) -> None:
    google.reply("/directions/v2:computeRoutes", {"routes": []})
    assert call(tools.goplaces_directions, {"from_text": "A", "to_text": "B"})["error"]["type"] == "google_api"


def test_route_without_legs_is_an_error(google) -> None:
    google.reply("/directions/v2:computeRoutes", {"routes": [{"description": "x"}]})
    assert call(tools.goplaces_directions, {"from_text": "A", "to_text": "B"})["error"]["type"] == "google_api"


# The canonical example from Google's polyline algorithm documentation.
def test_polyline_decoding_matches_the_reference_vector() -> None:
    points = tools._decode_polyline("_p~iF~ps|U_ulLnnqC_mqNvxq`@")
    assert points == [
        {"lat": 38.5, "lng": -120.2},
        {"lat": 40.7, "lng": -120.95},
        {"lat": 43.252, "lng": -126.453},
    ]


def test_truncated_polyline_is_rejected() -> None:
    with pytest.raises(tools.GoogleAPIError):
        tools._decode_polyline("_p~iF~ps|U_ulL")
