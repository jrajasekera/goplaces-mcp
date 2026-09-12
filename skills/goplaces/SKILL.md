---
name: goplaces
description: Find current places and businesses, retrieve place details, and plan routes or stops using Google Places and Routes MCP tools.
---

# Google Places and Routes

Use the `goplaces_*` tools when a request needs current Google Places data or a Google Routes calculation.

## Tool selection

- Use `goplaces_search` for free-form venue, business, landmark, attraction, or service searches.
- Use `goplaces_nearby` when coordinates and a search radius are already available.
- Use `goplaces_autocomplete` for partial input or likely place IDs.
- Use `goplaces_details` when phone, website, hours, reviews, photos, or business status are needed.
- Use `goplaces_photo` only with photo names returned by `goplaces_details`.
- Use `goplaces_resolve` to turn an address, landmark, city, or venue into candidate IDs and coordinates.
- Use `goplaces_directions` for distance, duration, optional steps, or travel-mode comparisons.
- Use `goplaces_route_search` for stops such as charging, coffee, gas, hotels, or food along a route.

## Defaults and constraints

- Keep result limits modest: 5 for autocomplete, resolve, and per-waypoint route searches; 10 for search and nearby.
- Request reviews, photos, and turn-by-turn steps only when the user needs them.
- For route search, start with `radius_m=1000`, `max_waypoints=5`, and `limit=5` unless broader coverage is requested.
- If a tool reports that `GOOGLE_PLACES_API_KEY` is missing, explain that the MCP server process needs the variable. Routes operations also require the Routes API on the same Google Cloud project.
