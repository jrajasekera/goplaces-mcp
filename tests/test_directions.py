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


MULTI_LEG_ROUTE = {
    "routes": [
        {
            "description": "Broad St and 5th Ave N",
            "distanceMeters": 3900,
            "duration": "1476s",
            "localizedValues": {
                "distance": {"text": "2.4 mi"},
                "duration": {"text": "25 mins"},
            },
            "legs": [
                {
                    "distanceMeters": 472,
                    "duration": "324s",
                    "localizedValues": {
                        "distance": {"text": "0.3 mi"},
                        "duration": {"text": "5 mins"},
                    },
                },
                {
                    "distanceMeters": 3428,
                    "duration": "1152s",
                    "localizedValues": {
                        "distance": {"text": "2.1 mi"},
                        "duration": {"text": "19 mins"},
                    },
                },
            ],
        }
    ]
}

TRANSIT_ROUTE = {
    "routes": [
        {
            "distanceMeters": 2094,
            "duration": "728s",
            "localizedValues": {
                "distance": {"text": "1.3 mi"},
                "duration": {"text": "12 mins"},
            },
            "legs": [
                {
                    "distanceMeters": 2094,
                    "duration": "728s",
                    "steps": [
                        {
                            "travelMode": "WALK",
                            "navigationInstruction": {"instructions": "Head west"},
                        },
                        {
                            "travelMode": "TRANSIT",
                            "transitDetails": {
                                "headsign": "Westlake",
                                "stopDetails": {
                                    "departureTime": "2026-09-12T16:20:00Z",
                                    "arrivalTime": "2026-09-12T16:23:00Z",
                                },
                            },
                        },
                        {
                            "travelMode": "TRANSIT",
                            "transitDetails": {
                                "headsign": "Rainier Beach",
                                "stopDetails": {
                                    "departureTime": "2026-09-12T16:28:00Z",
                                    "arrivalTime": "2026-09-12T16:35:00Z",
                                },
                            },
                        },
                    ],
                }
            ],
        }
    ]
}


def test_directions_localizes_the_whole_trip_not_the_first_leg(google) -> None:
    """A waypoint route's totals cover every leg, so its text must too."""
    google.reply("/directions/v2:computeRoutes", MULTI_LEG_ROUTE)
    result = call(
        tools.goplaces_directions,
        {"from_text": "A", "to_text": "B", "mode": "drive", "waypoints": ["C"]},
    )
    assert result["distance_meters"] == 3900
    assert result["duration_seconds"] == 1476
    # Not "0.3 mi"/"5 mins", which is only the first leg.
    assert result["distance_text"] == "2.4 mi"
    assert result["duration_text"] == "25 mins"


def test_directions_requests_route_level_localized_values(google) -> None:
    google.reply("/directions/v2:computeRoutes", MULTI_LEG_ROUTE)
    call(tools.goplaces_directions, {"from_text": "A", "to_text": "B"})
    tokens = google.requests_to("/directions/v2:computeRoutes")[0].mask_tokens()
    assert "routes.localizedValues.distance" in tokens
    assert "routes.localizedValues.duration" in tokens


def test_directions_reports_the_real_transit_times(google) -> None:
    """A transit route knows when it leaves; report that, not the request."""
    google.reply("/directions/v2:computeRoutes", TRANSIT_ROUTE)
    result = call(tools.goplaces_directions, {"from_text": "A", "to_text": "B", "mode": "transit"})
    assert result["departure_time"] == "2026-09-12T16:20:00Z"
    assert result["arrival_time"] == "2026-09-12T16:35:00Z"


def test_directions_falls_back_to_the_requested_departure_time(google) -> None:
    google.reply("/directions/v2:computeRoutes", ROUTE)
    result = call(
        tools.goplaces_directions,
        {"from_text": "A", "to_text": "B", "mode": "drive", "departure_time": "2030-05-10T18:57:00-03:00"},
    )
    assert result["departure_time"] == "2030-05-10T18:57:00-03:00"


def test_directions_omits_times_it_does_not_know(google) -> None:
    google.reply("/directions/v2:computeRoutes", ROUTE)
    result = call(tools.goplaces_directions, {"from_text": "A", "to_text": "B", "mode": "drive"})
    assert "departure_time" not in result
    assert "arrival_time" not in result
