"""Tool handlers for the goplaces MCP plugin.

This is a dependency-free Python port of the useful goplaces CLI workflows:
text search, nearby search, autocomplete, details, photo media, location
resolution, directions, and search-along-route.
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Iterable

_DEFAULT_PLACES_BASE_URL = "https://places.googleapis.com/v1"
_DEFAULT_ROUTES_BASE_URL = "https://routes.googleapis.com"
_DEFAULT_TIMEOUT_SECONDS = 10.0
_MAX_CIRCLE_RADIUS_M = 50_000.0
_MAX_RESULTS = 20
_MAX_RESOLVE_RESULTS = 10
_MAX_ROUTE_WAYPOINTS = 20
# Google returns these when it is throttling or briefly unavailable; both are
# worth one bounded retry. Every other status is a real failure.
_RETRY_STATUSES = frozenset({429, 503})
_DEFAULT_MAX_ATTEMPTS = 3
_DEFAULT_RETRY_BASE_DELAY_SECONDS = 0.5

_SEARCH_FIELD_MASK = (
    "places.id,places.displayName,places.formattedAddress,places.location,"
    "places.rating,places.userRatingCount,places.priceLevel,places.types,"
    "places.currentOpeningHours,places.businessStatus,nextPageToken"
)
_NEARBY_FIELD_MASK = (
    "places.id,places.displayName,places.formattedAddress,places.location,"
    "places.rating,places.userRatingCount,places.priceLevel,places.types,"
    "places.currentOpeningHours,places.businessStatus"
)
_AUTOCOMPLETE_FIELD_MASK = (
    "suggestions.placePrediction.placeId,suggestions.placePrediction.place,"
    "suggestions.placePrediction.text,suggestions.placePrediction.structuredFormat,"
    "suggestions.placePrediction.types,suggestions.placePrediction.distanceMeters,"
    "suggestions.queryPrediction.text,suggestions.queryPrediction.structuredFormat"
)
_DETAILS_FIELD_MASK_BASE = (
    "id,displayName,formattedAddress,location,rating,userRatingCount,priceLevel,"
    "types,regularOpeningHours,currentOpeningHours,businessStatus,"
    "nationalPhoneNumber,websiteUri"
)
_RESOLVE_FIELD_MASK = "places.id,places.displayName,places.formattedAddress,places.location,places.types"
_ROUTE_POLYLINE_FIELD_MASK = "routes.polyline.encodedPolyline"
_DIRECTIONS_FIELD_MASK = (
    "routes.description,routes.warnings,routes.legs.distanceMeters,"
    "routes.legs.duration,routes.legs.localizedValues.distance,"
    "routes.legs.localizedValues.duration,routes.legs.steps.distanceMeters,"
    "routes.legs.steps.staticDuration,routes.legs.steps.localizedValues.distance,"
    "routes.legs.steps.localizedValues.staticDuration,"
    "routes.legs.steps.navigationInstruction.instructions,"
    "routes.legs.steps.navigationInstruction.maneuver,routes.legs.steps.travelMode"
)

_PRICE_TO_ENUM = {
    0: "PRICE_LEVEL_FREE",
    1: "PRICE_LEVEL_INEXPENSIVE",
    2: "PRICE_LEVEL_MODERATE",
    3: "PRICE_LEVEL_EXPENSIVE",
    4: "PRICE_LEVEL_VERY_EXPENSIVE",
}
_ENUM_TO_PRICE = {v: k for k, v in _PRICE_TO_ENUM.items()}
# Google can also return this older/unspecified spelling.
_ENUM_TO_PRICE["PRICE_LEVEL_UNSPECIFIED"] = None

_DIRECTION_MODE_TO_API = {
    "walk": "WALK",
    "walking": "WALK",
    "drive": "DRIVE",
    "driving": "DRIVE",
    "bicycle": "BICYCLE",
    "bike": "BICYCLE",
    "bicycling": "BICYCLE",
    "transit": "TRANSIT",
}
_ROUTE_MODE_TO_API = {
    **_DIRECTION_MODE_TO_API,
    "two_wheeler": "TWO_WHEELER",
    "two-wheeler": "TWO_WHEELER",
    "two wheeler": "TWO_WHEELER",
}
_API_TO_DIRECTION_MODE = {
    "WALK": "walking",
    "DRIVE": "driving",
    "BICYCLE": "bicycling",
    "TRANSIT": "transit",
    "TWO_WHEELER": "two_wheeler",
}

_client_lock = threading.Lock()
_client: "GooglePlacesClient | None" = None

_logger = logging.getLogger("goplaces_mcp")
_logging_lock = threading.Lock()
_logging_configured = False


def _configure_logging() -> None:
    """Attach a stderr handler once, and only when debugging is requested.

    stdout carries the MCP protocol, so diagnostics must never go there.
    """
    global _logging_configured
    with _logging_lock:
        if _logging_configured:
            return
        _logging_configured = True
        if not _as_env_bool("GOPLACES_DEBUG"):
            _logger.addHandler(logging.NullHandler())
            return
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter("goplaces %(levelname)s %(message)s"))
        _logger.addHandler(handler)
        _logger.setLevel(logging.DEBUG)


def _as_env_bool(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "y", "on"}


class ValidationError(ValueError):
    """User-fixable input validation error."""

    def __init__(self, field: str, message: str) -> None:
        super().__init__(f"{field}: {message}")
        self.field = field
        self.message = message


class GoogleAPIError(RuntimeError):
    """Google API request failure."""

    def __init__(self, status: int | None, message: str, payload: Any = None) -> None:
        prefix = f"Google API error {status}" if status else "Google API error"
        super().__init__(f"{prefix}: {message}")
        self.status = status
        self.message = message
        self.payload = payload


def check_goplaces_available() -> bool:
    """Return True when the Google Places API key is configured."""
    return bool(os.getenv("GOOGLE_PLACES_API_KEY", "").strip())


def _get_client() -> "GooglePlacesClient":
    global _client
    with _client_lock:
        api_key = os.getenv("GOOGLE_PLACES_API_KEY", "").strip()
        places_base_url = os.getenv("GOOGLE_PLACES_BASE_URL", _DEFAULT_PLACES_BASE_URL).strip() or _DEFAULT_PLACES_BASE_URL
        routes_base_url = os.getenv("GOOGLE_ROUTES_BASE_URL", _DEFAULT_ROUTES_BASE_URL).strip() or _DEFAULT_ROUTES_BASE_URL
        directions_base_url = os.getenv("GOOGLE_DIRECTIONS_BASE_URL", routes_base_url).strip() or routes_base_url
        timeout = _env_float("GOOGLE_PLACES_TIMEOUT_SECONDS", _DEFAULT_TIMEOUT_SECONDS)
        candidate = GooglePlacesClient(
            api_key=api_key,
            places_base_url=places_base_url,
            routes_base_url=routes_base_url,
            directions_base_url=directions_base_url,
            timeout=timeout,
            max_attempts=int(_env_float("GOOGLE_PLACES_MAX_ATTEMPTS", _DEFAULT_MAX_ATTEMPTS)),
            retry_base_delay=_env_float("GOOGLE_PLACES_RETRY_BASE_DELAY_SECONDS", _DEFAULT_RETRY_BASE_DELAY_SECONDS),
        )
        if _client is None or _client.config_key != candidate.config_key:
            _client = candidate
        return _client


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _json_result(payload: Any) -> str:
    return json.dumps(_strip_none(payload), ensure_ascii=False, separators=(",", ":"))


def _json_error(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        return _json_result({"error": {"type": "validation", "field": exc.field, "message": exc.message}})
    if isinstance(exc, GoogleAPIError):
        payload = {"error": {"type": "google_api", "message": exc.message}}
        if exc.status is not None:
            payload["error"]["status"] = exc.status
        if exc.payload is not None:
            payload["error"]["details"] = exc.payload
        return _json_result(payload)
    return _json_result({"error": {"type": exc.__class__.__name__, "message": str(exc)}})


def _strip_none(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _strip_none(v) for k, v in value.items() if v is not None}
    if isinstance(value, list):
        return [_strip_none(v) for v in value]
    return value


def _as_str(args: dict[str, Any], key: str, default: str = "") -> str:
    value = args.get(key, default)
    if value is None:
        return default
    return str(value).strip()


def _as_bool(args: dict[str, Any], key: str, default: bool = False) -> bool:
    value = args.get(key, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)


def _as_int(args: dict[str, Any], key: str, default: int = 0) -> int:
    value = args.get(key, default)
    if value is None or value == "":
        return default
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValidationError(key, "must be an integer") from exc


def _as_float(args: dict[str, Any], key: str, default: float | None = None) -> float | None:
    value = args.get(key, default)
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ValidationError(key, "must be a number") from exc


def _as_string_list(args: dict[str, Any], key: str) -> list[str]:
    value = args.get(key)
    if value is None or value == "":
        return []
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    if isinstance(value, Iterable):
        return [str(part).strip() for part in value if str(part).strip()]
    raise ValidationError(key, "must be a string or list of strings")


def _limit(args: dict[str, Any], *, key: str = "limit", default: int = 10, maximum: int = _MAX_RESULTS) -> int:
    limit = _as_int(args, key, default)
    if limit < 1 or limit > maximum:
        raise ValidationError(key, f"must be 1-{maximum}")
    return limit


def _validate_lat_lng(lat: float, lng: float, prefix: str = "location") -> None:
    if lat < -90 or lat > 90:
        raise ValidationError(f"{prefix}.lat", "must be -90..90")
    if lng < -180 or lng > 180:
        raise ValidationError(f"{prefix}.lng", "must be -180..180")


def _circle_payload(lat: float, lng: float, radius_m: float, field: str = "location") -> dict[str, Any]:
    _validate_lat_lng(lat, lng, field)
    if radius_m <= 0:
        raise ValidationError(f"{field}.radius_m", "must be > 0")
    if radius_m > _MAX_CIRCLE_RADIUS_M:
        raise ValidationError(f"{field}.radius_m", f"must be <= {int(_MAX_CIRCLE_RADIUS_M)}")
    return {"circle": {"center": {"latitude": lat, "longitude": lng}, "radius": radius_m}}


def _maybe_circle_from_args(args: dict[str, Any], field: str = "location_bias") -> dict[str, Any] | None:
    has_any = any(args.get(k) is not None for k in ("lat", "lng", "radius_m"))
    if not has_any:
        return None
    lat = _as_float(args, "lat")
    lng = _as_float(args, "lng")
    radius_m = _as_float(args, "radius_m")
    if lat is None or lng is None or radius_m is None:
        raise ValidationError(field, "lat, lng, and radius_m are required together")
    return _circle_payload(lat, lng, radius_m, field)


def _require_text(value: str, field: str) -> str:
    value = value.strip()
    if not value:
        raise ValidationError(field, "required")
    return value


def _normalize_url_base(value: str) -> str:
    return value.rstrip("/")


class GooglePlacesClient:
    """Tiny stdlib Google Places/Routes API client."""

    def __init__(
        self,
        api_key: str,
        places_base_url: str,
        routes_base_url: str,
        directions_base_url: str,
        timeout: float,
        max_attempts: int = _DEFAULT_MAX_ATTEMPTS,
        retry_base_delay: float = _DEFAULT_RETRY_BASE_DELAY_SECONDS,
    ) -> None:
        if not api_key:
            raise ValidationError("GOOGLE_PLACES_API_KEY", "environment variable is required")
        self.api_key = api_key
        self.places_base_url = _normalize_url_base(places_base_url)
        self.routes_base_url = _normalize_url_base(routes_base_url)
        self.directions_base_url = _normalize_url_base(directions_base_url)
        self.timeout = timeout
        self.max_attempts = max(1, max_attempts)
        self.retry_base_delay = max(0.0, retry_base_delay)
        self.config_key = (
            api_key,
            self.places_base_url,
            self.routes_base_url,
            self.directions_base_url,
            timeout,
            self.max_attempts,
            self.retry_base_delay,
        )

    def request(self, method: str, url: str, body: dict[str, Any] | None = None, field_mask: str = "") -> dict[str, Any]:
        """Send one Google request, retrying throttled and unavailable responses."""
        _configure_logging()
        last_error: GoogleAPIError | None = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                raw = self._attempt(method, url, body, field_mask, attempt)
            except GoogleAPIError as exc:
                if exc.status not in _RETRY_STATUSES or attempt == self.max_attempts:
                    raise
                last_error = exc
                delay = self.retry_base_delay * (2 ** (attempt - 1))
                _logger.debug("retrying %s after status %s in %.2fs", url, exc.status, delay)
                if delay:
                    time.sleep(delay)
                continue
            if not raw.strip():
                return {}
            payload = _loads_json(raw)
            if not isinstance(payload, dict):
                raise GoogleAPIError(None, "unexpected non-object JSON response", payload)
            return payload
        # Unreachable: the loop either returns or re-raises on the final attempt.
        raise last_error or GoogleAPIError(None, "request failed")

    def _attempt(self, method: str, url: str, body: dict[str, Any] | None, field_mask: str, attempt: int) -> str:
        data = None if body is None else json.dumps(body).encode("utf-8")
        headers = {
            "X-Goog-Api-Key": self.api_key,
            "Accept": "application/json",
        }
        if body is not None:
            headers["Content-Type"] = "application/json"
        if field_mask:
            headers["X-Goog-FieldMask"] = field_mask
        request = urllib.request.Request(url, data=data, method=method.upper(), headers=headers)
        _logger.debug("request attempt %d %s %s mask=%s", attempt, method.upper(), url, field_mask or "-")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            payload = _loads_json_lenient(raw)
            message = _extract_google_error_message(payload) or raw or str(exc.reason)
            _logger.debug("error response %s %s: %s", exc.code, url, message)
            raise GoogleAPIError(exc.code, message, payload) from exc
        except urllib.error.URLError as exc:
            _logger.debug("transport failure %s: %s", url, exc.reason)
            raise GoogleAPIError(None, str(exc.reason)) from exc

    def places_url(self, path: str, query: dict[str, str] | None = None) -> str:
        return _build_url(self.places_base_url, path, query)

    def routes_url(self, path: str) -> str:
        return _build_url(self.routes_base_url, path, None)

    def directions_url(self, path: str) -> str:
        return _build_url(self.directions_base_url, path, None)


def _build_url(base: str, path: str, query: dict[str, str] | None) -> str:
    if not path.startswith("/"):
        path = "/" + path
    url = base.rstrip("/") + path
    clean_query = {k: v for k, v in (query or {}).items() if v}
    if clean_query:
        url += "?" + urllib.parse.urlencode(clean_query)
    return url


def _loads_json_lenient(raw: str) -> Any:
    """Decode an error body, tolerating the non-JSON pages Google proxies emit."""
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def _loads_json(raw: str) -> Any:
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise GoogleAPIError(None, "failed to decode JSON response", raw[:500]) from exc


def _extract_google_error_message(payload: Any) -> str:
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            return str(error.get("message") or error.get("status") or "")
    return ""


def goplaces_search(args: dict[str, Any], **_: Any) -> str:
    """Search Google Places by text."""
    try:
        client = _get_client()
        query = _require_text(_as_str(args, "query"), "query")
        keyword = _as_str(args, "keyword")
        if keyword:
            query = f"{query} {keyword}".strip()
        body: dict[str, Any] = {"textQuery": query, "pageSize": _limit(args, default=10)}
        _add_locale(body, args)
        page_token = _as_str(args, "page_token")
        if page_token:
            body["pageToken"] = page_token
        location_bias = _maybe_circle_from_args(args, "location_bias")
        if location_bias:
            body["locationBias"] = location_bias
        included_type = _as_str(args, "included_type")
        if included_type:
            body["includedType"] = included_type
        if "open_now" in args:
            body["openNow"] = _as_bool(args, "open_now")
        if "min_rating" in args and args.get("min_rating") is not None:
            rating = _as_float(args, "min_rating")
            if rating is None or rating < 0 or rating > 5:
                raise ValidationError("min_rating", "must be 0-5")
            body["minRating"] = rating
        price_levels = _price_level_enums(args.get("price_levels"))
        if price_levels:
            body["priceLevels"] = price_levels
        payload = client.request("POST", client.places_url("/places:searchText"), body, _SEARCH_FIELD_MASK)
        return _json_result(_map_search_response(payload))
    except Exception as exc:  # noqa: BLE001 - handlers return errors, never raise
        return _json_error(exc)


def goplaces_nearby(args: dict[str, Any], **_: Any) -> str:
    """Search places near coordinates."""
    try:
        client = _get_client()
        lat = _as_float(args, "lat")
        lng = _as_float(args, "lng")
        radius_m = _as_float(args, "radius_m")
        if lat is None or lng is None or radius_m is None:
            raise ValidationError("location_restriction", "lat, lng, and radius_m are required")
        body: dict[str, Any] = {
            "locationRestriction": _circle_payload(lat, lng, radius_m, "location_restriction"),
            "maxResultCount": _limit(args, default=10),
        }
        _add_locale(body, args)
        included = _as_string_list(args, "included_types")
        excluded = _as_string_list(args, "excluded_types")
        if included:
            body["includedTypes"] = included
        if excluded:
            body["excludedTypes"] = excluded
        payload = client.request("POST", client.places_url("/places:searchNearby"), body, _NEARBY_FIELD_MASK)
        return _json_result(_map_search_response(payload))
    except Exception as exc:  # noqa: BLE001
        return _json_error(exc)


def goplaces_autocomplete(args: dict[str, Any], **_: Any) -> str:
    """Autocomplete places and queries."""
    try:
        client = _get_client()
        limit = _limit(args, default=5)
        body: dict[str, Any] = {
            "input": _require_text(_as_str(args, "input"), "input"),
            "includeQueryPredictions": True,
        }
        _add_locale(body, args)
        session_token = _as_str(args, "session_token")
        if session_token:
            body["sessionToken"] = session_token
        location_bias = _maybe_circle_from_args(args, "location_bias")
        if location_bias:
            body["locationBias"] = location_bias
        payload = client.request("POST", client.places_url("/places:autocomplete"), body, _AUTOCOMPLETE_FIELD_MASK)
        suggestions = [_map_autocomplete_suggestion(item) for item in payload.get("suggestions", [])]
        return _json_result({"suggestions": [item for item in suggestions if item][:limit]})
    except Exception as exc:  # noqa: BLE001
        return _json_error(exc)


def goplaces_details(args: dict[str, Any], **_: Any) -> str:
    """Fetch Place Details."""
    try:
        client = _get_client()
        place_id = _normalize_place_id(_require_text(_as_str(args, "place_id"), "place_id"))
        field_mask_parts = [_DETAILS_FIELD_MASK_BASE]
        if _as_bool(args, "include_reviews"):
            field_mask_parts.append("reviews")
        if _as_bool(args, "include_photos"):
            field_mask_parts.append("photos")
        query = _locale_query(args)
        payload = client.request(
            "GET",
            client.places_url(f"/places/{_quote_path_segment(place_id)}", query),
            None,
            ",".join(field_mask_parts),
        )
        return _json_result(_map_place_details(payload))
    except Exception as exc:  # noqa: BLE001
        return _json_error(exc)


def goplaces_photo(args: dict[str, Any], **_: Any) -> str:
    """Fetch a photo media URL."""
    try:
        client = _get_client()
        name = _require_text(_as_str(args, "name"), "name").strip("/")
        name = name.removesuffix("/media")
        parts = name.split("/")
        if len(parts) != 4 or parts[0] != "places" or parts[2] != "photos" or not parts[1] or not parts[3]:
            raise ValidationError("name", "must be places/{place_id}/photos/{photo_id}")
        max_width = _as_int(args, "max_width_px", 0)
        max_height = _as_int(args, "max_height_px", 0)
        for field, value in (("max_width_px", max_width), ("max_height_px", max_height)):
            if value < 0 or value > 4800:
                raise ValidationError(field, "must be 1-4800")
        if max_width == 0 and max_height == 0:
            raise ValidationError("max_width_px", "max_width_px or max_height_px required")
        query = {"skipHttpRedirect": "true"}
        if max_width:
            query["maxWidthPx"] = str(max_width)
        if max_height:
            query["maxHeightPx"] = str(max_height)
        path = "/" + "/".join(_quote_path_segment(part) for part in parts) + "/media"
        payload = client.request("GET", client.places_url(path, query), None, "")
        return _json_result({"name": payload.get("name"), "photo_uri": payload.get("photoUri")})
    except Exception as exc:  # noqa: BLE001
        return _json_error(exc)


def goplaces_resolve(args: dict[str, Any], **_: Any) -> str:
    """Resolve free-form location text into candidate places."""
    try:
        client = _get_client()
        body: dict[str, Any] = {
            "textQuery": _require_text(_as_str(args, "location_text"), "location_text"),
            "pageSize": _limit(args, default=5, maximum=_MAX_RESOLVE_RESULTS),
        }
        _add_locale(body, args)
        payload = client.request("POST", client.places_url("/places:searchText"), body, _RESOLVE_FIELD_MASK)
        results = [_map_resolved_location(place) for place in payload.get("places", [])]
        return _json_result({"results": results})
    except Exception as exc:  # noqa: BLE001
        return _json_error(exc)


def goplaces_directions(args: dict[str, Any], **_: Any) -> str:
    """Fetch directions between two endpoints."""
    try:
        client = _get_client()
        include_steps = _as_bool(args, "include_steps")
        primary_mode = _normalize_direction_mode(_as_str(args, "mode", "walk"))
        compare_raw = _as_str(args, "compare_mode")
        compare_mode = _normalize_direction_mode(compare_raw) if compare_raw else ""
        if compare_mode and compare_mode == primary_mode:
            raise ValidationError("compare_mode", "must be different from mode")
        if _as_str(args, "arrival_time") and compare_mode:
            raise ValidationError("compare_mode", "cannot combine with arrival_time")
        avoid_requested = any(
            _as_bool(args, field)
            for field in ("avoid_tolls", "avoid_highways", "avoid_ferries")
        )
        if avoid_requested and primary_mode != "DRIVE" and compare_mode != "DRIVE":
            raise ValidationError("route_modifiers", "avoid tolls/highways/ferries require drive mode")

        request_body = _directions_body(args, primary_mode)
        payload = client.request(
            "POST",
            client.directions_url("/directions/v2:computeRoutes"),
            request_body,
            _DIRECTIONS_FIELD_MASK,
        )
        primary = _map_directions_response(payload, primary_mode, args, include_steps)
        if not compare_mode:
            return _json_result(primary)

        compare_body = _directions_body(args, compare_mode)
        compare_payload = client.request(
            "POST",
            client.directions_url("/directions/v2:computeRoutes"),
            compare_body,
            _DIRECTIONS_FIELD_MASK,
        )
        compare = _map_directions_response(compare_payload, compare_mode, args, include_steps)
        return _json_result({"routes": [primary, compare]})
    except Exception as exc:  # noqa: BLE001
        return _json_error(exc)


def goplaces_route_search(args: dict[str, Any], **_: Any) -> str:
    """Search for places along a route."""
    try:
        client = _get_client()
        query = _require_text(_as_str(args, "query"), "query")
        from_text = _require_text(_as_str(args, "from_text"), "from_text")
        to_text = _require_text(_as_str(args, "to_text"), "to_text")
        route_mode = _normalize_route_mode(_as_str(args, "mode", "drive"))
        max_waypoints = _as_int(args, "max_waypoints", 5)
        if max_waypoints < 1 or max_waypoints > _MAX_ROUTE_WAYPOINTS:
            raise ValidationError("max_waypoints", f"must be 1-{_MAX_ROUTE_WAYPOINTS}")
        radius_m = _as_float(args, "radius_m", 1000.0)
        if radius_m is None or radius_m <= 0 or radius_m > _MAX_CIRCLE_RADIUS_M:
            raise ValidationError("radius_m", f"must be > 0 and <= {int(_MAX_CIRCLE_RADIUS_M)}")
        limit = _limit(args, default=5)
        route_body: dict[str, Any] = {
            "origin": {"address": from_text},
            "destination": {"address": to_text},
            "travelMode": route_mode,
            "polylineQuality": "OVERVIEW",
            "polylineEncoding": "ENCODED_POLYLINE",
        }
        _add_locale(route_body, args)
        route_payload = client.request(
            "POST",
            client.routes_url("/directions/v2:computeRoutes"),
            route_body,
            _ROUTE_POLYLINE_FIELD_MASK,
        )
        polyline = _first_route_polyline(route_payload)
        points = _decode_polyline(polyline)
        waypoints = _sample_waypoints(points, max_waypoints)
        if not waypoints:
            raise GoogleAPIError(None, "no route waypoints returned")

        seen: set[str] = set()
        response_waypoints = []
        for point in waypoints:
            search_body: dict[str, Any] = {
                "textQuery": query,
                "pageSize": limit,
                "locationBias": _circle_payload(point["lat"], point["lng"], radius_m, "route_waypoint"),
            }
            _add_locale(search_body, args)
            search_payload = client.request("POST", client.places_url("/places:searchText"), search_body, _SEARCH_FIELD_MASK)
            mapped_results = []
            for place in search_payload.get("places", []):
                summary = _map_place_summary(place)
                place_id = summary.get("place_id")
                if place_id:
                    if place_id in seen:
                        continue
                    seen.add(place_id)
                mapped_results.append(summary)
            response_waypoints.append({"location": point, "results": mapped_results})
        return _json_result({"waypoints": response_waypoints})
    except Exception as exc:  # noqa: BLE001
        return _json_error(exc)


def _add_locale(body: dict[str, Any], args: dict[str, Any]) -> None:
    language = _as_str(args, "language")
    region = _as_str(args, "region")
    if language:
        body["languageCode"] = language
    if region:
        body["regionCode"] = region


def _locale_query(args: dict[str, Any]) -> dict[str, str]:
    query: dict[str, str] = {}
    language = _as_str(args, "language")
    region = _as_str(args, "region")
    if language:
        query["languageCode"] = language
    if region:
        query["regionCode"] = region
    return query


def _price_level_enums(value: Any) -> list[str]:
    if value is None or value == "":
        return []
    raw_values: Iterable[Any]
    if isinstance(value, str):
        raw_values = [part.strip() for part in value.split(",") if part.strip()]
    elif isinstance(value, Iterable):
        raw_values = value
    else:
        raw_values = [value]
    enums = []
    for item in raw_values:
        try:
            level = int(item)
        except (TypeError, ValueError) as exc:
            raise ValidationError("price_levels", "must contain integers 0-4") from exc
        if level < 0 or level > 4:
            raise ValidationError("price_levels", "must contain integers 0-4")
        enums.append(_PRICE_TO_ENUM[level])
    return enums


def _normalize_place_id(place_id: str) -> str:
    place_id = place_id.strip().removeprefix("/").removeprefix("places/")
    if not place_id or "/" in place_id:
        raise ValidationError("place_id", "must be a place ID or places/{place_id}")
    return place_id


def _quote_path_segment(value: str) -> str:
    return urllib.parse.quote(value, safe="")


def _map_search_response(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "results": [_map_place_summary(place) for place in payload.get("places", [])],
        "next_page_token": payload.get("nextPageToken"),
    }


def _display_name(place: dict[str, Any]) -> str:
    display = place.get("displayName")
    if isinstance(display, dict):
        return str(display.get("text") or "")
    return ""


def _map_location(location: dict[str, Any] | None) -> dict[str, float] | None:
    if not isinstance(location, dict):
        return None
    lat = location.get("latitude")
    lng = location.get("longitude")
    if lat is None or lng is None:
        return None
    return {"lat": lat, "lng": lng}


def _map_price_level(value: Any) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, int):
        return value
    return _ENUM_TO_PRICE.get(str(value))


def _open_now(place: dict[str, Any]) -> bool | None:
    hours = place.get("currentOpeningHours")
    if isinstance(hours, dict) and "openNow" in hours:
        return bool(hours["openNow"])
    return None


def _map_place_summary(place: dict[str, Any]) -> dict[str, Any]:
    return {
        "place_id": place.get("id"),
        "name": _display_name(place),
        "address": place.get("formattedAddress"),
        "location": _map_location(place.get("location")),
        "rating": place.get("rating"),
        "user_rating_count": place.get("userRatingCount"),
        "price_level": _map_price_level(place.get("priceLevel")),
        "types": place.get("types") or [],
        "open_now": _open_now(place),
        "business_status": place.get("businessStatus"),
    }


def _map_resolved_location(place: dict[str, Any]) -> dict[str, Any]:
    return {
        "place_id": place.get("id"),
        "name": _display_name(place),
        "address": place.get("formattedAddress"),
        "location": _map_location(place.get("location")),
        "types": place.get("types") or [],
    }


def _map_place_details(place: dict[str, Any]) -> dict[str, Any]:
    regular_hours = place.get("regularOpeningHours") if isinstance(place.get("regularOpeningHours"), dict) else {}
    return {
        "place_id": place.get("id"),
        "name": _display_name(place),
        "address": place.get("formattedAddress"),
        "location": _map_location(place.get("location")),
        "rating": place.get("rating"),
        "user_rating_count": place.get("userRatingCount"),
        "price_level": _map_price_level(place.get("priceLevel")),
        "types": place.get("types") or [],
        "phone": place.get("nationalPhoneNumber"),
        "website": place.get("websiteUri"),
        "hours": regular_hours.get("weekdayDescriptions") or [],
        "open_now": _open_now(place),
        "business_status": place.get("businessStatus"),
        "reviews": [_map_review(review) for review in place.get("reviews", [])],
        "photos": [_map_photo(photo) for photo in place.get("photos", [])],
    }


def _map_localized_text(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    return {"text": value.get("text"), "language_code": value.get("languageCode")}


def _map_author(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    return {
        "display_name": value.get("displayName"),
        "uri": value.get("uri"),
        "photo_uri": value.get("photoUri"),
    }


def _map_review(review: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": review.get("name"),
        "relative_publish_time_description": review.get("relativePublishTimeDescription"),
        "text": _map_localized_text(review.get("text")),
        "original_text": _map_localized_text(review.get("originalText")),
        "rating": review.get("rating"),
        "author": _map_author(review.get("authorAttribution")),
        "publish_time": review.get("publishTime"),
        "flag_content_uri": review.get("flagContentUri"),
        "google_maps_uri": review.get("googleMapsUri"),
        "visit_date": _strip_none({
            "year": (review.get("visitDate") or {}).get("year") if isinstance(review.get("visitDate"), dict) else None,
            "month": (review.get("visitDate") or {}).get("month") if isinstance(review.get("visitDate"), dict) else None,
            "day": (review.get("visitDate") or {}).get("day") if isinstance(review.get("visitDate"), dict) else None,
        }),
    }


def _map_photo(photo: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": photo.get("name"),
        "width_px": photo.get("widthPx"),
        "height_px": photo.get("heightPx"),
        "author_attributions": [_map_author(author) for author in photo.get("authorAttributions", [])],
    }


def _map_autocomplete_suggestion(item: dict[str, Any]) -> dict[str, Any] | None:
    place = item.get("placePrediction")
    if isinstance(place, dict):
        structured = place.get("structuredFormat") if isinstance(place.get("structuredFormat"), dict) else {}
        return {
            "kind": "place",
            "place_id": place.get("placeId"),
            "place": place.get("place"),
            "text": _autocomplete_text(place.get("text")),
            "main_text": _autocomplete_text(structured.get("mainText")),
            "secondary_text": _autocomplete_text(structured.get("secondaryText")),
            "types": place.get("types") or [],
            "distance_meters": place.get("distanceMeters"),
        }
    query = item.get("queryPrediction")
    if isinstance(query, dict):
        structured = query.get("structuredFormat") if isinstance(query.get("structuredFormat"), dict) else {}
        return {
            "kind": "query",
            "text": _autocomplete_text(query.get("text")),
            "main_text": _autocomplete_text(structured.get("mainText")),
            "secondary_text": _autocomplete_text(structured.get("secondaryText")),
        }
    return None


def _autocomplete_text(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("text") or "")
    return ""


def _normalize_direction_mode(mode: str) -> str:
    if not mode:
        mode = "walk"
    normalized = _DIRECTION_MODE_TO_API.get(mode.strip().lower())
    if not normalized:
        raise ValidationError("mode", "must be walk, drive, bicycle, or transit")
    return normalized


def _normalize_route_mode(mode: str) -> str:
    if not mode:
        mode = "drive"
    normalized = _ROUTE_MODE_TO_API.get(mode.strip().lower())
    if not normalized:
        raise ValidationError("mode", "must be drive, walk, bicycle, two_wheeler, or transit")
    return normalized


def _directions_body(args: dict[str, Any], api_mode: str) -> dict[str, Any]:
    body: dict[str, Any] = {
        "origin": _waypoint_from_args(args, "from"),
        "destination": _waypoint_from_args(args, "to"),
        "travelMode": api_mode,
    }
    units = _as_str(args, "units", "metric").lower()
    if units not in {"metric", "imperial"}:
        raise ValidationError("units", "must be metric or imperial")
    body["units"] = "IMPERIAL" if units == "imperial" else "METRIC"
    _add_locale(body, args)

    departure = _as_str(args, "departure_time")
    arrival = _as_str(args, "arrival_time")
    if departure and arrival:
        raise ValidationError("time", "use only one of departure_time or arrival_time")
    if departure:
        _validate_rfc3339ish(departure, "departure_time")
        body["departureTime"] = departure
    if arrival:
        if api_mode != "TRANSIT":
            raise ValidationError("arrival_time", "requires transit mode")
        _validate_rfc3339ish(arrival, "arrival_time")
        body["arrivalTime"] = arrival

    avoid_tolls = _as_bool(args, "avoid_tolls")
    avoid_highways = _as_bool(args, "avoid_highways")
    avoid_ferries = _as_bool(args, "avoid_ferries")
    if any((avoid_tolls, avoid_highways, avoid_ferries)) and api_mode == "DRIVE":
        body["routeModifiers"] = {
            "avoidTolls": avoid_tolls,
            "avoidHighways": avoid_highways,
            "avoidFerries": avoid_ferries,
        }
    return body


def _waypoint_from_args(args: dict[str, Any], prefix: str) -> dict[str, Any]:
    text = _as_str(args, f"{prefix}_text")
    place_id = _as_str(args, f"{prefix}_place_id")
    lat_present = args.get(f"{prefix}_lat") is not None
    lng_present = args.get(f"{prefix}_lng") is not None
    lat = _as_float(args, f"{prefix}_lat") if lat_present else None
    lng = _as_float(args, f"{prefix}_lng") if lng_present else None

    provided = sum([bool(text), bool(place_id), lat is not None or lng is not None])
    if provided == 0:
        raise ValidationError(prefix, "one of text, place_id, or lat/lng is required")
    if provided > 1:
        raise ValidationError(prefix, "use only one of text, place_id, or lat/lng")
    if text:
        return {"address": text}
    if place_id:
        return {"placeId": _normalize_place_id(place_id)}
    if lat is None or lng is None:
        raise ValidationError(f"{prefix}_location", "lat and lng required")
    _validate_lat_lng(lat, lng, prefix)
    return {"location": {"latLng": {"latitude": lat, "longitude": lng}}}


def _validate_rfc3339ish(value: str, field: str) -> None:
    # Avoid importing dateutil; this catches most bad values while letting Google
    # perform the definitive validation.
    if not re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$", value):
        raise ValidationError(field, "must be RFC3339, e.g. 2030-05-10T18:57:00-03:00")


def _map_directions_response(payload: dict[str, Any], api_mode: str, args: dict[str, Any], include_steps: bool) -> dict[str, Any]:
    route = _first_route(payload)
    legs = route.get("legs") or []
    if not legs:
        raise GoogleAPIError(None, "no directions returned", payload)
    leg = legs[0]
    localized = leg.get("localizedValues") or {}
    response = {
        "mode": _API_TO_DIRECTION_MODE.get(api_mode, api_mode.lower()),
        "summary": route.get("description"),
        "start_address": _waypoint_label(args, "from"),
        "end_address": _waypoint_label(args, "to"),
        "distance_text": (localized.get("distance") or {}).get("text"),
        "distance_meters": leg.get("distanceMeters"),
        "duration_text": (localized.get("duration") or {}).get("text"),
        "duration_seconds": _parse_duration_seconds(leg.get("duration")),
        "departure_time": _as_str(args, "departure_time"),
        "arrival_time": _as_str(args, "arrival_time"),
        "warnings": route.get("warnings") or [],
    }
    if include_steps:
        response["steps"] = [_map_directions_step(step) for step in leg.get("steps", [])]
    return response


def _first_route(payload: dict[str, Any]) -> dict[str, Any]:
    routes = payload.get("routes") or []
    if not routes:
        raise GoogleAPIError(None, "no routes returned", payload)
    route = routes[0]
    if not isinstance(route, dict):
        raise GoogleAPIError(None, "unexpected route response", payload)
    return route


def _first_route_polyline(payload: dict[str, Any]) -> str:
    route = _first_route(payload)
    polyline = (route.get("polyline") or {}).get("encodedPolyline") or ""
    if not polyline:
        raise GoogleAPIError(None, "empty route polyline", payload)
    return polyline


def _waypoint_label(args: dict[str, Any], prefix: str) -> str:
    text = _as_str(args, f"{prefix}_text")
    if text:
        return text
    place_id = _as_str(args, f"{prefix}_place_id")
    if place_id:
        return "place_id:" + _normalize_place_id(place_id)
    lat = args.get(f"{prefix}_lat")
    lng = args.get(f"{prefix}_lng")
    if lat is not None and lng is not None:
        return f"{lat},{lng}"
    return ""


def _map_directions_step(step: dict[str, Any]) -> dict[str, Any]:
    localized = step.get("localizedValues") or {}
    instruction = step.get("navigationInstruction") or {}
    return {
        "instruction": instruction.get("instructions"),
        "distance_text": (localized.get("distance") or {}).get("text"),
        "distance_meters": step.get("distanceMeters"),
        "duration_text": (localized.get("staticDuration") or {}).get("text"),
        "duration_seconds": _parse_duration_seconds(step.get("staticDuration")),
        "travel_mode": step.get("travelMode"),
        "maneuver": instruction.get("maneuver"),
    }


def _parse_duration_seconds(value: Any) -> int | None:
    if not isinstance(value, str):
        return None
    if value.endswith("s"):
        try:
            return int(float(value[:-1]))
        except ValueError:
            return None
    return None


def _decode_polyline(encoded: str) -> list[dict[str, float]]:
    if not encoded:
        raise GoogleAPIError(None, "empty polyline")
    points: list[dict[str, float]] = []
    index = 0
    lat = 0
    lng = 0
    length = len(encoded)
    while index < length:
        delta_lat, index = _decode_polyline_value(encoded, index)
        delta_lng, index = _decode_polyline_value(encoded, index)
        lat += delta_lat
        lng += delta_lng
        points.append({"lat": lat / 1e5, "lng": lng / 1e5})
    return points


def _decode_polyline_value(encoded: str, index: int) -> tuple[int, int]:
    result = 0
    shift = 0
    while True:
        if index >= len(encoded):
            raise GoogleAPIError(None, "invalid polyline")
        b = ord(encoded[index]) - 63
        index += 1
        result |= (b & 0x1F) << shift
        shift += 5
        if b < 0x20:
            break
    delta = ~(result >> 1) if result & 1 else result >> 1
    return delta, index


def _sample_waypoints(points: list[dict[str, float]], max_waypoints: int) -> list[dict[str, float]]:
    if max_waypoints <= 0 or not points:
        return []
    if len(points) <= max_waypoints:
        return points
    if max_waypoints == 1:
        return [points[len(points) // 2]]
    indexes = []
    last = len(points) - 1
    for i in range(max_waypoints):
        indexes.append(round(i * last / (max_waypoints - 1)))
    # Preserve order and avoid duplicate rounded indexes on short polylines.
    out: list[dict[str, float]] = []
    seen: set[int] = set()
    for idx in indexes:
        if idx not in seen:
            seen.add(idx)
            out.append(points[idx])
    return out
