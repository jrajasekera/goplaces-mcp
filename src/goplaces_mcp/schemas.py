"""Tool schemas exposed to MCP hosts for the goplaces plugin."""

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

_DETAIL_PROPERTIES = {
    "detail_level": {
        "type": "string",
        "enum": ["ids", "basic", "full"],
        "default": "full",
        "description": (
            "Field tier to request. Google bills at the most expensive tier in the "
            "request, so prefer the cheapest that answers the question: 'ids' returns "
            "place IDs only, 'basic' adds name, address, location, type, and a Maps "
            "link, and 'full' adds rating, price, hours, phone, and website."
        ),
    },
    "include_atmosphere": {
        "type": "boolean",
        "default": False,
        "description": (
            "Include the most expensive tier: editorial summary, dine-in/takeout/"
            "delivery/reservable, accessibility, and parking options."
        ),
    },
    "include_ev": {
        "type": "boolean",
        "default": False,
        "description": "Include EV charging connectors, charge rates, and availability.",
    },
}

_EV_FILTER_PROPERTIES = {
    "ev_connector_types": {
        "type": "array",
        "items": {"type": "string"},
        "description": (
            "Keep only places supporting one of these Google connector types, e.g. "
            "['EV_CONNECTOR_TYPE_CCS_COMBO_2', 'EV_CONNECTOR_TYPE_NACS', "
            "'EV_CONNECTOR_TYPE_J1772', 'EV_CONNECTOR_TYPE_TESLA', "
            "'EV_CONNECTOR_TYPE_CHADEMO', 'EV_CONNECTOR_TYPE_TYPE_2']."
        ),
    },
    "ev_min_charge_rate_kw": {
        "type": "number",
        "exclusiveMinimum": 0,
        "description": "Keep only charging stations at or above this rate in kilowatts.",
    },
}

_ORIGIN_PROPERTIES = {
    "origin_lat": {
        "type": "number",
        "description": (
            "Latitude to measure travel from. With origin_lng, each result gains "
            "distance_meters and duration_seconds, avoiding a directions call per result."
        ),
    },
    "origin_lng": {
        "type": "number",
        "description": "Longitude to measure travel from. Must be paired with origin_lat.",
    },
    "origin_mode": {
        "type": "string",
        "enum": ["drive", "walk", "bicycle", "two_wheeler"],
        "default": "drive",
        "description": "Travel mode for origin distances. Google does not support transit here.",
    },
}

# Responses are stripped of null fields before they are returned, so these
# schemas keep "required" minimal and stay open to fields a tier did not request.
_LOCATION_SCHEMA = {
    "type": "object",
    "properties": {"lat": {"type": "number"}, "lng": {"type": "number"}},
}

_PLACE_SCHEMA = {
    "type": "object",
    "description": "A place summary. Fields present depend on detail_level.",
    "properties": {
        "place_id": {"type": "string", "description": "Pass to goplaces_details or as a directions endpoint."},
        "name": {"type": "string"},
        "address": {"type": "string"},
        "location": _LOCATION_SCHEMA,
        "rating": {"type": "number"},
        "user_rating_count": {"type": "integer"},
        "price_level": {"type": "integer", "description": "0 free to 4 very expensive."},
        "price_range": {"type": "object"},
        "primary_type": {"type": "string"},
        "types": {"type": "array", "items": {"type": "string"}},
        "open_now": {"type": "boolean"},
        "business_status": {"type": "string"},
        "utc_offset_minutes": {"type": "integer"},
        "maps_url": {"type": "string", "description": "Google Maps link for the place."},
        "editorial_summary": {"type": "string"},
        "dine_in": {"type": "boolean"},
        "takeout": {"type": "boolean"},
        "delivery": {"type": "boolean"},
        "reservable": {"type": "boolean"},
        "accessibility": {"type": "array", "items": {"type": "string"}},
        "parking": {"type": "array", "items": {"type": "string"}},
        "ev_charge_options": {"type": "object"},
        "distance_meters": {"type": "number", "description": "Present when a routing origin was supplied."},
        "duration_seconds": {"type": "integer", "description": "Present when a routing origin was supplied."},
        "directions_url": {"type": "string"},
    },
}

_ERROR_SCHEMA = {
    "type": "object",
    "description": "Present only on failure; the result is also flagged isError.",
    "properties": {
        "type": {"type": "string", "enum": ["validation", "google_api", "unknown_tool"]},
        "field": {"type": "string"},
        "message": {"type": "string"},
        "status": {"type": "integer"},
    },
}


def _results_output_schema(description: str, *, paged: bool = False) -> dict:
    properties = {
        "results": {"type": "array", "items": _PLACE_SCHEMA, "description": description},
        "error": _ERROR_SCHEMA,
    }
    if paged:
        properties["next_page_token"] = {
            "type": "string",
            "description": "Pass back as page_token for the next page.",
        }
    return {"type": "object", "properties": properties}


GOPLACES_SEARCH = {
    "name": "goplaces_search",
    "title": "Search places",
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
            "page_token": {"type": "string", "description": (
                    "Google next_page_token from a previous goplaces_search response. "
                    "Every other argument, detail_level included, must match the "
                    "original request or Google rejects the page."
                )},
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
            **_ORIGIN_PROPERTIES,
            **_EV_FILTER_PROPERTIES,
            **_DETAIL_PROPERTIES,
            **_LOCALE_PROPERTIES,
        },
        "required": ["query"],
    },
    "output_schema": _results_output_schema("Matching places, best match first.", paged=True),
}

GOPLACES_NEARBY = {
    "name": "goplaces_nearby",
    "title": "Search near coordinates",
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
            "rank_by": {
                "type": "string",
                "enum": ["distance", "popularity"],
                "description": "Result ordering. Defaults to Google's popularity ranking.",
            },
            **_ORIGIN_PROPERTIES,
            **_DETAIL_PROPERTIES,
            **_LOCALE_PROPERTIES,
        },
        "required": ["lat", "lng", "radius_m"],
    },
    "output_schema": _results_output_schema("Places inside the radius."),
}

GOPLACES_AUTOCOMPLETE = {
    "name": "goplaces_autocomplete",
    "title": "Autocomplete a place",
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
    "output_schema": {
        "type": "object",
        "properties": {
            "suggestions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "kind": {"type": "string", "enum": ["place", "query"]},
                        "place_id": {"type": "string"},
                        "text": {"type": "string"},
                        "main_text": {"type": "string"},
                        "secondary_text": {"type": "string"},
                        "types": {"type": "array", "items": {"type": "string"}},
                        "distance_meters": {"type": "number"},
                    },
                },
            },
            "error": _ERROR_SCHEMA,
        },
    },
}

GOPLACES_DETAILS = {
    "name": "goplaces_details",
    "title": "Place details",
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
            **_DETAIL_PROPERTIES,
            **_LOCALE_PROPERTIES,
        },
        "required": ["place_id"],
    },
    "output_schema": {
        "type": "object",
        "properties": {
            **_PLACE_SCHEMA["properties"],
            "phone": {"type": "string"},
            "international_phone": {"type": "string"},
            "website": {"type": "string"},
            "hours": {"type": "array", "items": {"type": "string"}},
            "reviews": {
                "type": "array",
                "description": "Google requires showing the author attribution with each review.",
                "items": {"type": "object"},
            },
            "photos": {
                "type": "array",
                "description": "Google requires showing the author attributions with each photo.",
                "items": {"type": "object"},
            },
            "error": _ERROR_SCHEMA,
        },
    },
}

GOPLACES_PHOTO = {
    "name": "goplaces_photo",
    "title": "Resolve a place photo",
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
            "include_image": {
                "type": "boolean",
                "default": False,
                "description": (
                    "Also download the photo and return it as an image the model can "
                    "look at. Leave off when a URL is all the user needs."
                ),
            },
        },
        "required": ["name"],
    },
    "output_schema": {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "photo_uri": {"type": "string", "description": "Expiring Google-hosted photo URL."},
            "image_mime_type": {"type": "string"},
            "error": _ERROR_SCHEMA,
        },
    },
}

GOPLACES_RESOLVE = {
    "name": "goplaces_resolve",
    "title": "Resolve a location",
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
    "output_schema": {
        "type": "object",
        "properties": {
            "results": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "place_id": {"type": "string"},
                        "name": {"type": "string"},
                        "address": {"type": "string"},
                        "location": _LOCATION_SCHEMA,
                        "types": {"type": "array", "items": {"type": "string"}},
                    },
                },
            },
            "error": _ERROR_SCHEMA,
        },
    },
}

GOPLACES_DIRECTIONS = {
    "name": "goplaces_directions",
    "title": "Get directions",
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
            "waypoints": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Up to 25 intermediate stops, in order, as addresses or place names.",
            },
            "alternatives": {
                "type": "boolean",
                "default": False,
                "description": "Return alternative routes as well. Cannot be combined with waypoints.",
            },
            "routing_preference": {
                "type": "string",
                "enum": ["traffic_unaware", "traffic_aware", "traffic_aware_optimal"],
                "description": (
                    "Traffic handling for drive mode. Defaults to traffic_aware when a "
                    "departure_time is given, because a traffic-unaware route ignores it."
                ),
            },
            "transit_modes": {
                "type": "array",
                "items": {"type": "string", "enum": ["BUS", "SUBWAY", "TRAIN", "LIGHT_RAIL", "RAIL"]},
                "description": "Restrict transit to these vehicle types. Requires transit mode.",
            },
            "transit_routing_preference": {
                "type": "string",
                "enum": ["less_walking", "fewer_transfers"],
                "description": "Transit routing bias. Requires transit mode.",
            },
            **_LOCALE_PROPERTIES,
        },
    },
    "output_schema": {
        "type": "object",
        "properties": {
            "mode": {"type": "string"},
            "summary": {"type": "string"},
            "start_address": {"type": "string"},
            "end_address": {"type": "string"},
            "distance_meters": {"type": "number"},
            "distance_text": {"type": "string"},
            "duration_seconds": {"type": "integer"},
            "duration_text": {"type": "string"},
            "warnings": {"type": "array", "items": {"type": "string"}},
            "maps_url": {"type": "string"},
            "legs": {"type": "array", "items": {"type": "object"}, "description": "Present when waypoints were given."},
            "steps": {
                "type": "array",
                "description": "Present when include_steps is true. Transit steps carry a transit object with line, headsign, stops, and times.",
                "items": {"type": "object"},
            },
            "alternatives": {"type": "array", "items": {"type": "object"}},
            "routes": {
                "type": "array",
                "items": {"type": "object"},
                "description": "Present instead of a single route when compare_mode is used.",
            },
            "error": _ERROR_SCHEMA,
        },
    },
}

GOPLACES_ROUTE_SEARCH = {
    "name": "goplaces_route_search",
    "title": "Find stops along a route",
    "description": (
        "Find places along a route between two locations, ranked by how little "
        "they detour from it. Returns each result's detour time plus the direct "
        "route's distance and duration. Use for requests like 'coffee stops "
        "between Seattle and Portland' or 'EV charging on the way to Portland'."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Place search text to find along the route, e.g. 'coffee' or 'EV charging'."},
            "from_text": {"type": "string", "description": "Origin address or place name."},
            "to_text": {"type": "string", "description": "Destination address or place name."},
            "mode": {"type": "string", "enum": ["drive", "walk", "bicycle", "two_wheeler", "transit"], "default": "drive", "description": "Route travel mode."},
            "limit": {"type": "integer", "minimum": 1, "maximum": 20, "default": 5, "description": "Maximum stops to return."},
            "open_now": {"type": "boolean", "description": "When true, only places currently open."},
            "min_rating": {"type": "number", "minimum": 0, "maximum": 5, "description": "Minimum star rating from 0 to 5."},
            **_EV_FILTER_PROPERTIES,
            **_DETAIL_PROPERTIES,
            **_LOCALE_PROPERTIES,
        },
        "required": ["query", "from_text", "to_text"],
    },
    "output_schema": {
        "type": "object",
        "properties": {
            "route": {
                "type": "object",
                "description": "The direct route the detour times are measured against.",
                "properties": {
                    "from": {"type": "string"},
                    "to": {"type": "string"},
                    "mode": {"type": "string"},
                    "distance_meters": {"type": "number"},
                    "duration_seconds": {"type": "integer"},
                    "maps_url": {"type": "string"},
                },
            },
            "results": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        **_PLACE_SCHEMA["properties"],
                        "detour_seconds": {
                            "type": "integer",
                            "description": "Extra travel time versus going straight through.",
                        },
                        "trip_duration_seconds": {
                            "type": "integer",
                            "description": "Total travel time for the whole journey via this stop.",
                        },
                        "trip_distance_meters": {
                            "type": "number",
                            "description": "Total distance for the whole journey via this stop.",
                        },
                    },
                },
            },
            "error": _ERROR_SCHEMA,
        },
    },
}

GOPLACES_ROUTE_MATRIX = {
    "name": "goplaces_route_matrix",
    "title": "Compare travel times",
    "description": (
        "Rank destinations by travel time and distance from one or more origins "
        "in a single request. Use for 'which of these is closest to home' or "
        "'how long to each of these three offices' instead of one directions "
        "call per candidate. Results are sorted fastest first."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "origins": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Starting points as addresses, place names, or 'places/PLACE_ID' values.",
            },
            "destinations": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Destinations as addresses, place names, or 'places/PLACE_ID' values.",
            },
            "mode": {"type": "string", "enum": ["drive", "walk", "bicycle", "two_wheeler", "transit"], "default": "drive", "description": "Travel mode."},
            "units": {"type": "string", "enum": ["metric", "imperial"], "default": "metric", "description": "Localized distance units."},
            "departure_time": {"type": "string", "description": "Optional RFC3339 departure time. Enables traffic-aware drive times."},
            **_LOCALE_PROPERTIES,
        },
        "required": ["origins", "destinations"],
    },
    "output_schema": {
        "type": "object",
        "properties": {
            "mode": {"type": "string"},
            "results": {
                "type": "array",
                "description": "One entry per origin/destination pair, fastest first.",
                "items": {
                    "type": "object",
                    "properties": {
                        "origin": {"type": "string"},
                        "destination": {"type": "string"},
                        "origin_index": {"type": "integer"},
                        "destination_index": {"type": "integer"},
                        "condition": {"type": "string", "enum": ["ROUTE_EXISTS", "ROUTE_NOT_FOUND"]},
                        "distance_meters": {"type": "number"},
                        "distance_text": {"type": "string"},
                        "duration_seconds": {"type": "integer"},
                        "duration_text": {"type": "string"},
                    },
                },
            },
            "error": _ERROR_SCHEMA,
        },
    },
}

GOPLACES_REVERSE_GEOCODE = {
    "name": "goplaces_reverse_geocode",
    "title": "Identify a coordinate",
    "description": (
        "Identify what is at a latitude/longitude by finding the nearest places, "
        "closest first, with the distance from the point. Use for 'what is at "
        "these coordinates' or to label a GPS position."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "lat": {"type": "number", "description": "Latitude of the point to identify."},
            "lng": {"type": "number", "description": "Longitude of the point to identify."},
            "radius_m": {
                "type": "number",
                "minimum": 1,
                "maximum": 500,
                "default": 50,
                "description": "Search radius in meters. Keep it small; a wide radius describes the neighbourhood rather than the point.",
            },
            "limit": {"type": "integer", "minimum": 1, "maximum": 20, "default": 5, "description": "Maximum candidates to return."},
            "included_types": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Restrict candidates to these Google place types.",
            },
            **_DETAIL_PROPERTIES,
            **_LOCALE_PROPERTIES,
        },
        "required": ["lat", "lng"],
    },
    "output_schema": {
        "type": "object",
        "properties": {
            "query_location": _LOCATION_SCHEMA,
            "results": {
                "type": "array",
                "description": "Nearest places first.",
                "items": {
                    "type": "object",
                    "properties": {
                        **_PLACE_SCHEMA["properties"],
                        "meters_from_point": {"type": "number", "description": "Straight-line distance from the queried point."},
                    },
                },
            },
            "error": _ERROR_SCHEMA,
        },
    },
}

SERVER_INSTRUCTIONS = """Google Places and Routes tools. Use them whenever a request needs current
place data or a real travel calculation rather than recalled knowledge.

Picking a tool:
- goplaces_search: free-form venue, business, or landmark searches.
- goplaces_nearby: you already have coordinates and a radius.
- goplaces_resolve: turn an address or landmark into place IDs and coordinates.
- goplaces_autocomplete: partial user input.
- goplaces_details: phone, website, hours, reviews, or photos for one place ID.
- goplaces_photo: only with photo names from goplaces_details.
- goplaces_directions: distance, duration, steps, or a travel-mode comparison.
- goplaces_route_search: stops along a journey, ranked by detour time.
- goplaces_route_matrix: rank several candidates by travel time at once.
- goplaces_reverse_geocode: what is at a coordinate.

Cost: Google bills each Places request at the most expensive field tier it asks
for. detail_level defaults to 'full'. Pass 'basic' when ratings and hours are not
needed, and 'ids' when you only need place IDs. include_atmosphere and
include_ev are the most expensive options; leave them off unless asked.

Chaining: resolve or search first to get a place_id, then pass that place_id to
goplaces_details or as from_place_id/to_place_id in goplaces_directions. Page
through search results with next_page_token, repeating the other arguments
unchanged.

Attribution: when you show reviews or photos, include the author attribution the
response carries. Google's terms require it.

Errors come back as JSON with an "error" object and are flagged as tool errors.
A missing GOOGLE_PLACES_API_KEY means the server process lacks the variable."""
