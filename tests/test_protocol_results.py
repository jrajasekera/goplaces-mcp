"""What a host actually receives: isError, structuredContent, image blocks."""

from __future__ import annotations

import base64
import json

from mcp import types

from goplaces_mcp import server


def _result(payload: dict) -> types.CallToolResult:
    return server._tool_result(payload, json.dumps(payload, separators=(",", ":")))


def test_success_carries_structured_content_and_is_not_an_error() -> None:
    result = _result({"results": [{"place_id": "A"}]})
    assert result.isError is False
    assert result.structuredContent == {"results": [{"place_id": "A"}]}
    assert json.loads(result.content[0].text)["results"][0]["place_id"] == "A"


def test_error_payload_is_flagged_on_the_wire() -> None:
    result = _result({"error": {"type": "google_api", "status": 503, "message": "unavailable"}})
    assert result.isError is True
    assert result.structuredContent["error"]["status"] == 503


def test_image_bytes_become_an_image_block() -> None:
    data = base64.b64encode(b"jpeg-bytes").decode("ascii")
    result = _result({"photo_uri": "https://lh3.example/pic", "image_base64": data, "image_mime_type": "image/png"})
    image, text = result.content
    assert isinstance(image, types.ImageContent)
    assert image.data == data
    assert image.mimeType == "image/png"
    # The bytes must not be repeated in the text block or the structured payload.
    assert "image_base64" not in text.text
    assert "image_base64" not in result.structuredContent
    assert result.structuredContent["photo_uri"] == "https://lh3.example/pic"


def test_results_without_image_bytes_have_a_single_text_block() -> None:
    result = _result({"photo_uri": "https://lh3.example/pic"})
    assert len(result.content) == 1
    assert isinstance(result.content[0], types.TextContent)


def test_unknown_tool_is_an_error_result() -> None:
    result = server._error_result({"error": {"type": "unknown_tool", "message": "Unknown tool: nope"}})
    assert result.isError is True
    assert json.loads(result.content[0].text)["error"]["type"] == "unknown_tool"


def test_annotations_declare_a_read_only_open_world_tool() -> None:
    annotations = server._tool_annotations({"title": "Search places"})
    assert annotations.readOnlyHint is True
    assert annotations.openWorldHint is True
    assert annotations.destructiveHint is False
    assert annotations.title == "Search places"


def test_declared_output_schemas_accept_real_handler_payloads(google) -> None:
    """A published outputSchema that rejects our own output would break hosts."""
    import jsonschema

    from goplaces_mcp import tools

    from .conftest import call

    place = {
        "id": "A",
        "displayName": {"text": "Cafe"},
        "formattedAddress": "1 Main St",
        "location": {"latitude": 47.6, "longitude": -122.3},
        "rating": 4.5,
        "userRatingCount": 10,
        "priceLevel": "PRICE_LEVEL_MODERATE",
        "types": ["cafe"],
        "currentOpeningHours": {"openNow": True},
        "businessStatus": "OPERATIONAL",
        "utcOffsetMinutes": -480,
        "regularOpeningHours": {"weekdayDescriptions": ["Mon: 7AM-5PM"]},
    }
    google.reply("/places:searchText", {"places": [place], "nextPageToken": "N"})
    google.reply("/places:searchNearby", {"places": [place]})
    google.reply("/places:autocomplete", {"suggestions": [{"queryPrediction": {"text": {"text": "a"}}}]})
    google.reply("/places/A", place)
    google.reply("/media", {"name": "n", "photoUri": "https://lh3.example/p"})
    google.reply(
        "/directions/v2:computeRoutes",
        {"routes": [{"description": "I-5", "duration": "600s", "distanceMeters": 900, "polyline": {"encodedPolyline": "_p~iF~ps|U"}, "legs": [{"duration": "600s", "distanceMeters": 900}]}]},
    )
    google.reply(
        "/distanceMatrix/v2:computeRouteMatrix",
        [{"originIndex": 0, "destinationIndex": 0, "condition": "ROUTE_EXISTS", "duration": "60s", "distanceMeters": 100}],
    )

    cases = {
        "goplaces_search": (tools.goplaces_search, {"query": "coffee"}),
        "goplaces_nearby": (tools.goplaces_nearby, {"lat": 47.6, "lng": -122.3, "radius_m": 500}),
        "goplaces_autocomplete": (tools.goplaces_autocomplete, {"input": "a"}),
        "goplaces_details": (tools.goplaces_details, {"place_id": "A", "include_reviews": True}),
        "goplaces_photo": (tools.goplaces_photo, {"name": "places/A/photos/B", "max_width_px": 100}),
        "goplaces_resolve": (tools.goplaces_resolve, {"location_text": "x"}),
        "goplaces_directions": (tools.goplaces_directions, {"from_text": "A", "to_text": "B"}),
        "goplaces_route_search": (tools.goplaces_route_search, {"query": "q", "from_text": "A", "to_text": "B"}),
        "goplaces_route_matrix": (tools.goplaces_route_matrix, {"origins": ["A"], "destinations": ["B"]}),
        "goplaces_reverse_geocode": (tools.goplaces_reverse_geocode, {"lat": 47.6, "lng": -122.3}),
    }
    schemas_by_name = {d["name"]: d["output_schema"] for d in server._TOOL_DEFINITIONS}
    assert set(cases) == set(schemas_by_name)

    for name, (handler, args) in cases.items():
        payload = call(handler, args)
        assert "error" not in payload, f"{name} returned {payload}"
        jsonschema.validate(instance=payload, schema=schemas_by_name[name])
