"""Tool schemas exposed to the Hermes model for the goplaces plugin."""

from __future__ import annotations

_LOCATION_BIAS_PROPERTIES = {
    "lat": {
        "type": "number",
        "description": "Latitude for a circular location bias/restriction.",
    },
    "lng": {
        "type": "number",
        "description": "Longitude for a circular location bias/restriction.",
    },
    "radius_m": {
        "type": "number",
        "description": "Radius in meters for the circular location bias/restriction.",
    },
}

_LOCALE_PROPERTIES = {
    "language": {
        "type": "string",
        "description": "Optional BCP-47 language code, for example 'en' or 'en-US'.",
    },
    "region": {
        "type": "string",
        "description": "Optional CLDR region code, for example 'US' or 'DE'.",
    },
}

GOPLACES_SEARCH = {
    "name": "goplaces_search",
    "description": (
        "Search Google Places by free-form text. Use for finding businesses, "
        "landmarks, venues, restaurants, shops, attractions, or services. "
        "Supports filters for open now, rating, price, type, pagination, and "
        "optional circular location bias. Returns compact place summaries and "
        "a next_page_token when Google provides one."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search text, e.g. 'coffee', 'sushi near Bryant Park', or 'EV charging'."},
            "limit": {"type": "integer", "minimum": 1, "maximum": 20, "default": 10, "description": "Maximum number of results."},
            "page_token": {"type": "string", "description": "Google next_page_token from a previous goplaces_search response."},
            "keyword": {"type": "string", "description": "Extra keyword appended to the text query."},
            "included_type": {"type": "string", "description": "Optional Google place type filter, e.g. 'restaurant', 'cafe', 'park'."},
            "open_now": {"type": "boolean", "description": "When true, request places currently open."},
            "min_rating": {"type": "number", "minimum": 0, "maximum": 5, "description": "Minimum star rating from 0 to 5."},
            "price_levels": {
                "type": "array",
                "items": {"type": "integer", "minimum": 0, "maximum": 4},
                "description": "Google price levels: 0 free, 1 inexpensive, 2 moderate, 3 expensive, 4 very expensive.",
            },
            **_LOCATION_BIAS_PROPERTIES,
            **_LOCALE_PROPERTIES,
        },
        "required": ["query"],
    },
}

GOPLACES_NEARBY = {
    "name": "goplaces_nearby",
    "description": (
        "Search Google Places near a latitude/longitude within a radius. "
        "Use when the user gives coordinates or you already resolved a location. "
        "Returns compact place summaries."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "lat": _LOCATION_BIAS_PROPERTIES["lat"],
            "lng": _LOCATION_BIAS_PROPERTIES["lng"],
            "radius_m": _LOCATION_BIAS_PROPERTIES["radius_m"],
            "limit": {"type": "integer", "minimum": 1, "maximum": 20, "default": 10, "description": "Maximum number of nearby results."},
            "included_types": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Included Google place types, e.g. ['cafe'] or ['restaurant', 'bar'].",
            },
            "excluded_types": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Excluded Google place types.",
            },
            **_LOCALE_PROPERTIES,
        },
        "required": ["lat", "lng", "radius_m"],
    },
}

GOPLACES_AUTOCOMPLETE = {
    "name": "goplaces_autocomplete",
    "description": (
        "Autocomplete a partial place or query string using Google Places. "
        "Use to turn partial user input into place/query suggestions and place IDs."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "input": {"type": "string", "description": "Partial place or query text, e.g. 'cof' or 'Space Nee'."},
            "limit": {"type": "integer", "minimum": 1, "maximum": 20, "default": 5, "description": "Maximum suggestions to return."},
            "session_token": {"type": "string", "description": "Optional Google session token for billing consistency."},
            **_LOCATION_BIAS_PROPERTIES,
            **_LOCALE_PROPERTIES,
        },
        "required": ["input"],
    },
}

GOPLACES_DETAILS = {
    "name": "goplaces_details",
    "description": (
        "Fetch rich Google Place Details by place ID. Use after search, nearby, "
        "autocomplete, or resolve when the user needs phone, website, hours, "
        "business status, reviews, or photos. Reviews and photos are opt-in because "
        "they are heavier fields."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "place_id": {"type": "string", "description": "Google place ID, with or without a 'places/' prefix."},
            "include_reviews": {"type": "boolean", "default": False, "description": "Include Google reviews in the details response."},
            "include_photos": {"type": "boolean", "default": False, "description": "Include photo metadata in the details response."},
            **_LOCALE_PROPERTIES,
        },
        "required": ["place_id"],
    },
}

GOPLACES_PHOTO = {
    "name": "goplaces_photo",
    "description": (
        "Resolve a Google Places photo resource name into a photo media URL. "
        "Use with photo names returned by goplaces_details(include_photos=true)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Photo resource name like 'places/PLACE_ID/photos/PHOTO_ID'."},
            "max_width_px": {"type": "integer", "minimum": 1, "maximum": 4800, "description": "Maximum photo width in pixels. Required when max_height_px is omitted."},
            "max_height_px": {"type": "integer", "minimum": 1, "maximum": 4800, "description": "Maximum photo height in pixels. Required when max_width_px is omitted."},
        },
        "required": ["name"],
    },
}

GOPLACES_RESOLVE = {
    "name": "goplaces_resolve",
    "description": (
        "Resolve a free-form location string into candidate Google places with "
        "coordinates and place IDs. Use before nearby searches or directions when "
        "you need structured candidates for an address, landmark, city, or venue."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "location_text": {"type": "string", "description": "Free-form location text, e.g. 'Riverside Park, New York'."},
            "limit": {"type": "integer", "minimum": 1, "maximum": 10, "default": 5, "description": "Maximum candidate locations."},
            **_LOCALE_PROPERTIES,
        },
        "required": ["location_text"],
    },
}

GOPLACES_DIRECTIONS = {
    "name": "goplaces_directions",
    "description": (
        "Get directions, distance, duration, warnings, and optional steps between "
        "two locations using Google Routes. Each endpoint can be specified by text, "
        "place ID, or latitude/longitude. Supports walk, drive, bicycle, transit, "
        "units, departure/arrival time, drive avoid modifiers, and one compare mode."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "from_text": {"type": "string", "description": "Origin as an address or place name. Use only one origin form."},
            "to_text": {"type": "string", "description": "Destination as an address or place name. Use only one destination form."},
            "from_place_id": {"type": "string", "description": "Origin Google place ID. Use only one origin form."},
            "to_place_id": {"type": "string", "description": "Destination Google place ID. Use only one destination form."},
            "from_lat": {"type": "number", "description": "Origin latitude. Must be paired with from_lng."},
            "from_lng": {"type": "number", "description": "Origin longitude. Must be paired with from_lat."},
            "to_lat": {"type": "number", "description": "Destination latitude. Must be paired with to_lng."},
            "to_lng": {"type": "number", "description": "Destination longitude. Must be paired with to_lat."},
            "mode": {"type": "string", "enum": ["walk", "drive", "bicycle", "transit"], "default": "walk", "description": "Primary travel mode."},
            "compare_mode": {"type": "string", "enum": ["walk", "drive", "bicycle", "transit"], "description": "Optional second travel mode to compare with mode."},
            "include_steps": {"type": "boolean", "default": False, "description": "Include turn-by-turn steps in the response."},
            "units": {"type": "string", "enum": ["metric", "imperial"], "default": "metric", "description": "Localized distance units."},
            "avoid_tolls": {"type": "boolean", "description": "Avoid toll roads. Requires drive mode for the affected route."},
            "avoid_highways": {"type": "boolean", "description": "Avoid highways. Requires drive mode for the affected route."},
            "avoid_ferries": {"type": "boolean", "description": "Avoid ferries. Requires drive mode for the affected route."},
            "departure_time": {"type": "string", "description": "Optional RFC3339 departure time, e.g. 2030-05-10T18:57:00-03:00. Mutually exclusive with arrival_time."},
            "arrival_time": {"type": "string", "description": "Optional RFC3339 transit arrival time. Requires transit mode and is mutually exclusive with departure_time."},
            **_LOCALE_PROPERTIES,
        },
    },
}

GOPLACES_ROUTE_SEARCH = {
    "name": "goplaces_route_search",
    "description": (
        "Search for places along a route between two text locations. The tool "
        "computes a Google Routes polyline, samples waypoints, searches near each "
        "waypoint, and de-duplicates places. Use for requests like 'coffee stops "
        "between Seattle and Portland'."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Place search text to find along the route, e.g. 'coffee' or 'EV charging'."},
            "from_text": {"type": "string", "description": "Origin address or place name."},
            "to_text": {"type": "string", "description": "Destination address or place name."},
            "mode": {"type": "string", "enum": ["drive", "walk", "bicycle", "two_wheeler", "transit"], "default": "drive", "description": "Route travel mode."},
            "radius_m": {"type": "number", "default": 1000, "description": "Search radius around each sampled waypoint."},
            "max_waypoints": {"type": "integer", "minimum": 1, "maximum": 20, "default": 5, "description": "Number of route waypoints to sample."},
            "limit": {"type": "integer", "minimum": 1, "maximum": 20, "default": 5, "description": "Max results to request per waypoint before de-duplication."},
            **_LOCALE_PROPERTIES,
        },
        "required": ["query", "from_text", "to_text"],
    },
}
