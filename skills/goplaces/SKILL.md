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
- Use `goplaces_route_matrix` to rank several candidates by travel time in one request.
- Use `goplaces_reverse_geocode` to find out what is at a latitude/longitude.

## Workflows

**Resolve, then search nearby.** When the user names a place rather than giving
coordinates, call `goplaces_resolve` first, take the `location` from the best
candidate, then call `goplaces_nearby` with those coordinates and a radius.
`goplaces_resolve` is deliberately cheap, so this costs less than a wide text
search.

**Hand the place ID forward.** Every result carries a `place_id`. Pass it to
`goplaces_details` for hours, phone, and website, and as `from_place_id` or
`to_place_id` in `goplaces_directions` rather than re-typing the name. Place IDs
are exact; names are ambiguous.

**Rank candidates by travel time.** With several places in hand, one
`goplaces_route_matrix` call answers "which is closest" for all of them. Do not
loop `goplaces_directions` over candidates. For a single search, passing
`origin_lat`/`origin_lng` returns distance and duration per result directly.

**Page through results.** When a search response includes `next_page_token`,
pass it back as `page_token` to get the next page. Do not re-run the search with
a larger `limit` to simulate paging.

**Find stops on a journey.** `goplaces_route_search` takes the two endpoints and
returns stops ranked by `detour_seconds`, the extra time versus driving straight
through. Compare that against the `route` object it also returns. For EV stops,
pass `ev_connector_types` and `ev_min_charge_rate_kw`.

## Cost

Google bills each Places request at the **most expensive field tier it asks
for**, so ask for the cheapest tier that answers the question:

- `detail_level: "ids"` when you only need place IDs to feed another call.
- `detail_level: "basic"` for name, address, location, type, and a Maps link.
- `detail_level: "full"` (the default) adds rating, price, hours, phone, website.
- `include_atmosphere` and `include_ev` are the most expensive options.

Prefer `basic` unless the user's question actually turns on ratings, price, or
opening hours. `goplaces_details` with `include_reviews` or `include_photos`,
and any call with `include_atmosphere`, are the expensive calls; the rest are
comparatively cheap.

## Attribution

Google's terms require attribution for user-generated content. When you show a
review, include its `author.display_name`. When you show a photo, include the
photo's `author_attributions`. Both are already in the response. Reviews also
carry `flag_content_uri`, which should be preserved when displaying review text.

## Defaults and constraints

- Keep result limits modest: 5 for autocomplete, resolve, and route search; 10 for search and nearby.
- Request reviews, photos, turn-by-turn steps, and atmosphere fields only when the user needs them.
- Transit steps carry a `transit` object with line, headsign, stops, and departure times. Use it rather than the bare instruction.
- A `departure_time` on a drive route automatically enables traffic-aware timing.
- If a tool reports that `GOOGLE_PLACES_API_KEY` is missing, explain that the MCP server process needs the variable. Routes operations also require the Routes API on the same Google Cloud project.
