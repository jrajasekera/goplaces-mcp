"""Tool handlers for the goplaces MCP plugin.

This is a dependency-free Python port of the useful goplaces CLI workflows:
text search, nearby search, autocomplete, details, photo media, location
resolution, directions, and search-along-route.
"""

from __future__ import annotations

import base64
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
# Reverse geocoding is a nearest-thing lookup, so the radius stays small; a
# wide one would return a neighbourhood rather than what is at the point.
_MAX_REVERSE_GEOCODE_RADIUS_M = 500.0
# Google returns these when it is throttling or briefly unavailable; both are
# worth one bounded retry. Every other status is a real failure.
_RETRY_STATUSES = frozenset({429, 503})
_DEFAULT_MAX_ATTEMPTS = 3
_DEFAULT_RETRY_BASE_DELAY_SECONDS = 0.5
# Photo bytes travel to the host base64-encoded, which inflates them by a third.
# Keep the decoded ceiling well under typical MCP message limits.
_MAX_PHOTO_BYTES = 5_000_000

# Google bills each Places request at the highest SKU tier named in its field
# mask, so the mask is built from an explicit tier table rather than a fixed
# string. Tiers below are the Text Search / Nearby Search column of Google's
# Place Data Fields table; Place Details is cheaper for some of them, but
# billing the same mask at the stricter tier is the safe direction to err.
_TIER_IDS = "ids"
_TIER_BASIC = "basic"
_TIER_FULL = "full"
_DETAIL_LEVELS = (_TIER_IDS, _TIER_BASIC, _TIER_FULL)

# Essentials: place identifiers only.
_IDS_FIELDS = ("id",)
# Pro: enough to name, place, and link a result.
_BASIC_FIELDS = (
    "displayName",
    "formattedAddress",
    "location",
    "types",
    "primaryTypeDisplayName",
    "businessStatus",
    "googleMapsUri",
    "utcOffsetMinutes",
)
# Enterprise: the ranking and contact signals agents usually want.
_FULL_FIELDS = (
    "rating",
    "userRatingCount",
    "priceLevel",
    "priceRange",
    "currentOpeningHours",
    "nationalPhoneNumber",
    "internationalPhoneNumber",
    "websiteUri",
)
# Enterprise + Atmosphere: opt-in, because it is the most expensive tier.
_ATMOSPHERE_FIELDS = (
    "editorialSummary",
    "dineIn",
    "takeout",
    "delivery",
    "reservable",
    "parkingOptions",
    "accessibilityOptions",
)
_EV_FIELDS = ("evChargeOptions",)

_DETAILS_EXTRA_FIELDS = ("regularOpeningHours",)

_AUTOCOMPLETE_FIELD_MASK = (
    "suggestions.placePrediction.placeId,suggestions.placePrediction.place,"
    "suggestions.placePrediction.text,suggestions.placePrediction.structuredFormat,"
    "suggestions.placePrediction.types,suggestions.placePrediction.distanceMeters,"
    "suggestions.queryPrediction.text,suggestions.queryPrediction.structuredFormat"
)
# Resolve is a cheap coordinate lookup, so it deliberately stays off the
# rating/hours tier no matter what detail_level a caller passes.
_RESOLVE_FIELD_MASK = "places.id,places.displayName,places.formattedAddress,places.location,places.types"
_ROUTE_POLYLINE_FIELD_MASK = "routes.duration,routes.distanceMeters,routes.polyline.encodedPolyline"
# routingSummaries hangs off the response root, so it must NOT carry the
# "places." prefix the per-place tokens use.
_ROUTING_SUMMARY_FIELD_MASK = "routingSummaries"
_DIRECTIONS_FIELD_MASK = (
    "routes.description,routes.warnings,routes.distanceMeters,routes.duration,"
    # Route-level text covers every leg; legs[0] alone understates a waypoint trip.
    "routes.localizedValues.distance,routes.localizedValues.duration,"
    "routes.legs.distanceMeters,"
    "routes.legs.duration,routes.legs.localizedValues.distance,"
    "routes.legs.localizedValues.duration,routes.legs.steps.distanceMeters,"
    "routes.legs.steps.staticDuration,routes.legs.steps.localizedValues.distance,"
    "routes.legs.steps.localizedValues.staticDuration,"
    "routes.legs.steps.navigationInstruction.instructions,"
    "routes.legs.steps.navigationInstruction.maneuver,routes.legs.steps.travelMode,"
    "routes.legs.steps.transitDetails"
)
_ROUTE_MATRIX_FIELD_MASK = (
    "originIndex,destinationIndex,condition,distanceMeters,duration,"
    "localizedValues.distance,localizedValues.duration"
)

_MAPS_SEARCH_URL = "https://www.google.com/maps/search/"
_MAPS_DIRECTIONS_URL = "https://www.google.com/maps/dir/"

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
# Routes caps intermediate waypoints at 25.
_MAX_INTERMEDIATES = 25
# Routes caps address/place-ID matrix waypoints at 50 and pairs at 625.
_MAX_MATRIX_WAYPOINTS = 50
_MAX_MATRIX_PAIRS = 625
_ROUTING_PREFERENCES = {
    "traffic_unaware": "TRAFFIC_UNAWARE",
    "traffic_aware": "TRAFFIC_AWARE",
    "traffic_aware_optimal": "TRAFFIC_AWARE_OPTIMAL",
}
_TRANSIT_TRAVEL_MODES = frozenset({"BUS", "SUBWAY", "TRAIN", "LIGHT_RAIL", "RAIL"})
_TRANSIT_ROUTING_PREFERENCES = {
    "less_walking": "LESS_WALKING",
    "fewer_transfers": "FEWER_TRANSFERS",
}
_API_TO_MAPS_URL_MODE = {
    "DRIVE": "driving",
    "WALK": "walking",
    "BICYCLE": "bicycling",
    "TRANSIT": "transit",
    "TWO_WHEELER": "two-wheeler",
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


def _as_address_list(args: dict[str, Any], key: str) -> list[str]:
    """Read a list of addresses without comma-splitting.

    ``_as_string_list`` splits a bare string on commas, which is right for type
    names but would turn "1 Main St, Seattle" into two separate waypoints.
    """
    value = args.get(key)
    if value is None or value == "":
        return []
    if isinstance(value, str):
        stripped = value.strip()
        return [stripped] if stripped else []
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

    def request_list(self, method: str, url: str, body: dict[str, Any] | None, field_mask: str) -> list[Any]:
        """Like :meth:`request`, for endpoints that return a JSON array.

        ``computeRouteMatrix`` streams its elements as a top-level array, so it
        cannot go through the object-shaped path.
        """
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
                if delay:
                    time.sleep(delay)
                continue
            if not raw.strip():
                return []
            payload = _loads_json(raw)
            if isinstance(payload, dict):
                # A single error object can arrive in place of the array.
                message = _extract_google_error_message(payload)
                raise GoogleAPIError(None, message or "unexpected object response", payload)
            if not isinstance(payload, list):
                raise GoogleAPIError(None, "unexpected non-array JSON response", payload)
            return payload
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

    def fetch_bytes(self, url: str) -> tuple[bytes, str]:
        """Download a photo body, returning the bytes and its media type.

        Photo URIs point at Google's media CDN rather than the API host, so this
        deliberately sends no API key and no field mask.
        """
        _configure_logging()
        request = urllib.request.Request(url, method="GET")
        _logger.debug("fetching photo bytes from %s", url)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                content_type = response.headers.get("Content-Type", "image/jpeg").split(";")[0].strip()
                data = response.read(_MAX_PHOTO_BYTES + 1)
        except urllib.error.HTTPError as exc:
            raise GoogleAPIError(exc.code, f"photo download failed: {exc.reason}") from exc
        except urllib.error.URLError as exc:
            raise GoogleAPIError(None, f"photo download failed: {exc.reason}") from exc
        if len(data) > _MAX_PHOTO_BYTES:
            raise GoogleAPIError(
                None,
                f"photo exceeds {_MAX_PHOTO_BYTES} bytes; request a smaller max_width_px",
            )
        return data, content_type or "image/jpeg"

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
        ev_options = _ev_options(args)
        if ev_options:
            body["evOptions"] = ev_options
        routing = _routing_parameters(args)
        if routing:
            body["routingParameters"] = routing
        field_mask = _place_field_mask(
            _detail_level(args),
            prefix="places.",
            atmosphere=_as_bool(args, "include_atmosphere"),
            ev=bool(ev_options) or _as_bool(args, "include_ev"),
        )
        # nextPageToken hangs off the response root, like routingSummaries, so it
        # must not pick up the "places." prefix the per-place tokens use.
        field_mask += ",nextPageToken"
        if routing:
            field_mask += "," + _ROUTING_SUMMARY_FIELD_MASK
        payload = client.request("POST", client.places_url("/places:searchText"), body, field_mask)
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
        rank = _as_str(args, "rank_by").lower()
        if rank:
            if rank not in {"distance", "popularity"}:
                raise ValidationError("rank_by", "must be distance or popularity")
            body["rankPreference"] = rank.upper()
        routing = _routing_parameters(args)
        if routing:
            body["routingParameters"] = routing
        field_mask = _place_field_mask(
            _detail_level(args),
            prefix="places.",
            atmosphere=_as_bool(args, "include_atmosphere"),
            ev=_as_bool(args, "include_ev"),
        )
        if routing:
            field_mask += "," + _ROUTING_SUMMARY_FIELD_MASK
        payload = client.request("POST", client.places_url("/places:searchNearby"), body, field_mask)
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
        level = _detail_level(args)
        # regularOpeningHours is an Enterprise-tier field, so requesting it at a
        # cheaper detail_level would quietly bill at the top tier anyway.
        extra = list(_DETAILS_EXTRA_FIELDS) if level == _TIER_FULL else []
        if _as_bool(args, "include_reviews"):
            extra.append("reviews")
        if _as_bool(args, "include_photos"):
            extra.append("photos")
        field_mask = _place_field_mask(
            level,
            prefix="",
            atmosphere=_as_bool(args, "include_atmosphere"),
            ev=_as_bool(args, "include_ev"),
            extra=extra,
        )
        query = _locale_query(args)
        payload = client.request(
            "GET",
            client.places_url(f"/places/{_quote_path_segment(place_id)}", query),
            None,
            field_mask,
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
        photo_uri = payload.get("photoUri")
        result: dict[str, Any] = {"name": payload.get("name"), "photo_uri": photo_uri}
        if _as_bool(args, "include_image") and photo_uri:
            data, mime_type = client.fetch_bytes(photo_uri)
            # The adapter lifts these into an MCP image block so the model can
            # actually look at the photo instead of passing a URL along.
            result["image_base64"] = base64.b64encode(data).decode("ascii")
            result["image_mime_type"] = mime_type
        return _json_result(result)
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
        primary_mode = _normalize_direction_mode(_as_str(args, "mode", "drive"))
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
        # Build the compare body up front too: it validates mode-specific options
        # against the second mode, and doing that after the first request would
        # discard a result the user already paid for.
        compare_body = _directions_body(args, compare_mode) if compare_mode else None
        payload = client.request(
            "POST",
            client.directions_url("/directions/v2:computeRoutes"),
            request_body,
            _DIRECTIONS_FIELD_MASK,
        )
        primary = _map_directions_response(payload, primary_mode, args, include_steps)
        if not compare_mode:
            return _json_result(primary)

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
    """Search for places along a route.

    Two requests total: one Routes call for the overview polyline, then one Text
    Search with ``searchAlongRouteParameters``. Google ranks by detour time and
    returns a routing summary per result, so there is no waypoint sampling, no
    per-waypoint fan-out, and no radius approximation.
    """
    try:
        client = _get_client()
        query = _require_text(_as_str(args, "query"), "query")
        from_text = _require_text(_as_str(args, "from_text"), "from_text")
        to_text = _require_text(_as_str(args, "to_text"), "to_text")
        route_mode = _normalize_route_mode(_as_str(args, "mode", "drive"))
        limit = _limit(args, default=5)
        # Validate every argument before spending the first billable request.
        detail_level = _detail_level(args)
        ev_options = _ev_options(args)
        min_rating = _as_float(args, "min_rating") if args.get("min_rating") is not None else None
        if min_rating is not None and (min_rating < 0 or min_rating > 5):
            raise ValidationError("min_rating", "must be 0-5")

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
        route = _first_route(route_payload)
        polyline = _first_route_polyline(route_payload)
        direct_seconds = _parse_duration_seconds(route.get("duration"))

        search_body: dict[str, Any] = {
            "textQuery": query,
            "pageSize": limit,
            "searchAlongRouteParameters": {"polyline": {"encodedPolyline": polyline}},
        }
        _add_locale(search_body, args)
        if ev_options:
            search_body["evOptions"] = ev_options
        if "open_now" in args:
            search_body["openNow"] = _as_bool(args, "open_now")
        if min_rating is not None:
            search_body["minRating"] = min_rating

        field_mask = _place_field_mask(
            detail_level,
            prefix="places.",
            atmosphere=_as_bool(args, "include_atmosphere"),
            ev=bool(ev_options) or _as_bool(args, "include_ev"),
        )
        field_mask += "," + _ROUTING_SUMMARY_FIELD_MASK
        search_payload = client.request(
            "POST",
            client.places_url("/places:searchText"),
            search_body,
            field_mask,
        )

        summaries = search_payload.get("routingSummaries")
        summaries = summaries if isinstance(summaries, list) else []
        results = []
        for index, place in enumerate(search_payload.get("places", [])):
            summary = _map_place_summary(place)
            entry = summaries[index] if index < len(summaries) else None
            _apply_routing_summary(
                entry,
                summary,
                duration_key="trip_duration_seconds",
                distance_key="trip_distance_meters",
            )
            detour = _detour_seconds(entry, direct_seconds)
            if detour is not None:
                summary["detour_seconds"] = detour
            summary["directions_url"] = summary.get("directions_url") or _maps_directions_url(
                from_text,
                to_text,
                travel_mode=route_mode,
            )
            results.append(summary)
        return _json_result({
            "route": {
                "from": from_text,
                "to": to_text,
                "mode": _API_TO_DIRECTION_MODE.get(route_mode, route_mode.lower()),
                "distance_meters": route.get("distanceMeters"),
                "duration_seconds": direct_seconds,
                "maps_url": _maps_directions_url(from_text, to_text, travel_mode=route_mode),
            },
            "results": results,
        })
    except Exception as exc:  # noqa: BLE001 - handlers return errors, never raise
        return _json_error(exc)


def _detour_seconds(summary: Any, direct_seconds: int | None) -> int | None:
    """Extra time a stop costs versus driving straight through.

    Search-along-route summaries carry two legs, origin -> place and place ->
    destination; their sum minus the direct route duration is the detour.
    """
    if direct_seconds is None:
        return None
    legs = _routing_summary_legs(summary)
    if len(legs) < 2:
        return None
    durations = [_parse_duration_seconds(leg.get("duration")) for leg in legs]
    if any(value is None for value in durations):
        return None
    return max(0, sum(durations) - direct_seconds)


def goplaces_route_matrix(args: dict[str, Any], **_: Any) -> str:
    """Rank destinations by travel time from one or more origins.

    One request answers "which of these is closest", instead of a directions
    call per candidate.
    """
    try:
        client = _get_client()
        origins = _matrix_waypoints(args, "origins")
        destinations = _matrix_waypoints(args, "destinations")
        pairs = len(origins) * len(destinations)
        if pairs > _MAX_MATRIX_PAIRS:
            raise ValidationError(
                "destinations",
                f"origins x destinations must be <= {_MAX_MATRIX_PAIRS}, got {pairs}",
            )
        api_mode = _normalize_route_mode(_as_str(args, "mode", "drive"))
        body: dict[str, Any] = {
            "origins": [{"waypoint": waypoint} for waypoint in origins],
            "destinations": [{"waypoint": waypoint} for waypoint in destinations],
            "travelMode": api_mode,
        }
        units = _as_str(args, "units", "metric").lower()
        if units not in {"metric", "imperial"}:
            raise ValidationError("units", "must be metric or imperial")
        body["units"] = "IMPERIAL" if units == "imperial" else "METRIC"
        _add_locale(body, args)
        departure = _as_str(args, "departure_time")
        if departure:
            _validate_rfc3339ish(departure, "departure_time")
            body["departureTime"] = departure
            if api_mode in {"DRIVE", "TWO_WHEELER"}:
                body["routingPreference"] = "TRAFFIC_AWARE"

        elements = client.request_list(
            "POST",
            client.routes_url("/distanceMatrix/v2:computeRouteMatrix"),
            body,
            _ROUTE_MATRIX_FIELD_MASK,
        )
        origin_labels = _as_address_list(args, "origins")
        destination_labels = _as_address_list(args, "destinations")
        results = []
        for element in elements:
            if not isinstance(element, dict):
                continue
            origin_index = element.get("originIndex")
            destination_index = element.get("destinationIndex")
            localized = element.get("localizedValues") or {}
            results.append(_strip_none({
                "origin_index": origin_index,
                "destination_index": destination_index,
                "origin": _label_at(origin_labels, origin_index),
                "destination": _label_at(destination_labels, destination_index),
                "condition": element.get("condition"),
                "distance_meters": element.get("distanceMeters"),
                "distance_text": (localized.get("distance") or {}).get("text"),
                "duration_seconds": _parse_duration_seconds(element.get("duration")),
                "duration_text": (localized.get("duration") or {}).get("text"),
            }))
        # Google streams elements in no guaranteed order, and callers almost
        # always want the fastest pair first.
        results.sort(
            key=lambda item: (
                item.get("duration_seconds") if item.get("duration_seconds") is not None else float("inf"),
                item.get("origin_index") or 0,
                item.get("destination_index") or 0,
            )
        )
        return _json_result({"mode": _API_TO_DIRECTION_MODE.get(api_mode, api_mode.lower()), "results": results})
    except Exception as exc:  # noqa: BLE001 - handlers return errors, never raise
        return _json_error(exc)


def _matrix_waypoints(args: dict[str, Any], key: str) -> list[dict[str, Any]]:
    """Read a list of addresses or place IDs into Routes waypoints."""
    values = _as_address_list(args, key)
    if not values:
        raise ValidationError(key, "at least one address or place ID is required")
    if len(values) > _MAX_MATRIX_WAYPOINTS:
        raise ValidationError(key, f"must be at most {_MAX_MATRIX_WAYPOINTS}")
    waypoints = []
    for value in values:
        if value.startswith("places/") or value.startswith("place_id:"):
            waypoints.append({"placeId": _normalize_place_id(value.removeprefix("place_id:"))})
        else:
            waypoints.append({"address": value})
    return waypoints


def _label_at(labels: list[str], index: Any) -> str | None:
    if isinstance(index, int) and 0 <= index < len(labels):
        return labels[index]
    return None


def goplaces_reverse_geocode(args: dict[str, Any], **_: Any) -> str:
    """Identify what sits at a coordinate.

    Places has no true reverse geocoder, so this is a distance-ranked nearby
    search over a deliberately tight radius: the nearest places to the point are
    the best available answer to "what is here".
    """
    try:
        client = _get_client()
        lat = _as_float(args, "lat")
        lng = _as_float(args, "lng")
        if lat is None or lng is None:
            raise ValidationError("location", "lat and lng are required")
        radius_m = _as_float(args, "radius_m", 50.0)
        if radius_m is None or radius_m <= 0 or radius_m > _MAX_REVERSE_GEOCODE_RADIUS_M:
            raise ValidationError("radius_m", f"must be > 0 and <= {int(_MAX_REVERSE_GEOCODE_RADIUS_M)}")
        body: dict[str, Any] = {
            "locationRestriction": _circle_payload(lat, lng, radius_m, "location"),
            "maxResultCount": _limit(args, default=5),
            "rankPreference": "DISTANCE",
        }
        _add_locale(body, args)
        included = _as_string_list(args, "included_types")
        if included:
            body["includedTypes"] = included
        field_mask = _place_field_mask(
            _detail_level(args),
            prefix="places.",
            atmosphere=_as_bool(args, "include_atmosphere"),
            ev=_as_bool(args, "include_ev"),
        )
        payload = client.request("POST", client.places_url("/places:searchNearby"), body, field_mask)
        origin = {"lat": lat, "lng": lng}
        results = []
        for place in payload.get("places", []):
            summary = _map_place_summary(place)
            location = summary.get("location")
            if location:
                summary["meters_from_point"] = round(
                    _haversine_meters(origin, location), 1
                )
            results.append(summary)
        results.sort(key=lambda item: item.get("meters_from_point") if item.get("meters_from_point") is not None else float("inf"))
        return _json_result({"query_location": origin, "results": results})
    except Exception as exc:  # noqa: BLE001 - handlers return errors, never raise
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


def _detail_level(args: dict[str, Any]) -> str:
    """Read the requested field tier, defaulting to the historical full mask."""
    level = _as_str(args, "detail_level", _TIER_FULL).lower() or _TIER_FULL
    if level not in _DETAIL_LEVELS:
        raise ValidationError("detail_level", f"must be one of {', '.join(_DETAIL_LEVELS)}")
    return level


def _place_field_mask(
    level: str,
    *,
    prefix: str,
    atmosphere: bool = False,
    ev: bool = False,
    extra: Iterable[str] = (),
) -> str:
    """Build a field mask for the requested tier.

    ``prefix`` is ``"places."`` for the search endpoints and ``""`` for Place
    Details, which names the same fields without the collection prefix.

    Every name passed here is a *place* field and gets the prefix. Response-root
    tokens such as ``nextPageToken`` and ``routingSummaries`` must be appended by
    the caller instead; prefixing those is an API error.
    """
    fields: list[str] = list(_IDS_FIELDS)
    if level in (_TIER_BASIC, _TIER_FULL):
        fields.extend(_BASIC_FIELDS)
    if level == _TIER_FULL:
        fields.extend(_FULL_FIELDS)
    if atmosphere:
        fields.extend(_ATMOSPHERE_FIELDS)
    if ev:
        fields.extend(_EV_FIELDS)
    fields.extend(extra)
    seen: dict[str, None] = {}
    for field in fields:
        seen.setdefault(f"{prefix}{field}", None)
    return ",".join(seen)


def _maps_search_url(place_id: str | None, name: str | None, location: dict[str, float] | None) -> str | None:
    """Build a Google Maps link with no API call.

    Google's Maps URLs scheme requires ``query`` even when ``query_place_id`` is
    supplied, so a place ID alone is not enough.
    """
    if location:
        query = f"{location['lat']},{location['lng']}"
    elif name:
        query = name
    else:
        return None
    params = {"api": "1", "query": query}
    if place_id:
        params["query_place_id"] = place_id
    return _MAPS_SEARCH_URL + "?" + urllib.parse.urlencode(params)


def _maps_directions_url(
    origin: str,
    destination: str,
    *,
    origin_place_id: str = "",
    destination_place_id: str = "",
    travel_mode: str = "",
) -> str | None:
    """Build a Google Maps directions link with no API call."""
    if not origin or not destination:
        return None
    params = {"api": "1", "origin": origin, "destination": destination}
    if origin_place_id:
        params["origin_place_id"] = origin_place_id
    if destination_place_id:
        params["destination_place_id"] = destination_place_id
    mode = _API_TO_MAPS_URL_MODE.get(travel_mode)
    if mode:
        params["travelmode"] = mode
    return _MAPS_DIRECTIONS_URL + "?" + urllib.parse.urlencode(params)


def _routing_parameters(args: dict[str, Any]) -> dict[str, Any] | None:
    """Attach a routing origin so Google returns distance/duration per result.

    ``origin`` here is a bare LatLng, unlike the wrapped waypoints the Routes
    API takes.
    """
    lat = _as_float(args, "origin_lat")
    lng = _as_float(args, "origin_lng")
    if lat is None and lng is None:
        return None
    if lat is None or lng is None:
        raise ValidationError("origin", "origin_lat and origin_lng are required together")
    _validate_lat_lng(lat, lng, "origin")
    mode = _as_str(args, "origin_mode", "drive")
    api_mode = _ROUTE_MODE_TO_API.get(mode.strip().lower())
    if not api_mode:
        raise ValidationError("origin_mode", "must be drive, walk, bicycle, or two_wheeler")
    if api_mode == "TRANSIT":
        raise ValidationError("origin_mode", "Places routing does not support transit")
    return {
        "origin": {"latitude": lat, "longitude": lng},
        "travelMode": api_mode,
    }


def _routing_summary_legs(summary: Any) -> list[dict[str, Any]]:
    if not isinstance(summary, dict):
        return []
    legs = summary.get("legs")
    return [leg for leg in legs if isinstance(leg, dict)] if isinstance(legs, list) else []


def _apply_routing_summary(
    summary: Any,
    target: dict[str, Any],
    *,
    duration_key: str = "duration_seconds",
    distance_key: str = "distance_meters",
) -> None:
    """Fold an aligned routingSummaries entry into a mapped place.

    A routing origin yields one leg (origin -> place), so the totals are travel
    from that origin. Search-along-route yields two (origin -> place, place ->
    destination), so the totals are the whole trip via that stop — a different
    quantity, which is why the caller renames the keys.
    """
    legs = _routing_summary_legs(summary)
    if not legs:
        return
    durations = [_parse_duration_seconds(leg.get("duration")) for leg in legs]
    distances = [leg.get("distanceMeters") for leg in legs]
    if any(value is not None for value in durations):
        target[duration_key] = sum(value for value in durations if value is not None)
    if any(value is not None for value in distances):
        target[distance_key] = sum(value for value in distances if value is not None)
    directions_uri = summary.get("directionsUri") if isinstance(summary, dict) else None
    if directions_uri:
        target["directions_url"] = directions_uri


def _attach_routing_summaries(payload: dict[str, Any], results: list[dict[str, Any]]) -> None:
    """Zip routingSummaries onto results; Google guarantees index alignment."""
    summaries = payload.get("routingSummaries")
    if not isinstance(summaries, list):
        return
    for result, summary in zip(results, summaries):
        _apply_routing_summary(summary, result)


def _ev_options(args: dict[str, Any]) -> dict[str, Any] | None:
    """Build the EV filter Google applies to a text search."""
    connectors = [value.strip().upper() for value in _as_string_list(args, "ev_connector_types")]
    for connector in connectors:
        if not connector.startswith("EV_CONNECTOR_TYPE_"):
            raise ValidationError(
                "ev_connector_types",
                "must be Google EV_CONNECTOR_TYPE_* values, e.g. EV_CONNECTOR_TYPE_CCS_COMBO_2",
            )
    minimum_kw = _as_float(args, "ev_min_charge_rate_kw")
    if minimum_kw is not None and minimum_kw <= 0:
        raise ValidationError("ev_min_charge_rate_kw", "must be > 0")
    options: dict[str, Any] = {}
    if connectors:
        options["connectorTypes"] = connectors
    if minimum_kw is not None:
        options["minimumChargingRateKw"] = minimum_kw
    return options or None


def _normalize_place_id(place_id: str) -> str:
    place_id = place_id.strip().removeprefix("/").removeprefix("places/")
    if not place_id or "/" in place_id:
        raise ValidationError("place_id", "must be a place ID or places/{place_id}")
    return place_id


def _quote_path_segment(value: str) -> str:
    return urllib.parse.quote(value, safe="")


def _map_search_response(payload: dict[str, Any]) -> dict[str, Any]:
    results = [_map_place_summary(place) for place in payload.get("places", [])]
    _attach_routing_summaries(payload, results)
    return {
        "results": results,
        "next_page_token": payload.get("nextPageToken"),
    }


def _display_name(place: dict[str, Any]) -> str | None:
    """Return None when Google sent no name, so _strip_none drops the key.

    An empty string would read as "this place has no name" rather than "a
    cheaper field tier did not ask for one".
    """
    display = place.get("displayName")
    if isinstance(display, dict):
        return display.get("text") or None
    return None


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
    place_id = place.get("id")
    name = _display_name(place)
    location = _map_location(place.get("location"))
    summary = {
        "place_id": place_id,
        "name": name,
        "address": place.get("formattedAddress"),
        "location": location,
        "rating": place.get("rating"),
        "user_rating_count": place.get("userRatingCount"),
        "price_level": _map_price_level(place.get("priceLevel")),
        "price_range": _map_price_range(place.get("priceRange")),
        "primary_type": _localized_name(place.get("primaryTypeDisplayName")),
        "types": place.get("types") or None,
        "open_now": _open_now(place),
        "business_status": place.get("businessStatus"),
        "utc_offset_minutes": place.get("utcOffsetMinutes"),
        # Prefer Google's own deep link; fall back to a link we can build for free.
        "maps_url": place.get("googleMapsUri") or _maps_search_url(place_id, name, location),
    }
    summary.update(_map_atmosphere(place))
    ev_options = _map_ev_charge_options(place.get("evChargeOptions"))
    if ev_options:
        summary["ev_charge_options"] = ev_options
    return summary


def _localized_name(value: Any) -> str | None:
    if isinstance(value, dict):
        return value.get("text")
    return None


def _map_price_range(value: Any) -> dict[str, Any] | None:
    """Map Google's startPrice/endPrice Money pair into a flat range."""
    if not isinstance(value, dict):
        return None
    start = value.get("startPrice") if isinstance(value.get("startPrice"), dict) else {}
    end = value.get("endPrice") if isinstance(value.get("endPrice"), dict) else {}
    currency = start.get("currencyCode") or end.get("currencyCode")
    if not (start or end):
        return None
    return _strip_none({
        "currency": currency,
        "start_units": _as_optional_int(start.get("units")),
        "end_units": _as_optional_int(end.get("units")),
    }) or None


def _as_optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


_ATMOSPHERE_BOOLEANS = {
    "dineIn": "dine_in",
    "takeout": "takeout",
    "delivery": "delivery",
    "reservable": "reservable",
}


def _map_atmosphere(place: dict[str, Any]) -> dict[str, Any]:
    """Map the opt-in Atmosphere-tier fields, omitting any Google left out."""
    mapped: dict[str, Any] = {}
    for source, target in _ATMOSPHERE_BOOLEANS.items():
        if source in place:
            mapped[target] = bool(place[source])
    summary = place.get("editorialSummary")
    if isinstance(summary, dict) and summary.get("text"):
        mapped["editorial_summary"] = summary.get("text")
    for source, target in (("accessibilityOptions", "accessibility"), ("parkingOptions", "parking")):
        options = place.get(source)
        if isinstance(options, dict):
            enabled = sorted(key for key, value in options.items() if value is True)
            if enabled:
                mapped[target] = enabled
    return mapped


def _map_ev_charge_options(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    connectors = []
    for item in value.get("connectorAggregation") or []:
        if not isinstance(item, dict):
            continue
        connectors.append(_strip_none({
            "type": item.get("type"),
            "max_charge_rate_kw": item.get("maxChargeRateKw"),
            "count": item.get("count"),
            "available_count": item.get("availableCount"),
            "out_of_service_count": item.get("outOfServiceCount"),
        }))
    mapped = _strip_none({
        "connector_count": value.get("connectorCount"),
        "connectors": connectors or None,
    })
    return mapped or None


def _map_resolved_location(place: dict[str, Any]) -> dict[str, Any]:
    return {
        "place_id": place.get("id"),
        "name": _display_name(place),
        "address": place.get("formattedAddress"),
        "location": _map_location(place.get("location")),
        "types": place.get("types") or None,
    }


def _map_place_details(place: dict[str, Any]) -> dict[str, Any]:
    regular_hours = place.get("regularOpeningHours") if isinstance(place.get("regularOpeningHours"), dict) else {}
    details = _map_place_summary(place)
    details.update({
        "phone": place.get("nationalPhoneNumber"),
        "international_phone": place.get("internationalPhoneNumber"),
        "website": place.get("websiteUri"),
        "hours": regular_hours.get("weekdayDescriptions") or None,
        "reviews": [_map_review(review) for review in place.get("reviews", [])] or None,
        "photos": [_map_photo(photo) for photo in place.get("photos", [])] or None,
    })
    return details


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
        mode = "drive"
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

    intermediates = _intermediate_waypoints(args)
    if intermediates:
        body["intermediates"] = intermediates

    if _as_bool(args, "alternatives"):
        # Google silently ignores alternatives when intermediates are present,
        # so say so rather than returning one route with no explanation.
        if intermediates:
            raise ValidationError("alternatives", "cannot combine with waypoints")
        body["computeAlternativeRoutes"] = True

    routing_preference = _as_str(args, "routing_preference").lower()
    if routing_preference:
        if routing_preference not in _ROUTING_PREFERENCES:
            raise ValidationError("routing_preference", f"must be one of {', '.join(_ROUTING_PREFERENCES)}")
        if api_mode not in {"DRIVE", "TWO_WHEELER"}:
            raise ValidationError("routing_preference", "requires drive mode")
        body["routingPreference"] = _ROUTING_PREFERENCES[routing_preference]
    elif api_mode == "DRIVE" and departure:
        # A departure time does nothing on a traffic-unaware route, which makes
        # "leaving at 8am" look supported while silently returning static times.
        body["routingPreference"] = "TRAFFIC_AWARE"

    transit_preferences = _transit_preferences(args, api_mode)
    if transit_preferences:
        body["transitPreferences"] = transit_preferences
    return body


def _intermediate_waypoints(args: dict[str, Any]) -> list[dict[str, Any]]:
    """Map free-form stop text into Routes intermediate waypoints."""
    stops = _as_address_list(args, "waypoints")
    if not stops:
        return []
    if len(stops) > _MAX_INTERMEDIATES:
        raise ValidationError("waypoints", f"must be at most {_MAX_INTERMEDIATES}")
    return [{"address": stop} for stop in stops]


def _transit_preferences(args: dict[str, Any], api_mode: str) -> dict[str, Any] | None:
    modes = [value.strip().upper() for value in _as_string_list(args, "transit_modes")]
    preference = _as_str(args, "transit_routing_preference").lower()
    if not modes and not preference:
        return None
    if api_mode != "TRANSIT":
        raise ValidationError("transit_modes", "requires transit mode")
    preferences: dict[str, Any] = {}
    for mode in modes:
        if mode not in _TRANSIT_TRAVEL_MODES:
            raise ValidationError("transit_modes", f"must be one of {', '.join(sorted(_TRANSIT_TRAVEL_MODES))}")
    if modes:
        preferences["allowedTravelModes"] = modes
    if preference:
        if preference not in _TRANSIT_ROUTING_PREFERENCES:
            raise ValidationError(
                "transit_routing_preference",
                f"must be one of {', '.join(_TRANSIT_ROUTING_PREFERENCES)}",
            )
        preferences["routingPreference"] = _TRANSIT_ROUTING_PREFERENCES[preference]
    return preferences


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
    routes = payload.get("routes") or []
    if not routes:
        raise GoogleAPIError(None, "no routes returned", payload)
    mapped = [_map_one_route(route, api_mode, args, include_steps, payload) for route in routes]
    primary = mapped[0]
    if len(mapped) > 1:
        primary["alternatives"] = mapped[1:]
    return primary


def _map_one_route(
    route: Any,
    api_mode: str,
    args: dict[str, Any],
    include_steps: bool,
    payload: dict[str, Any],
) -> dict[str, Any]:
    if not isinstance(route, dict):
        raise GoogleAPIError(None, "unexpected route response", payload)
    legs = route.get("legs") or []
    if not legs:
        raise GoogleAPIError(None, "no directions returned", payload)
    start = _waypoint_label(args, "from")
    end = _waypoint_label(args, "to")
    # A multi-leg route is one journey through stops; report the whole trip.
    totals = _leg_totals(legs)
    # Route-level text already spans every leg. Fall back to legs[0] only when
    # there is one leg, since its text is then the whole trip by definition.
    localized = route.get("localizedValues") or {}
    if not localized and len(legs) == 1:
        localized = legs[0].get("localizedValues") or {}
    scheduled = _transit_schedule(legs)
    response = {
        "mode": _API_TO_DIRECTION_MODE.get(api_mode, api_mode.lower()),
        "summary": route.get("description"),
        "start_address": start,
        "end_address": end,
        "distance_text": (localized.get("distance") or {}).get("text"),
        "distance_meters": route.get("distanceMeters") or totals["distance_meters"],
        "duration_text": (localized.get("duration") or {}).get("text"),
        "duration_seconds": _parse_duration_seconds(route.get("duration")) or totals["duration_seconds"],
        # A transit route knows its own schedule; anything else can only echo
        # the time that was asked for.
        "departure_time": scheduled["departure_time"] or _as_str(args, "departure_time") or None,
        "arrival_time": scheduled["arrival_time"] or _as_str(args, "arrival_time") or None,
        "warnings": route.get("warnings") or [],
        "maps_url": _maps_directions_url(
            start,
            end,
            origin_place_id=_place_id_arg(args, "from"),
            destination_place_id=_place_id_arg(args, "to"),
            travel_mode=api_mode,
        ),
    }
    if len(legs) > 1:
        response["legs"] = [
            {
                "distance_meters": leg.get("distanceMeters"),
                "duration_seconds": _parse_duration_seconds(leg.get("duration")),
                "distance_text": ((leg.get("localizedValues") or {}).get("distance") or {}).get("text"),
                "duration_text": ((leg.get("localizedValues") or {}).get("duration") or {}).get("text"),
            }
            for leg in legs
        ]
    if include_steps:
        response["steps"] = [
            _map_directions_step(step) for leg in legs for step in leg.get("steps", [])
        ]
    return response


def _transit_schedule(legs: list[Any]) -> dict[str, str | None]:
    """Pull a transit route's real departure and arrival times off its steps.

    The Routes API carries no route-level times, so the first boarding and the
    last alighting are the closest thing the journey has to a schedule. Walking
    steps have no times at all, which is why a mode with none reports neither.
    """
    times: list[tuple[str | None, str | None]] = []
    for leg in legs:
        if not isinstance(leg, dict):
            continue
        for step in leg.get("steps") or []:
            if not isinstance(step, dict):
                continue
            details = step.get("transitDetails")
            if not isinstance(details, dict):
                continue
            stops = details.get("stopDetails")
            if not isinstance(stops, dict):
                continue
            times.append((stops.get("departureTime"), stops.get("arrivalTime")))
    if not times:
        return {"departure_time": None, "arrival_time": None}
    return {"departure_time": times[0][0], "arrival_time": times[-1][1]}


def _leg_totals(legs: list[Any]) -> dict[str, int | None]:
    distances = [leg.get("distanceMeters") for leg in legs if isinstance(leg, dict)]
    durations = [_parse_duration_seconds(leg.get("duration")) for leg in legs if isinstance(leg, dict)]
    return {
        "distance_meters": sum(value for value in distances if value is not None) or None,
        "duration_seconds": sum(value for value in durations if value is not None) or None,
    }


def _place_id_arg(args: dict[str, Any], prefix: str) -> str:
    raw = _as_str(args, f"{prefix}_place_id")
    return _normalize_place_id(raw) if raw else ""


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
        "transit": _map_transit_details(step.get("transitDetails")),
    }


def _map_transit_details(details: Any) -> dict[str, Any] | None:
    """Map the line, headsign, stops, and times that make a transit step usable.

    Without these a transit step is just "Bus towards somewhere", which is not
    enough for anyone to actually catch it.
    """
    if not isinstance(details, dict):
        return None
    line = details.get("transitLine") if isinstance(details.get("transitLine"), dict) else {}
    vehicle = line.get("vehicle") if isinstance(line.get("vehicle"), dict) else {}
    stops = details.get("stopDetails") if isinstance(details.get("stopDetails"), dict) else {}
    localized = details.get("localizedValues") if isinstance(details.get("localizedValues"), dict) else {}
    agencies = [
        agency.get("name")
        for agency in (line.get("agencies") or [])
        if isinstance(agency, dict) and agency.get("name")
    ]
    mapped = {
        "line": line.get("name"),
        "line_short": line.get("nameShort"),
        "agencies": agencies or None,
        "vehicle_type": vehicle.get("type"),
        "headsign": details.get("headsign"),
        "headway_seconds": _parse_duration_seconds(details.get("headway")),
        "stop_count": details.get("stopCount"),
        "trip_short_text": details.get("tripShortText"),
        "departure_stop": _transit_stop_name(stops.get("departureStop")),
        "arrival_stop": _transit_stop_name(stops.get("arrivalStop")),
        "departure_time": stops.get("departureTime"),
        "arrival_time": stops.get("arrivalTime"),
        "departure_time_text": _localized_time(localized.get("departureTime")),
        "arrival_time_text": _localized_time(localized.get("arrivalTime")),
    }
    stripped = _strip_none(mapped)
    return stripped or None


def _transit_stop_name(stop: Any) -> str | None:
    return stop.get("name") if isinstance(stop, dict) else None


def _localized_time(value: Any) -> str | None:
    if not isinstance(value, dict):
        return None
    time_value = value.get("time")
    if isinstance(time_value, dict):
        return time_value.get("text")
    return None


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


def _haversine_meters(a: dict[str, float], b: dict[str, float]) -> float:
    """Great-circle distance between two lat/lng points, in meters."""
    earth_radius_m = 6_371_008.8
    lat1 = math.radians(a["lat"])
    lat2 = math.radians(b["lat"])
    delta_lat = lat2 - lat1
    delta_lng = math.radians(b["lng"] - a["lng"])
    h = math.sin(delta_lat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(delta_lng / 2) ** 2
    return 2 * earth_radius_m * math.asin(math.sqrt(h))
