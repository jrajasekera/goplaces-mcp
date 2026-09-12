# goplaces MCP: Implementing the 2026-09-11 Server Review

Date: 2026-09-12
Source: `docs/reviews/2026-09-11_server-review-and-improvement-ideas.md`
Branch: `review-improvements`

## Verified Google API facts this plan relies on

Confirmed against developers.google.com before writing code:

- `searchAlongRouteParameters.polyline.encodedPolyline` is accepted by
  `places:searchText` only, and requires `textQuery`.
- `routingSummaries` is a **root-level** field-mask token. Prefixing it with
  `places.` is an error. Entries are index-aligned 1:1 with `places`, and an
  entry is empty when no summary is available.
- Search-along-route produces **two** legs per summary (origin -> place, place ->
  destination). A plain `routingParameters.origin` produces **one**.
- `routingParameters.origin` is a bare `LatLng`, unlike Routes waypoints.
  `routingParameters` works on both `searchText` and `searchNearby`, and does
  not support `TRANSIT`.
- Field-mask tokens are `places.`-prefixed on search, unprefixed on Place Details.
- Billing is at the **highest** SKU tier named in the field mask.
- `computeRouteMatrix` lives at `/distanceMatrix/v2:computeRouteMatrix` and
  returns a JSON **array** of elements, unordered; key off `originIndex` /
  `destinationIndex`.
- Google's own best-practices pages recommend exponential backoff on 4XX/5XX
  transient failures.

## Stages

### 1. Test harness (done)

`tests/fake_google.py` replays canned Google JSON over localhost through the
documented base-URL variables. `tests/conftest.py` exposes the `google` fixture.
Covers validation, mapping, errors, retries, directions, polyline decoding.

### 2. Field tiers and richer details

Replace the fixed field-mask constants with a table of field -> SKU tier, and
derive masks from a `detail_level` of `ids` | `basic` | `full`, plus
`include_atmosphere` and `include_ev` flags for the Atmosphere-tier extras.

`detail_level` defaults to `full` so existing responses do not regress; the
skill tells agents to ask for `basic` or `ids` when they do not need ratings.

### 3. Maps links

Add `places.googleMapsUri` to the search/nearby/details masks, and build
`maps_url` / `directions_url` client-side from the documented Maps URLs scheme
so no extra API call is needed.

### 4. Routing summaries on search and nearby

Optional `origin_lat` / `origin_lng` (+ `origin_mode`) attach
`routingParameters`, and each result gains `distance_meters` /
`duration_seconds` from the aligned `routingSummaries` entry.

### 5. Native search-along-route

Rewrite `goplaces_route_search` as two requests: one `computeRoutes` for the
polyline, one `searchText` with `searchAlongRouteParameters` plus
`routingSummaries`. Each result carries a detour time derived from the two legs
against the direct route duration. Deletes the waypoint sampler, the per-waypoint
fan-out, and the `locationBias` approximation, which removes the timeout,
sampling, and bias problems together.

### 6. Directions gaps

Add `transitDetails` to the mask and map line/headsign/stops/times. Add
intermediate waypoints, `computeAlternativeRoutes`, `transitPreferences`, and
`routing_preference` for traffic-aware drive times.

### 7. New tools

- `goplaces_route_matrix` wrapping `computeRouteMatrix`.
- `goplaces_reverse_geocode` as a distance-ranked tight-radius nearby search.

### 8. Protocol polish

`isError` on error payloads, `readOnlyHint`/`openWorldHint` annotations,
`outputSchema` + `structuredContent`, server `instructions`, and optional photo
bytes returned as MCP image content.

### 9. Docs

Skill workflows (resolve-then-nearby, place-ID handoff, paging, attribution
requirements, cost guidance), README, and AGENTS.md.

## Out of scope, with reason

Removing `/bin/sh` from `.mcp.json` (review item 14). The shell is what lets one
manifest serve both hosts: it expands `${CLAUDE_PLUGIN_ROOT:-.}` itself, and
AGENTS.md makes that expression a compatibility invariant. Moving expansion to
the host would depend on Codex's unverified support for variable substitution in
`args`. `--frozen` is added, which is the portable half of the item.
