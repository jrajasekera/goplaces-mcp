"""Field tiers, Maps links, and routing summaries on search and nearby."""

from __future__ import annotations

import pytest

from goplaces_mcp import tools

from .conftest import call

# Google's Place Data Fields table; billing is at the highest tier requested.
ENTERPRISE_FIELDS = {
    "places.rating",
    "places.userRatingCount",
    "places.priceLevel",
    "places.currentOpeningHours",
    "places.websiteUri",
    "places.nationalPhoneNumber",
}
ATMOSPHERE_FIELDS = {"places.editorialSummary", "places.dineIn", "places.parkingOptions"}


@pytest.mark.parametrize("handler", [tools.goplaces_search, tools.goplaces_nearby])
def test_ids_level_requests_only_identifiers(google, handler) -> None:
    google.reply("/places:search", {"places": []})
    args = {"query": "coffee", "lat": 47.6, "lng": -122.3, "radius_m": 500, "detail_level": "ids"}
    call(handler, args)
    assert google.last_request().mask_tokens() <= {"places.id", "nextPageToken"}


@pytest.mark.parametrize("handler", [tools.goplaces_search, tools.goplaces_nearby])
def test_basic_level_stays_off_the_enterprise_tier(google, handler) -> None:
    google.reply("/places:search", {"places": []})
    args = {"query": "coffee", "lat": 47.6, "lng": -122.3, "radius_m": 500, "detail_level": "basic"}
    call(handler, args)
    mask = google.last_request().mask_tokens()
    assert "places.displayName" in mask
    assert "places.googleMapsUri" in mask
    assert not (mask & ENTERPRISE_FIELDS)
    assert not (mask & ATMOSPHERE_FIELDS)


def test_full_is_the_default_so_responses_do_not_regress(google) -> None:
    google.reply("/places:searchText", {"places": []})
    call(tools.goplaces_search, {"query": "coffee"})
    mask = google.last_request().mask_tokens()
    assert ENTERPRISE_FIELDS <= mask
    # The most expensive tier still has to be asked for.
    assert not (mask & ATMOSPHERE_FIELDS)


def test_atmosphere_is_opt_in(google) -> None:
    google.reply("/places:searchText", {"places": []})
    call(tools.goplaces_search, {"query": "coffee", "include_atmosphere": True})
    assert ATMOSPHERE_FIELDS <= google.last_request().mask_tokens()


def test_unknown_detail_level_is_rejected(google) -> None:
    error = call(tools.goplaces_search, {"query": "a", "detail_level": "everything"})["error"]
    assert error["field"] == "detail_level"
    assert google.requests == []


def test_details_mask_has_no_places_prefix(google) -> None:
    """Place Details names the same fields without the collection prefix."""
    google.reply("/places/A", {"id": "A", "displayName": {"text": "A"}})
    call(tools.goplaces_details, {"place_id": "A"})
    mask = google.last_request().mask_tokens()
    assert "rating" in mask
    assert not any(token.startswith("places.") for token in mask)


def test_atmosphere_fields_are_mapped(google) -> None:
    google.reply(
        "/places/A",
        {
            "id": "A",
            "displayName": {"text": "A"},
            "editorialSummary": {"text": "Cosy corner cafe."},
            "dineIn": True,
            "takeout": False,
            "reservable": True,
            "accessibilityOptions": {"wheelchairAccessibleEntrance": True, "wheelchairAccessibleParking": False},
            "parkingOptions": {"freeParkingLot": True},
            "primaryTypeDisplayName": {"text": "Coffee shop"},
        },
    )
    result = call(tools.goplaces_details, {"place_id": "A", "include_atmosphere": True})
    assert result["editorial_summary"] == "Cosy corner cafe."
    assert result["dine_in"] is True
    assert result["takeout"] is False
    assert result["reservable"] is True
    assert result["accessibility"] == ["wheelchairAccessibleEntrance"]
    assert result["parking"] == ["freeParkingLot"]
    assert result["primary_type"] == "Coffee shop"


def test_googles_own_maps_uri_wins_over_a_built_link(google) -> None:
    google.reply(
        "/places:searchText",
        {"places": [{"id": "A", "displayName": {"text": "A"}, "googleMapsUri": "https://maps.app.goo.gl/abc"}]},
    )
    result = call(tools.goplaces_search, {"query": "a"})
    assert result["results"][0]["maps_url"] == "https://maps.app.goo.gl/abc"


def test_maps_link_prefers_coordinates_when_google_omits_the_uri(google) -> None:
    google.reply(
        "/places:searchText",
        {"places": [{"id": "A", "displayName": {"text": "Cafe A"}, "location": {"latitude": 47.6, "longitude": -122.3}}]},
    )
    url = call(tools.goplaces_search, {"query": "a"})["results"][0]["maps_url"]
    assert url == "https://www.google.com/maps/search/?api=1&query=47.6%2C-122.3&query_place_id=A"


@pytest.mark.parametrize(
    ("handler", "args", "path"),
    [
        (tools.goplaces_search, {"query": "coffee"}, "/places:searchText"),
        (tools.goplaces_nearby, {"lat": 47.6, "lng": -122.3, "radius_m": 500}, "/places:searchNearby"),
    ],
)
def test_routing_origin_returns_distance_per_result(google, handler, args, path) -> None:
    google.reply(
        path,
        {
            "places": [{"id": "A", "displayName": {"text": "A"}}, {"id": "B", "displayName": {"text": "B"}}],
            # A routing origin yields one leg per summary, unlike search-along-route.
            "routingSummaries": [
                {"legs": [{"duration": "600s", "distanceMeters": 2400}]},
                {"legs": [{"duration": "180s", "distanceMeters": 700}]},
            ],
        },
    )
    result = call(handler, {**args, "origin_lat": 47.61, "origin_lng": -122.33})
    assert result["results"][0]["duration_seconds"] == 600
    assert result["results"][0]["distance_meters"] == 2400
    assert result["results"][1]["duration_seconds"] == 180
    request = google.last_request()
    # A bare LatLng, not the wrapped waypoint the Routes API takes.
    assert request.body["routingParameters"]["origin"] == {"latitude": 47.61, "longitude": -122.33}
    assert request.body["routingParameters"]["travelMode"] == "DRIVE"
    assert "routingSummaries" in request.mask_tokens()


def test_routing_summaries_are_not_requested_without_an_origin(google) -> None:
    google.reply("/places:searchText", {"places": []})
    call(tools.goplaces_search, {"query": "coffee"})
    # Asking for them without an origin is an API error.
    assert "routingSummaries" not in google.last_request().mask_tokens()


def test_half_an_origin_is_rejected(google) -> None:
    error = call(tools.goplaces_search, {"query": "a", "origin_lat": 47.6})["error"]
    assert error["field"] == "origin"
    assert google.requests == []


def test_transit_origin_is_rejected(google) -> None:
    """Places routing does not support transit; Google would reject the request."""
    error = call(
        tools.goplaces_search,
        {"query": "a", "origin_lat": 47.6, "origin_lng": -122.3, "origin_mode": "transit"},
    )["error"]
    assert error["field"] == "origin_mode"
    assert google.requests == []


def test_nearby_can_rank_by_distance(google) -> None:
    google.reply("/places:searchNearby", {"places": []})
    call(tools.goplaces_nearby, {"lat": 47.6, "lng": -122.3, "radius_m": 500, "rank_by": "distance"})
    assert google.last_request().body["rankPreference"] == "DISTANCE"
