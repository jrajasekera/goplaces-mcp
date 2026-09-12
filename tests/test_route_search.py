"""Native search-along-route: two requests, detour times, no waypoint sampling."""

from __future__ import annotations

from goplaces_mcp import tools

from .conftest import call

POLYLINE = "_p~iF~ps|U_ulLnnqC_mqNvxq`@"

ROUTE_PAYLOAD = {
    "routes": [
        {
            "distanceMeters": 280_000,
            "duration": "10800s",
            "polyline": {"encodedPolyline": POLYLINE},
        }
    ]
}


def _place(place_id: str, name: str) -> dict:
    return {
        "id": place_id,
        "displayName": {"text": name},
        "formattedAddress": f"{name} Road",
        "location": {"latitude": 46.0, "longitude": -122.8},
    }


def _summary(to_place: int, to_destination: int) -> dict:
    return {
        "legs": [
            {"duration": f"{to_place}s", "distanceMeters": 1000},
            {"duration": f"{to_destination}s", "distanceMeters": 2000},
        ]
    }


def _wire(google, places: list[dict], summaries: list[dict]) -> None:
    google.reply("/directions/v2:computeRoutes", ROUTE_PAYLOAD)
    google.reply("/places:searchText", {"places": places, "routingSummaries": summaries})


def test_route_search_makes_exactly_two_requests(google) -> None:
    """The old implementation issued one search per sampled waypoint."""
    _wire(google, [_place("A", "Cafe A")], [_summary(3600, 7300)])
    result = call(
        tools.goplaces_route_search,
        {"query": "coffee", "from_text": "Seattle", "to_text": "Portland"},
    )
    assert len(google.requests) == 2
    assert len(google.requests_to("/directions/v2:computeRoutes")) == 1
    assert len(google.requests_to("/places:searchText")) == 1
    assert result["results"][0]["place_id"] == "A"


def test_route_search_sends_the_polyline_to_google(google) -> None:
    _wire(google, [], [])
    call(tools.goplaces_route_search, {"query": "coffee", "from_text": "A", "to_text": "B"})
    body = google.requests_to("/places:searchText")[0].body
    assert body["searchAlongRouteParameters"]["polyline"]["encodedPolyline"] == POLYLINE
    # A location bias would only approximate the corridor and let Google return
    # places well outside it.
    assert "locationBias" not in body


def test_routing_summaries_token_has_no_places_prefix(google) -> None:
    """routingSummaries is a response-root field; prefixing it is an API error."""
    _wire(google, [], [])
    call(tools.goplaces_route_search, {"query": "q", "from_text": "A", "to_text": "B"})
    mask = google.requests_to("/places:searchText")[0].mask_tokens()
    assert "routingSummaries" in mask
    assert "places.routingSummaries" not in mask


def test_detour_is_measured_against_the_direct_route(google) -> None:
    # Direct route is 10800s. This stop costs 3600 + 7300 = 10900s, a 100s detour.
    _wire(google, [_place("A", "Cafe A")], [_summary(3600, 7300)])
    result = call(
        tools.goplaces_route_search,
        {"query": "coffee", "from_text": "Seattle", "to_text": "Portland"},
    )
    stop = result["results"][0]
    assert stop["detour_seconds"] == 100
    # Named for the whole journey via this stop, not travel from an origin.
    assert stop["trip_duration_seconds"] == 10900
    assert stop["trip_distance_meters"] == 3000
    assert "duration_seconds" not in stop
    assert result["route"]["duration_seconds"] == 10800
    assert result["route"]["distance_meters"] == 280_000


def test_detour_never_goes_negative(google) -> None:
    """Google's summary legs and the direct route need not agree exactly."""
    _wire(google, [_place("A", "Cafe A")], [_summary(1000, 1000)])
    result = call(tools.goplaces_route_search, {"query": "q", "from_text": "A", "to_text": "B"})
    assert result["results"][0]["detour_seconds"] == 0


def test_summaries_stay_aligned_with_places(google) -> None:
    """Google guarantees index alignment, including empty entries."""
    _wire(
        google,
        [_place("A", "A"), _place("B", "B"), _place("C", "C")],
        [_summary(100, 10700), {}, _summary(200, 10700)],
    )
    results = call(tools.goplaces_route_search, {"query": "q", "from_text": "A", "to_text": "B"})["results"]
    assert [item["place_id"] for item in results] == ["A", "B", "C"]
    assert results[0]["detour_seconds"] == 0
    assert "detour_seconds" not in results[1]
    assert results[2]["detour_seconds"] == 100


def test_missing_summaries_still_return_places(google) -> None:
    _wire(google, [_place("A", "A")], [])
    google.reply("/places:searchText", {"places": [_place("A", "A")]})
    results = call(tools.goplaces_route_search, {"query": "q", "from_text": "A", "to_text": "B"})["results"]
    assert results[0]["place_id"] == "A"
    assert "detour_seconds" not in results[0]


def test_route_search_forwards_ev_filters(google) -> None:
    """The default prompt asks for EV charging stops, so this must be expressible."""
    _wire(google, [], [])
    call(
        tools.goplaces_route_search,
        {
            "query": "EV charging",
            "from_text": "Seattle",
            "to_text": "Portland",
            "ev_connector_types": ["EV_CONNECTOR_TYPE_CCS_COMBO_2"],
            "ev_min_charge_rate_kw": 150,
        },
    )
    request = google.requests_to("/places:searchText")[0]
    assert request.body["evOptions"] == {
        "connectorTypes": ["EV_CONNECTOR_TYPE_CCS_COMBO_2"],
        "minimumChargingRateKw": 150.0,
    }
    # Asking for EV filters implies wanting the charging details back.
    assert "places.evChargeOptions" in request.mask_tokens()


def test_ev_charge_options_are_mapped(google) -> None:
    google.reply("/directions/v2:computeRoutes", ROUTE_PAYLOAD)
    google.reply(
        "/places:searchText",
        {
            "places": [
                {
                    **_place("A", "Supercharger"),
                    "evChargeOptions": {
                        "connectorCount": 8,
                        "connectorAggregation": [
                            {
                                "type": "EV_CONNECTOR_TYPE_NACS",
                                "maxChargeRateKw": 250.0,
                                "count": 8,
                                "availableCount": 5,
                            }
                        ],
                    },
                }
            ],
            "routingSummaries": [],
        },
    )
    options = call(
        tools.goplaces_route_search,
        {"query": "EV charging", "from_text": "A", "to_text": "B", "include_ev": True},
    )["results"][0]["ev_charge_options"]
    assert options["connector_count"] == 8
    assert options["connectors"][0]["type"] == "EV_CONNECTOR_TYPE_NACS"
    assert options["connectors"][0]["max_charge_rate_kw"] == 250.0
    assert options["connectors"][0]["available_count"] == 5


def test_bad_connector_type_is_rejected(google) -> None:
    error = call(
        tools.goplaces_route_search,
        {"query": "ev", "from_text": "A", "to_text": "B", "ev_connector_types": ["ccs"]},
    )["error"]
    assert error["type"] == "validation"
    assert error["field"] == "ev_connector_types"
    # Bad input must not cost a billable route call first.
    assert google.requests == []


def test_route_search_includes_a_directions_link(google) -> None:
    _wire(google, [_place("A", "A")], [_summary(100, 10700)])
    result = call(
        tools.goplaces_route_search,
        {"query": "q", "from_text": "Seattle", "to_text": "Portland"},
    )
    assert result["route"]["maps_url"] == (
        "https://www.google.com/maps/dir/?api=1&origin=Seattle&destination=Portland&travelmode=driving"
    )


def test_empty_polyline_is_an_error(google) -> None:
    google.reply("/directions/v2:computeRoutes", {"routes": [{"polyline": {}}]})
    error = call(tools.goplaces_route_search, {"query": "q", "from_text": "A", "to_text": "B"})["error"]
    assert error["type"] == "google_api"
    assert google.requests_to("/places:searchText") == []
