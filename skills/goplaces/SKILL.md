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

## Defaults worth knowing

- `goplaces_directions` defaults to `mode: "drive"`. Pass `walk`, `bicycle`, or
  `transit` explicitly when the user asked for one. `goplaces_route_search` and
  `goplaces_route_matrix` also default to `drive`.
- `detail_level` defaults to `"full"` on every Places tool. That is the right
  default — see [Cost](#cost) for when to go cheaper — but it is not free.
- `include_steps`, `include_reviews`, `include_photos`, `include_atmosphere`,
  and `include_ev` all default to off. Nothing returns them by accident.
- An unrequested field is **absent**, not empty. A missing `rating` means the
  tier did not ask for it, not that the place is unrated. Never report absence
  as a fact about the place.

## Workflows

**Resolve, then search nearby.** When the user names a place rather than giving
coordinates, call `goplaces_resolve` first, take the `location` from the best
candidate, then call `goplaces_nearby` with those coordinates and a radius.
`goplaces_resolve` is deliberately cheap, so this costs less than a wide text
search.

**Hand the place ID forward.** Every result carries a `place_id`. Pass it to
`goplaces_details` for hours, phone, and website, and as `from_place_id` or
`to_place_id` in `goplaces_directions` rather than re-typing the name. Place IDs
are exact; names are ambiguous. The one exception is `waypoints`, which takes
text only.

**Rank candidates by travel time.** With several places in hand, one
`goplaces_route_matrix` call answers "which is closest" for all of them. Do not
loop `goplaces_directions` over candidates. For a single search, passing
`origin_lat`/`origin_lng` returns distance and duration per result directly.

**Page through results.** When a search response includes `next_page_token`,
pass it back as `page_token`. Every other argument — `detail_level`,
`limit`, and each filter included — must match the original request or Google
rejects the page. Do not re-run the search with a larger `limit` to simulate
paging.

**Find stops on a journey.** `goplaces_route_search` takes the two endpoints and
returns stops ranked by `detour_seconds`, the extra time versus driving straight
through. Compare that against the `route` object it also returns. For EV stops,
pass `ev_connector_types` and `ev_min_charge_rate_kw`.

## Worked examples

**"Coffee near the Space Needle."**

1. `goplaces_resolve {"location_text": "Space Needle", "limit": 3}` → take `location`
   from the best candidate.
2. `goplaces_nearby {"lat": 47.6205, "lng": -122.3493, "radius_m": 800,
   "included_types": ["cafe"], "rank_by": "distance", "limit": 10}` → pick a
   `place_id` from the results. `goplaces_nearby` has no `open_now` filter; for
   "open now" use `goplaces_search` with a location bias instead.
3. `goplaces_details {"place_id": "...", "include_reviews": false}` for hours,
   phone, and website. Only call this for the one or two places you will
   actually name in the answer.

**"Which of these three offices is closest to home?"**

One call, not three:

```json
{"origins": ["123 Home St, Seattle"],
 "destinations": ["Office A address", "Office B address", "Office C address"],
 "mode": "drive"}
```

`goplaces_route_matrix` returns results sorted fastest first. Looping
`goplaces_directions` over the candidates costs three billable requests and
gives the same answer.

**"EV charging on the way to Portland, CCS only."**

```json
{"query": "EV charging", "from_text": "Seattle, WA", "to_text": "Portland, OR",
 "ev_connector_types": ["EV_CONNECTOR_TYPE_CCS_COMBO_2"],
 "ev_min_charge_rate_kw": 150, "include_ev": true, "limit": 5}
```

Read each result's `detour_seconds` against the returned `route` object — the
detour is extra time versus driving straight through, and `trip_duration_seconds`
is the whole journey via that stop.

## When a call comes back empty

Zero results is a real answer. Before concluding that, relax the request in this
order, one step at a time:

1. Drop the hard filters the call accepts — `open_now`, `min_rating`,
   `price_levels`, `included_type`, `ev_min_charge_rate_kw`. These exclude
   places that exist.
2. Widen `radius_m` on `goplaces_nearby`, roughly doubling rather than nudging,
   or drop `included_types` to a broader type.
3. Fall back from `goplaces_nearby` to `goplaces_search` with a text query and a
   location bias — `nearby` is type-driven and goes empty in sparse areas where
   text search still finds something.

Do not re-issue the identical call. If the relaxed search is still empty, say so
and say what was relaxed; a widened or filter-free result is a different claim
from what the user asked for and should be reported as such.

## Cost

Google bills each Places request at the **most expensive field tier it asks
for**:

- `detail_level: "ids"` returns place IDs only.
- `detail_level: "basic"` adds name, address, location, type, and a Maps link.
- `detail_level: "full"` (the default) adds rating, price, hours, phone, website.
- `include_atmosphere` and `include_ev` are the most expensive options.

Keep the `full` default for anything you will show the user — dropping to a
cheaper tier and then needing hours costs a second billable call, and the absent
field is easy to misread as "this place has none". Downgrade deliberately:

- `"ids"` when the results only feed another call, such as place IDs going
  straight into `goplaces_route_matrix`.
- `"basic"` for a wide sweep you will narrow before showing anything, or when
  the user asked only where something is.
- Leave `include_atmosphere` and `include_ev` off unless the question turns on
  dine-in/accessibility/parking or on charging connectors.

`goplaces_details` with `include_reviews` or `include_photos` is the other
expensive call. Make it for the places you will actually name, not for every
search result.

## Photos

`goplaces_details` with `include_photos` returns photo metadata, including each
photo's resource `name`. Pass that name to `goplaces_photo` to get the image
itself, which the server returns as an image block. Fetch a photo only when the
user asked to see one — the metadata alone is enough to say a place has photos.

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
- `alternatives` cannot be combined with `waypoints`, and the drive-only
  modifiers (`avoid_tolls`, `avoid_highways`, `avoid_ferries`) are rejected for
  a walking, cycling, or transit route.
- If a tool reports that `GOOGLE_PLACES_API_KEY` is missing, explain that the MCP server process needs the variable. Routes operations also require the Routes API on the same Google Cloud project.
