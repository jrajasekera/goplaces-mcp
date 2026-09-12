"""Route matrix, reverse geocode, and the directions gaps the review listed."""

from __future__ import annotations

from goplaces_mcp import tools

from .conftest import call

MATRIX_PATH = "/distanceMatrix/v2:computeRouteMatrix"


def test_route_matrix_sorts_by_travel_time(google) -> None:
    """Google streams elements unordered; callers want the fastest first."""
    google.reply(
        MATRIX_PATH,
        [
            {"originIndex": 0, "destinationIndex": 1, "condition": "ROUTE_EXISTS", "duration": "900s", "distanceMeters": 5000},
            {"originIndex": 0, "destinationIndex": 0, "condition": "ROUTE_EXISTS", "duration": "300s", "distanceMeters": 1200},
            {"originIndex": 0, "destinationIndex": 2, "condition": "ROUTE_NOT_FOUND"},
        ],
    )
    result = call(
        tools.goplaces_route_matrix,
        {"origins": ["Home"], "destinations": ["Cafe A", "Cafe B", "Unreachable"]},
    )
    assert [item["duration_seconds"] for item in result["results"][:2]] == [300, 900]
    assert result["results"][0]["destination"] == "Cafe A"
    assert result["results"][1]["destination"] == "Cafe B"
    # The unreachable pair sorts last rather than being dropped.
    assert result["results"][2]["condition"] == "ROUTE_NOT_FOUND"


def test_route_matrix_wraps_waypoints_and_masks_root_tokens(google) -> None:
    google.reply(MATRIX_PATH, [])
    call(
        tools.goplaces_route_matrix,
        {"origins": ["Home", "places/P1"], "destinations": ["Work"], "mode": "walk"},
    )
    request = google.last_request()
    assert request.body["origins"] == [
        {"waypoint": {"address": "Home"}},
        {"waypoint": {"placeId": "P1"}},
    ]
    assert request.body["destinations"] == [{"waypoint": {"address": "Work"}}]
    assert request.body["travelMode"] == "WALK"
    # Matrix elements are the response root, so no "routes." prefix.
    assert not any(token.startswith("routes.") for token in request.mask_tokens())


def test_route_matrix_departure_time_enables_traffic(google) -> None:
    """A drive time "at 8am" is meaningless on a traffic-unaware route."""
    google.reply(MATRIX_PATH, [])
    call(
        tools.goplaces_route_matrix,
        {"origins": ["A"], "destinations": ["B"], "departure_time": "2030-05-10T08:00:00Z"},
    )
    assert google.last_request().body["routingPreference"] == "TRAFFIC_AWARE"


def test_route_matrix_rejects_an_oversized_request(google) -> None:
    error = call(
        tools.goplaces_route_matrix,
        {"origins": [f"o{i}" for i in range(30)], "destinations": [f"d{i}" for i in range(30)]},
    )["error"]
    assert error["field"] == "destinations"
    assert google.requests == []


def test_route_matrix_requires_both_sides(google) -> None:
    assert call(tools.goplaces_route_matrix, {"destinations": ["B"]})["error"]["field"] == "origins"
    assert call(tools.goplaces_route_matrix, {"origins": ["A"]})["error"]["field"] == "destinations"
    assert google.requests == []


def test_route_matrix_surfaces_an_error_object(google) -> None:
    """The streaming endpoint can return an error object instead of an array."""
    google.reply(MATRIX_PATH, {"error": {"code": 400, "message": "bad waypoint"}}, status=200)
    assert call(tools.goplaces_route_matrix, {"origins": ["A"], "destinations": ["B"]})["error"]["type"] == "google_api"


def test_reverse_geocode_ranks_by_distance_from_the_point(google) -> None:
    google.reply(
        "/places:searchNearby",
        {
            "places": [
                {"id": "FAR", "displayName": {"text": "Far"}, "location": {"latitude": 47.6010, "longitude": -122.3300}},
                {"id": "NEAR", "displayName": {"text": "Near"}, "location": {"latitude": 47.6000, "longitude": -122.3300}},
            ]
        },
    )
    result = call(tools.goplaces_reverse_geocode, {"lat": 47.6, "lng": -122.33})
    assert [item["place_id"] for item in result["results"]] == ["NEAR", "FAR"]
    assert result["results"][0]["meters_from_point"] == 0.0
    assert 100 < result["results"][1]["meters_from_point"] < 120
    assert result["query_location"] == {"lat": 47.6, "lng": -122.33}
    body = google.last_request().body
    assert body["rankPreference"] == "DISTANCE"
    assert body["locationRestriction"]["circle"]["radius"] == 50.0


def test_reverse_geocode_refuses_a_neighbourhood_sized_radius(google) -> None:
    error = call(tools.goplaces_reverse_geocode, {"lat": 47.6, "lng": -122.3, "radius_m": 5000})["error"]
    assert error["field"] == "radius_m"
    assert google.requests == []


def test_reverse_geocode_requires_both_coordinates(google) -> None:
    assert call(tools.goplaces_reverse_geocode, {"lat": 47.6})["error"]["field"] == "location"


def test_transit_steps_carry_the_line_and_times(google) -> None:
    """Without these a transit step is unusable: no line, stop, or departure."""
    google.reply(
        "/directions/v2:computeRoutes",
        {
            "routes": [
                {
                    "legs": [
                        {
                            "distanceMeters": 4000,
                            "duration": "1500s",
                            "steps": [
                                {
                                    "travelMode": "TRANSIT",
                                    "navigationInstruction": {"instructions": "Take the train"},
                                    "transitDetails": {
                                        "headsign": "Cais do Sodre",
                                        "headway": "600s",
                                        "stopCount": 3,
                                        "tripShortText": "538",
                                        "stopDetails": {
                                            "departureStop": {"name": "Rossio"},
                                            "arrivalStop": {"name": "Cais do Sodre"},
                                            "departureTime": "2026-09-12T17:41:00Z",
                                            "arrivalTime": "2026-09-12T18:04:00Z",
                                        },
                                        "localizedValues": {
                                            "departureTime": {"time": {"text": "5:41 PM"}},
                                            "arrivalTime": {"time": {"text": "6:04 PM"}},
                                        },
                                        "transitLine": {
                                            "name": "Cascais Line",
                                            "nameShort": "CL",
                                            "agencies": [{"name": "CP"}],
                                            "vehicle": {"type": "HEAVY_RAIL"},
                                        },
                                    },
                                }
                            ],
                        }
                    ]
                }
            ]
        },
    )
    result = call(
        tools.goplaces_directions,
        {"from_text": "A", "to_text": "B", "mode": "transit", "include_steps": True},
    )
    transit = result["steps"][0]["transit"]
    assert transit["line"] == "Cascais Line"
    assert transit["line_short"] == "CL"
    assert transit["agencies"] == ["CP"]
    assert transit["vehicle_type"] == "HEAVY_RAIL"
    assert transit["headsign"] == "Cais do Sodre"
    assert transit["headway_seconds"] == 600
    assert transit["stop_count"] == 3
    assert transit["departure_stop"] == "Rossio"
    assert transit["arrival_stop"] == "Cais do Sodre"
    assert transit["departure_time_text"] == "5:41 PM"
    assert transit["arrival_time"] == "2026-09-12T18:04:00Z"
    assert "routes.legs.steps.transitDetails" in google.last_request().mask_tokens()


def test_non_transit_steps_have_no_transit_object(google) -> None:
    google.reply(
        "/directions/v2:computeRoutes",
        {"routes": [{"legs": [{"steps": [{"travelMode": "WALK", "navigationInstruction": {"instructions": "Walk"}}]}]}]},
    )
    result = call(tools.goplaces_directions, {"from_text": "A", "to_text": "B", "include_steps": True})
    assert "transit" not in result["steps"][0]


def test_transit_preferences_are_forwarded(google) -> None:
    google.reply("/directions/v2:computeRoutes", {"routes": [{"legs": [{"duration": "60s"}]}]})
    call(
        tools.goplaces_directions,
        {
            "from_text": "A",
            "to_text": "B",
            "mode": "transit",
            "transit_modes": ["TRAIN"],
            "transit_routing_preference": "fewer_transfers",
        },
    )
    assert google.last_request().body["transitPreferences"] == {
        "allowedTravelModes": ["TRAIN"],
        "routingPreference": "FEWER_TRANSFERS",
    }


def test_transit_preferences_require_transit_mode(google) -> None:
    error = call(
        tools.goplaces_directions,
        {"from_text": "A", "to_text": "B", "mode": "drive", "transit_modes": ["TRAIN"]},
    )["error"]
    assert error["field"] == "transit_modes"
    assert google.requests == []


def test_drive_departure_time_becomes_traffic_aware(google) -> None:
    """Otherwise Google silently returns static times for a future departure."""
    google.reply("/directions/v2:computeRoutes", {"routes": [{"legs": [{"duration": "60s"}]}]})
    call(
        tools.goplaces_directions,
        {"from_text": "A", "to_text": "B", "mode": "drive", "departure_time": "2030-05-10T08:00:00Z"},
    )
    assert google.last_request().body["routingPreference"] == "TRAFFIC_AWARE"


def test_explicit_routing_preference_wins(google) -> None:
    google.reply("/directions/v2:computeRoutes", {"routes": [{"legs": [{"duration": "60s"}]}]})
    call(
        tools.goplaces_directions,
        {
            "from_text": "A",
            "to_text": "B",
            "mode": "drive",
            "departure_time": "2030-05-10T08:00:00Z",
            "routing_preference": "traffic_aware_optimal",
        },
    )
    assert google.last_request().body["routingPreference"] == "TRAFFIC_AWARE_OPTIMAL"


def test_walking_routes_get_no_routing_preference(google) -> None:
    """Google rejects routingPreference on anything but drive and two-wheeler."""
    google.reply("/directions/v2:computeRoutes", {"routes": [{"legs": [{"duration": "60s"}]}]})
    call(
        tools.goplaces_directions,
        {"from_text": "A", "to_text": "B", "mode": "walk", "departure_time": "2030-05-10T08:00:00Z"},
    )
    assert "routingPreference" not in google.last_request().body


def test_waypoints_become_intermediates_and_legs(google) -> None:
    google.reply(
        "/directions/v2:computeRoutes",
        {
            "routes": [
                {
                    "distanceMeters": 9000,
                    "duration": "1800s",
                    "legs": [
                        {"distanceMeters": 4000, "duration": "800s"},
                        {"distanceMeters": 5000, "duration": "1000s"},
                    ],
                }
            ]
        },
    )
    result = call(
        tools.goplaces_directions,
        {"from_text": "A", "to_text": "C", "waypoints": ["B"], "mode": "drive"},
    )
    assert google.last_request().body["intermediates"] == [{"address": "B"}]
    assert result["distance_meters"] == 9000
    assert result["duration_seconds"] == 1800
    assert [leg["duration_seconds"] for leg in result["legs"]] == [800, 1000]


def test_alternatives_cannot_be_combined_with_waypoints(google) -> None:
    """Google silently ignores the flag; say so instead."""
    error = call(
        tools.goplaces_directions,
        {"from_text": "A", "to_text": "C", "waypoints": ["B"], "alternatives": True},
    )["error"]
    assert error["field"] == "alternatives"
    assert google.requests == []


def test_alternative_routes_are_returned(google) -> None:
    google.reply(
        "/directions/v2:computeRoutes",
        {
            "routes": [
                {"description": "I-5", "duration": "1800s", "legs": [{"duration": "1800s"}]},
                {"description": "US-101", "duration": "2100s", "legs": [{"duration": "2100s"}]},
            ]
        },
    )
    result = call(
        tools.goplaces_directions,
        {"from_text": "A", "to_text": "B", "mode": "drive", "alternatives": True},
    )
    assert google.last_request().body["computeAlternativeRoutes"] is True
    assert result["summary"] == "I-5"
    assert [alt["summary"] for alt in result["alternatives"]] == ["US-101"]


def test_directions_include_a_maps_link(google) -> None:
    google.reply("/directions/v2:computeRoutes", {"routes": [{"legs": [{"duration": "60s"}]}]})
    result = call(tools.goplaces_directions, {"from_text": "Space Needle", "to_text": "Pike Place", "mode": "bicycle"})
    assert result["maps_url"] == (
        "https://www.google.com/maps/dir/?api=1&origin=Space+Needle"
        "&destination=Pike+Place&travelmode=bicycling"
    )


def test_an_address_with_a_comma_stays_one_waypoint(google) -> None:
    """Comma-splitting would turn one address into two bogus waypoints."""
    google.reply(MATRIX_PATH, [])
    call(tools.goplaces_route_matrix, {"origins": "1 Main St, Seattle", "destinations": ["B"]})
    assert google.last_request().body["origins"] == [{"waypoint": {"address": "1 Main St, Seattle"}}]


def test_comma_in_an_intermediate_waypoint_is_preserved(google) -> None:
    google.reply("/directions/v2:computeRoutes", {"routes": [{"legs": [{"duration": "60s"}]}]})
    call(tools.goplaces_directions, {"from_text": "A", "to_text": "C", "waypoints": ["9 Pike St, Seattle"]})
    assert google.last_request().body["intermediates"] == [{"address": "9 Pike St, Seattle"}]


def test_matrix_labels_stay_aligned_with_waypoints(google) -> None:
    """Labels and waypoints must be split identically or index i mislabels."""
    google.reply(
        MATRIX_PATH,
        [{"originIndex": 0, "destinationIndex": 0, "condition": "ROUTE_EXISTS", "duration": "60s"}],
    )
    result = call(tools.goplaces_route_matrix, {"origins": "1 Main St, Seattle", "destinations": ["B"]})
    body = google.last_request().body
    assert body["origins"] == [{"waypoint": {"address": "1 Main St, Seattle"}}]
    # The label must describe the waypoint it is indexed against, not half of it.
    assert result["results"][0]["origin"] == "1 Main St, Seattle"

    google.reset()
    google.reply(
        MATRIX_PATH,
        [{"originIndex": 1, "destinationIndex": 0, "condition": "ROUTE_EXISTS", "duration": "60s"}],
    )
    result = call(
        tools.goplaces_route_matrix,
        {"origins": ["1 Main St, Seattle", "9 Pike St, Seattle"], "destinations": ["B"]},
    )
    assert result["results"][0]["origin"] == "9 Pike St, Seattle"


def test_reverse_geocode_honours_the_opt_in_flags(google) -> None:
    """The schema advertises these, so accepting and ignoring them is a silent lie."""
    google.reply("/places:searchNearby", {"places": []})
    call(
        tools.goplaces_reverse_geocode,
        {"lat": 47.6, "lng": -122.3, "include_atmosphere": True, "include_ev": True},
    )
    mask = google.last_request().mask_tokens()
    assert "places.editorialSummary" in mask
    assert "places.evChargeOptions" in mask


def test_reverse_geocode_stays_cheap_by_default(google) -> None:
    google.reply("/places:searchNearby", {"places": []})
    call(tools.goplaces_reverse_geocode, {"lat": 47.6, "lng": -122.3})
    mask = google.last_request().mask_tokens()
    assert "places.editorialSummary" not in mask
    assert "places.evChargeOptions" not in mask


def test_compare_mode_validates_both_legs_before_spending(google) -> None:
    """routing_preference is valid for drive but not walk; catch it before paying."""
    google.reply("/directions/v2:computeRoutes", {"routes": [{"legs": [{"duration": "60s"}]}]})
    error = call(
        tools.goplaces_directions,
        {
            "from_text": "A",
            "to_text": "B",
            "mode": "drive",
            "compare_mode": "walk",
            "routing_preference": "traffic_aware",
        },
    )["error"]
    assert error["field"] == "routing_preference"
    assert google.requests == [], "must not pay for the primary leg then discard it"


def test_compare_mode_validates_transit_options_before_spending(google) -> None:
    google.reply("/directions/v2:computeRoutes", {"routes": [{"legs": [{"duration": "60s"}]}]})
    error = call(
        tools.goplaces_directions,
        {
            "from_text": "A",
            "to_text": "B",
            "mode": "transit",
            "compare_mode": "drive",
            "transit_modes": ["TRAIN"],
        },
    )["error"]
    assert error["field"] == "transit_modes"
    assert google.requests == []


def test_valid_compare_mode_still_issues_both_requests(google) -> None:
    google.reply("/directions/v2:computeRoutes", {"routes": [{"legs": [{"duration": "60s"}]}]})
    result = call(
        tools.goplaces_directions,
        {"from_text": "A", "to_text": "B", "mode": "drive", "compare_mode": "walk"},
    )
    assert [route["mode"] for route in result["routes"]] == ["driving", "walking"]
    assert len(google.requests_to("/directions/v2:computeRoutes")) == 2
