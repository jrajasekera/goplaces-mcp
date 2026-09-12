# goplaces MCP: Server Review and Improvement Ideas

Date: 2026-09-11

## Overall

The server is in good shape. The thin adapter, hand-written schemas, stdlib-only
client, and machine-readable JSON errors are all sound choices. The two biggest
gaps are test coverage and the route-search design. Everything else below is
incremental.

## Issues in the current code

- **Test coverage is almost entirely protocol-level.** Five tests exist and none
  exercise a handler against a mocked Google response. Validation, response
  mapping, polyline decoding, waypoint sampling, and directions compare mode are
  untested. The base-URL env vars exist for exactly this purpose but nothing
  uses them. A local HTTP fixture that replays canned Google JSON would cover
  the whole of `src/goplaces_mcp/tools.py` without credentials.
- **Errors are not flagged as errors on the wire.** Handlers return JSON error
  payloads as ordinary text content. MCP has an `isError` flag on tool results,
  and hosts use it to decide whether to retry or surface the failure. Setting it
  when the payload has an `error` key is a small change in `server.py`.
- **Route search can take minutes.** It makes one routes call plus one Places
  call per waypoint, all sequential, each with a 10s timeout. With the maximum
  of 20 waypoints that is a possible 210s wall clock, which many MCP clients
  will time out. The per-waypoint searches are independent and could run in a
  thread pool.
- **Waypoint sampling is by index, not by distance.** Polyline points are dense
  in cities and sparse on highways, so sampled waypoints cluster around urban
  stretches and skip long rural gaps. Sampling by cumulative haversine distance
  along the polyline would spread them evenly (`_sample_waypoints`).
- **Route search uses a bias, not a restriction.** Each waypoint search passes
  `locationBias`, so Google may return places well outside the radius. Results
  also carry no distance back to the waypoint or route, so the agent cannot
  tell a 200m detour from a 15km one.
- **Transit directions lose the useful part.** The directions field mask omits
  `transitDetails`, so transit steps come back as bare instructions with no line
  name, headsign, stop names, or departure times.
- **Stale references.** Three docstrings and a comment still say "Hermes", the
  agent this was ported from. Also `.serena/` is untracked and should be
  gitignored.
- **Version is defined in four places.** The package, pyproject, and both plugin
  manifests each carry `1.0.0`. A test asserting they agree would prevent
  drift.
- **No logging or retries.** There is no stderr diagnostic path at all, and a
  429 or 503 from Google fails immediately. A bounded retry with backoff for
  those two codes and an opt-in debug log to stderr would help when things go
  wrong inside a host.

## Improvement ideas, roughly by value

1. **Use Google's native search-along-route.** Places API Text Search accepts
   `searchAlongRouteParameters` with an encoded polyline and returns
   `routingSummaries` with detour duration per result. That collapses route
   search from N+1 requests into 2, fixes the sampling, bias, and timeout
   problems above, and gives the agent detour times for free. Highest-value
   change; also deletes code.
2. **Add Google Maps links to every result.** `googleMapsUri` is cheap to
   request for places, and a directions URL can be built client-side with no
   API call. Agents constantly get asked "send me the link".
3. **Return routing summaries on search and nearby.** Passing
   `routingParameters` with an origin makes Google return distance and duration
   from that origin for each result. Answers "which of these is closest to me"
   without a directions call per result.
4. **Structured output.** MCP supports `outputSchema` on tools and
   `structuredContent` on results. Hosts can validate and render results rather
   than parsing a JSON string out of text. Also documents the response shape.
5. **Tool annotations.** Every tool is read-only and open-world. Declaring
   `readOnlyHint` and `openWorldHint` lets hosts skip confirmation prompts.
6. **Richer details, opt-in.** Useful additions: `editorialSummary`,
   `primaryTypeDisplayName`, `internationalPhoneNumber`, `utcOffsetMinutes`,
   dine-in, takeout, delivery, reservable, accessibility and parking options,
   `priceRange`. Group behind `include_*` or a `fields` parameter so the base
   call stays cheap.
7. **EV charging support.** The default prompt is "Find EV charging stops
   between Seattle and Portland", but search cannot express connector type or
   minimum charge rate, and results omit `evChargeOptions`. Text Search
   supports `evOptions` for both.
8. **Directions gaps.** Intermediate waypoints, `computeAlternativeRoutes`,
   transit mode preferences (rail only, fewer transfers), and
   `routingPreference` for traffic-aware drive times. Worth verifying: Routes
   API may ignore or reject `departureTime` for DRIVE unless a traffic-aware
   routing preference is set, so departure time might silently do nothing.
9. **Cost awareness.** Places API (New) bills by the most expensive field tier
   requested. Every search asks for rating, price level, and current hours,
   which pushes each call into the top tier. A `detail_level` parameter (ids
   only, basic, full) would let the skill default to cheap calls and escalate
   only when needed. The skill should document which tools are expensive.
10. **Photos as image content.** MCP has an image content type.
    `goplaces_photo` could optionally fetch the bytes and return them so the
    agent can look at the photo rather than pass a URL along.
11. **Server instructions.** The MCP `initialize` response can carry an
    `instructions` string. A condensed skill there covers hosts that install the
    server without the plugin skill.
12. **Skill depth.** `skills/goplaces/SKILL.md` covers tool selection but not
    workflows. Add: resolve-then-nearby chain, place ID handoff into details
    and directions, paging with `next_page_token`, and Google's attribution
    requirements for reviews and photos (the server already returns author
    attributions and flag URLs the agent should surface).
13. **Two candidate new tools.** A route matrix tool wrapping
    `computeRouteMatrix` for "rank these five places by drive time from home",
    and a reverse-geocode approximation via nearby search with a tiny radius
    for "what is at these coordinates". Both are adds under the compatibility
    invariants, so they touch schemas, handlers, skill, README, and tests
    together.
14. **Startup and portability.** The `.mcp.json` launcher runs `uv run` without
    `--frozen`, so first launch may resolve and sync, and every launch checks
    the lock. It also shells through `/bin/sh`, so Windows hosts cannot start
    it. Only matters if distributed widely.

## Suggested order

1. Test harness (everything else needs it).
2. Native search-along-route rewrite.
3. Maps links plus routing summaries.
4. `isError` and tool annotations as a small polish pass.
5. Cost tiers and richer details once real agent usage is visible.
