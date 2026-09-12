"""Handler behaviour against canned Google responses."""

from __future__ import annotations

import json

from goplaces_mcp import tools

from .conftest import call
from .fake_google import Response

PLACE = {
    "id": "PLACE_A",
    "displayName": {"text": "Blue Bottle"},
    "formattedAddress": "1 Main St, Seattle",
    "location": {"latitude": 47.6, "longitude": -122.3},
    "rating": 4.5,
    "userRatingCount": 120,
    "priceLevel": "PRICE_LEVEL_MODERATE",
    "types": ["cafe", "food"],
    "currentOpeningHours": {"openNow": True},
    "businessStatus": "OPERATIONAL",
    "googleMapsUri": "https://maps.google.com/?cid=1",
}


def test_search_maps_a_place_summary(google) -> None:
    google.reply("/places:searchText", {"places": [PLACE], "nextPageToken": "NEXT"})
    result = call(tools.goplaces_search, {"query": "coffee"})
    summary = result["results"][0]
    assert summary["place_id"] == "PLACE_A"
    assert summary["name"] == "Blue Bottle"
    assert summary["address"] == "1 Main St, Seattle"
    assert summary["location"] == {"lat": 47.6, "lng": -122.3}
    assert summary["rating"] == 4.5
    assert summary["price_level"] == 2
    assert summary["open_now"] is True
    assert summary["types"] == ["cafe", "food"]
    assert result["next_page_token"] == "NEXT"


def test_search_forwards_filters_and_page_token(google) -> None:
    google.reply("/places:searchText", {"places": []})
    call(
        tools.goplaces_search,
        {
            "query": "sushi",
            "keyword": "omakase",
            "limit": 3,
            "page_token": "TOKEN",
            "included_type": "restaurant",
            "open_now": True,
            "min_rating": 4.2,
            "price_levels": [2, 3],
            "lat": 47.6,
            "lng": -122.3,
            "radius_m": 500,
            "language": "en",
            "region": "US",
        },
    )
    body = google.last_request().body
    assert body["textQuery"] == "sushi omakase"
    assert body["pageSize"] == 3
    assert body["pageToken"] == "TOKEN"
    assert body["includedType"] == "restaurant"
    assert body["openNow"] is True
    assert body["minRating"] == 4.2
    assert body["priceLevels"] == ["PRICE_LEVEL_MODERATE", "PRICE_LEVEL_EXPENSIVE"]
    assert body["locationBias"]["circle"]["radius"] == 500
    assert body["languageCode"] == "en"
    assert body["regionCode"] == "US"


def test_nearby_uses_a_location_restriction(google) -> None:
    google.reply("/places:searchNearby", {"places": [PLACE]})
    result = call(
        tools.goplaces_nearby,
        {"lat": 47.6, "lng": -122.3, "radius_m": 800, "included_types": ["cafe"], "excluded_types": ["bar"]},
    )
    assert result["results"][0]["place_id"] == "PLACE_A"
    body = google.last_request().body
    assert body["locationRestriction"]["circle"]["center"] == {"latitude": 47.6, "longitude": -122.3}
    assert body["maxResultCount"] == 10
    assert body["includedTypes"] == ["cafe"]
    assert body["excludedTypes"] == ["bar"]


def test_autocomplete_maps_place_and_query_predictions(google) -> None:
    google.reply(
        "/places:autocomplete",
        {
            "suggestions": [
                {
                    "placePrediction": {
                        "placeId": "P1",
                        "place": "places/P1",
                        "text": {"text": "Space Needle"},
                        "structuredFormat": {
                            "mainText": {"text": "Space Needle"},
                            "secondaryText": {"text": "Seattle, WA"},
                        },
                        "types": ["tourist_attraction"],
                        "distanceMeters": 420,
                    }
                },
                {"queryPrediction": {"text": {"text": "space needle tickets"}}},
                {"unknownPrediction": {}},
            ]
        },
    )
    suggestions = call(tools.goplaces_autocomplete, {"input": "space nee"})["suggestions"]
    assert [item["kind"] for item in suggestions] == ["place", "query"]
    assert suggestions[0]["place_id"] == "P1"
    assert suggestions[0]["secondary_text"] == "Seattle, WA"
    assert suggestions[0]["distance_meters"] == 420
    assert suggestions[1]["text"] == "space needle tickets"


def test_autocomplete_applies_the_limit_after_mapping(google) -> None:
    google.reply(
        "/places:autocomplete",
        {"suggestions": [{"queryPrediction": {"text": {"text": str(i)}}} for i in range(10)]},
    )
    assert len(call(tools.goplaces_autocomplete, {"input": "a", "limit": 3})["suggestions"]) == 3


def test_details_opt_in_fields_change_the_field_mask(google) -> None:
    google.reply("/places/PLACE_A", {**PLACE, "regularOpeningHours": {"weekdayDescriptions": ["Mon: 7AM-5PM"]}})
    base = call(tools.goplaces_details, {"place_id": "PLACE_A"})
    assert base["hours"] == ["Mon: 7AM-5PM"]
    assert base["reviews"] == []
    assert "reviews" not in google.last_request().mask_tokens()

    call(tools.goplaces_details, {"place_id": "PLACE_A", "include_reviews": True, "include_photos": True})
    mask = google.last_request().mask_tokens()
    assert "reviews" in mask and "photos" in mask


def test_details_maps_reviews_and_photo_attribution(google) -> None:
    google.reply(
        "/places/PLACE_A",
        {
            **PLACE,
            "reviews": [
                {
                    "name": "places/PLACE_A/reviews/R1",
                    "rating": 5,
                    "text": {"text": "Great", "languageCode": "en"},
                    "authorAttribution": {"displayName": "Sam", "uri": "https://maps.example/sam"},
                    "publishTime": "2026-01-02T03:04:05Z",
                    "flagContentUri": "https://maps.example/flag",
                    "visitDate": {"year": 2026, "month": 1},
                }
            ],
            "photos": [
                {
                    "name": "places/PLACE_A/photos/PH1",
                    "widthPx": 4032,
                    "heightPx": 3024,
                    "authorAttributions": [{"displayName": "Ada"}],
                }
            ],
        },
    )
    result = call(tools.goplaces_details, {"place_id": "PLACE_A", "include_reviews": True, "include_photos": True})
    review = result["reviews"][0]
    assert review["rating"] == 5
    assert review["text"] == {"text": "Great", "language_code": "en"}
    assert review["author"]["display_name"] == "Sam"
    assert review["flag_content_uri"] == "https://maps.example/flag"
    assert review["visit_date"] == {"year": 2026, "month": 1}
    photo = result["photos"][0]
    assert photo["name"] == "places/PLACE_A/photos/PH1"
    assert photo["author_attributions"][0]["display_name"] == "Ada"


def test_photo_resolves_a_media_url(google) -> None:
    google.reply("/media", {"name": "places/A/photos/B/media", "photoUri": "https://lh3.example/pic"})
    result = call(tools.goplaces_photo, {"name": "places/A/photos/B/media", "max_width_px": 800})
    assert result["photo_uri"] == "https://lh3.example/pic"
    request = google.last_request()
    assert request.path.endswith("/places/A/photos/B/media")
    assert request.query["maxWidthPx"] == ["800"]
    assert request.query["skipHttpRedirect"] == ["true"]


def test_resolve_returns_lean_candidates(google) -> None:
    google.reply("/places:searchText", {"places": [PLACE]})
    result = call(tools.goplaces_resolve, {"location_text": "Riverside Park"})
    candidate = result["results"][0]
    assert candidate["place_id"] == "PLACE_A"
    assert candidate["location"] == {"lat": 47.6, "lng": -122.3}
    # Resolve is a cheap lookup; it must not request rating-tier fields.
    assert "places.rating" not in google.last_request().mask_tokens()


def test_google_error_becomes_a_machine_readable_payload(google) -> None:
    google.reply(
        "/places:searchText",
        {"error": {"code": 400, "message": "Invalid field mask", "status": "INVALID_ARGUMENT"}},
        status=400,
    )
    error = call(tools.goplaces_search, {"query": "coffee"})["error"]
    assert error["type"] == "google_api"
    assert error["status"] == 400
    assert error["message"] == "Invalid field mask"


def test_non_json_error_body_still_produces_an_error(google) -> None:
    google.route("/places:searchText", lambda _r: Response(payload={}, status=500))
    error = call(tools.goplaces_search, {"query": "coffee"})["error"]
    assert error["type"] == "google_api"
    assert error["status"] == 500


def test_throttled_requests_are_retried(google) -> None:
    google.sequence(
        "/places:searchText",
        [
            Response(payload={"error": {"message": "quota"}}, status=429),
            Response(payload={"error": {"message": "unavailable"}}, status=503),
            Response(payload={"places": [PLACE]}),
        ],
    )
    result = call(tools.goplaces_search, {"query": "coffee"})
    assert result["results"][0]["place_id"] == "PLACE_A"
    assert len(google.requests_to("/places:searchText")) == 3


def test_retries_are_bounded(google, monkeypatch) -> None:
    monkeypatch.setenv("GOOGLE_PLACES_MAX_ATTEMPTS", "2")
    google.reply("/places:searchText", {"error": {"message": "quota"}}, status=429)
    error = call(tools.goplaces_search, {"query": "coffee"})["error"]
    assert error["status"] == 429
    assert len(google.requests_to("/places:searchText")) == 2


def test_client_errors_are_not_retried(google) -> None:
    google.reply("/places:searchText", {"error": {"message": "bad key"}}, status=403)
    assert call(tools.goplaces_search, {"query": "coffee"})["error"]["status"] == 403
    assert len(google.requests_to("/places:searchText")) == 1


def test_null_fields_are_stripped_from_the_payload(google) -> None:
    google.reply("/places:searchText", {"places": [{"id": "X", "displayName": {"text": "X"}}]})
    raw = tools.goplaces_search({"query": "x"})
    assert "null" not in raw
    assert json.loads(raw)["results"][0] == {
        "place_id": "X",
        "name": "X",
        "types": [],
    }
