# goplaces MCP

This repository packages one Google Places and Routes implementation for Codex, Claude Code, and Hermes Agent through a shared stdio MCP server.

`AGENTS.md` is the canonical project guidance; keep `CLAUDE.md` as a relative symlink to it.

## Development Setup

- Read `README.md`, `pyproject.toml`, and the files relevant to the change.
- Requires Python 3.11+ and `uv`; use `uv` for environments, dependencies, commands, and lockfile updates.
- Launch the stdio server with `uv run goplaces-mcp`; an MCP client normally starts it.

## Architecture

- `src/goplaces_mcp/tools.py` contains the Google Places and Routes client, validation, response mapping, and tool handlers.
- `src/goplaces_mcp/schemas.py` is the authoritative definition of the ten public tool names, titles, descriptions, input schemas, output schemas, and the `SERVER_INSTRUCTIONS` string sent at initialize.
- `src/goplaces_mcp/server.py` is a thin MCP adapter. Keep provider and domain logic out of this file. It sets `isError` on payloads carrying an `error` key, attaches read-only tool annotations, returns `structuredContent`, and lifts photo bytes into an image block.
- `skills/goplaces/SKILL.md` teaches agents when and how to select the tools. Keep it valid for Codex, Claude Code, and Hermes Agent; Hermes prefixes the tool names, so refer to tools by their bare `goplaces_*` names and do not assume a host-specific spelling.
- `.mcp.json` is intentionally shared by both hosts. It uses `CLAUDE_PLUGIN_ROOT` when Claude supplies it and the plugin-root working directory otherwise. The `/bin/sh` wrapper is what expands that variable, which is why one manifest can serve both hosts and why the launcher is POSIX-only. `uv run --frozen` keeps startup from resolving or re-locking dependencies.
- `.codex-plugin/plugin.json` and `.claude-plugin/plugin.json` are host-specific metadata around the same skill and server.
- Hermes Agent is the third consumer and needs no manifest here: it has its own MCP client and is wired up with an `mcp_servers` entry in `~/.hermes/config.yaml` plus a copy of `skills/goplaces` in `~/.hermes/skills`. README documents the entry. The retired native plugin lives at `github.com/jrajasekera/hermes-goplaces`; do not port fixes there.
- Release versions are duplicated in both plugin manifests, `pyproject.toml`, and `src/goplaces_mcp/__init__.py`; keep them synchronized when releasing. `tests/test_packaging.py` fails if they drift.

## Compatibility Invariants

- Preserve the existing `goplaces_*` tool names unless a breaking release is explicitly requested.
- Keep `.mcp.json` at the repository root. Codex plugin validation requires that filename, while Claude Code discovers it there automatically.
- Do not replace `${CLAUDE_PLUGIN_ROOT:-.}` with a host-specific absolute path.
- Treat stdout as MCP protocol output. Send diagnostics through the module `_logger` in `tools.py`, which only attaches a stderr handler when `GOPLACES_DEBUG` is set. Never use ordinary `print()` calls.
- Tool handlers are synchronous network clients; dispatch them off the async event loop as the server currently does.
- Keep schemas and handlers in one-to-one correspondence. Adding or removing a tool requires updating the schema registry, handler registry, skill, README, and protocol tests. Every tool also needs a `title` and an `output_schema`, plus an entry in the `cases` table in `tests/test_protocol_results.py`; both are asserted exhaustively, so a new tool fails the suite until it is listed.
- Return machine-readable JSON errors to the agent instead of leaking handler exceptions through the MCP transport.
- Validate every argument before issuing the first billable Google request. A handler that fails validation after a network call has already cost the user money.
- Field-mask tokens are `places.`-prefixed on the search endpoints and unprefixed on Place Details. `nextPageToken` and `routingSummaries` hang off the response root and must never take the `places.` prefix; `_place_field_mask` prefixes everything passed to it, so append root tokens at the call site.
- Google bills each Places request at the most expensive field tier the mask names. Keep `_place_field_mask` the single place tiers are decided, and leave the most expensive fields behind `include_atmosphere` and `include_ev`.
- `computeRouteMatrix` returns a JSON array, not an object, and its elements arrive unordered. Use `GooglePlacesClient.request_list` and key off `originIndex`/`destinationIndex`.
- `_as_string_list` splits a bare string on commas, which is correct for enum-ish lists such as types and connector types, and wrong for free-form addresses. Use `_as_address_list` for anything a user might write a comma inside, and use the same helper for a list and for any labels indexed against it, or label *i* stops describing item *i*.
- A handler that issues more than one request must build every request body before the first call. Deferring the second body means a mode-specific option that is invalid for the second request fails only after the first has been paid for.
- Localized route text lives at two levels. `routes.localizedValues` spans the whole trip; `routes.legs[].localizedValues` covers one leg. Read leg text as the trip total and a waypoint route silently reports only its first leg, so `units` appears to do nothing.
- The Routes API carries no route-level departure or arrival time. A transit route's real schedule is on its steps, in `transitDetails.stopDetails`; only echo the requested time when the route has none of its own, and omit the field rather than emitting an empty string.
- An unrequested field must be absent, not empty. A cheap `detail_level` that returns `name: ""` or `types: []` tells an agent the place has no name and no types, which is a different claim from "a cheaper tier did not ask". `_strip_none` drops `None`, not `""` or `[]`.

## Credentials And External Calls

- Never commit API keys, credentials, `.env` files, or captured request headers.
- `GOOGLE_PLACES_API_KEY` is required at runtime. Directions and route-search operations also require the Routes API on the same Google Cloud project.
- The optional base-URL variables exist to support controlled testing; do not silently redirect production requests.
- `GOOGLE_PLACES_TIMEOUT_SECONDS`, `GOOGLE_PLACES_MAX_ATTEMPTS`, and `GOOGLE_PLACES_RETRY_BASE_DELAY_SECONDS` tune the client. `429` and `503` are retried with exponential backoff; every other status fails immediately.
- Tests should mock Google responses and must not require credentials or make billable API calls.
- Do not perform a live Google request unless the user explicitly authorizes it and understands that it may incur API usage.

## Development And Verification

Use the narrowest relevant checks while iterating. Before handing off a change that affects packaging, schemas, the server, or manifests, run:

```sh
uv sync --dev
uv run pytest
uv run python -m compileall -q src tests
uv build
git diff --check
claude plugin validate .
```

Also validate the Codex skill and plugin when the installed validators are available:

```sh
uv run --with pyyaml python ~/.codex/skills/.system/skill-creator/scripts/quick_validate.py skills/goplaces
uv run --with pyyaml python ~/.codex/skills/.system/plugin-creator/scripts/validate_plugin.py .
```

`tests/test_server.py` covers tool listing, annotations, output schemas, server instructions, `isError`, and both manifest launch modes with the API key removed.

`tests/fake_google.py` is a localhost HTTP stand-in for Google, wired in through
the documented base-URL variables by the `google` fixture in `tests/conftest.py`.
Use it for anything that touches request building or response mapping; it needs
no credentials and makes no billable calls. `google.reply()` sets a canned
response per path suffix, `google.sequence()` queues responses for retry tests,
and recorded requests expose `body`, `query`, and `mask_tokens()` so field masks
can be asserted directly.

`tests/test_protocol_results.py` validates each tool's real output against the
`output_schema` it publishes; keep that passing when changing response shapes.

Protocol tests must initialize a real stdio `ClientSession`; direct calls to decorated `Server` registration methods do not exercise the public MCP path. Keep coverage for both launch modes:

- Codex: plugin-root working directory with no `CLAUDE_PLUGIN_ROOT`.
- Claude Code: an arbitrary working directory with `CLAUDE_PLUGIN_ROOT` set to the plugin root.

Report live-API validation separately from credential-free protocol, mocked handler, and package validation.
