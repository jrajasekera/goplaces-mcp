# goplaces MCP

Google Places and Routes tools for Codex, Claude Code, and Hermes Agent, served
by one stdio [Model Context Protocol](https://modelcontextprotocol.io) server.

An agent with this server can search for businesses and landmarks, look up
hours, phone numbers, reviews, and photos, and calculate directions, travel-time
comparisons, and stops along a route, all from live Google data.

- [Quick start](#quick-start)
- [Install into a host](#install-into-a-host)
  - [Codex](#codex)
  - [Claude Code](#claude-code)
  - [Hermes Agent](#hermes-agent)
- [Tools](#tools)
- [Configuration](#configuration)
- [Cost](#cost)
- [Errors and retries](#errors-and-retries)
- [How it fits together](#how-it-fits-together)
- [Development](#development)
- [Attribution and license](#attribution-and-license)

## Quick start

You need Python 3.11 or newer, [`uv`](https://docs.astral.sh/uv/), and a
Google Cloud API key with **Places API (New)** enabled. Enable the **Routes
API** on the same project as well if you want directions, route search, or
travel-time matrices.

1. Clone the repository and install the locked dependencies:

   ```sh
   git clone https://github.com/jrajasekera/goplaces-mcp
   cd goplaces-mcp
   uv sync
   ```

2. Check that the server starts with your key:

   ```sh
   export GOOGLE_PLACES_API_KEY=your_google_api_key
   uv run goplaces-mcp
   ```

   The server speaks MCP over stdin and stdout and waits silently for a client.
   Nothing is printed, and no Google request is made. Press `Ctrl-C` to stop
   it.

3. Register it with a host using one of the sections under
   [Install into a host](#install-into-a-host). The host launches the server
   itself from then on. You never run it by hand.

## Install into a host

Every host launches the same `goplaces-mcp` command and passes it
`GOOGLE_PLACES_API_KEY` plus any of the [optional variables](#configuration).
The bundled skill in `skills/goplaces/SKILL.md` teaches the agent when to reach
for which tool and how to keep Google costs down.

### Codex

This repository is a Codex plugin. `.codex-plugin/plugin.json` registers the
skill and the shared `.mcp.json` server definition.

Install it through a Codex plugin marketplace, or point Codex at the
`.mcp.json` file in a local checkout. The MCP child process inherits
`GOOGLE_PLACES_API_KEY` and the optional variables from the Codex environment,
so set them in the shell that starts Codex.

### Claude Code

This repository is also a Claude Code plugin. Claude Code discovers
`.claude-plugin/plugin.json`, the root `.mcp.json`, and the skill when the
plugin is installed.

To use a local checkout without installing it:

```sh
export GOOGLE_PLACES_API_KEY=your_google_api_key
claude --plugin-dir /absolute/path/to/goplaces-mcp
```

The server inherits the key from the environment Claude Code starts in.

### Hermes Agent

Hermes has its own MCP client, so it uses this server directly rather than a
native plugin. The older standalone `hermes-goplaces` plugin is retired.

1. Clone the repository on the Hermes host and run `uv sync` in it.

2. Add one `mcp_servers` entry to `~/.hermes/config.yaml`:

   ```yaml
   mcp_servers:
     goplaces:
       command: /usr/local/bin/uv
       args:
         - run
         - --frozen
         - --directory
         - /absolute/path/to/goplaces-mcp
         - goplaces-mcp
       env:
         GOOGLE_PLACES_API_KEY: ${GOOGLE_PLACES_API_KEY}
   ```

   Two details matter here. Hermes passes only an allowlist of variables
   (`PATH`, `HOME`, and similar) to stdio subprocesses, so the key must be named
   under `env`. The `${VAR}` form resolves from `~/.hermes/.env`, which keeps the
   key out of `config.yaml`. Use an absolute path for `uv` because the entry has
   no working directory and the inherited `PATH` may differ from your login
   shell's.

3. Copy the skill next to it:

   ```sh
   cp -R skills/goplaces ~/.hermes/skills/goplaces
   ```

4. Verify the connection without spending any Google quota:

   ```sh
   hermes mcp test goplaces
   ```

Hermes registers the tools into an `mcp-goplaces` toolset and prefixes each
name with `mcp_` and the server name, much as Claude Code prefixes MCP tools.
The agent therefore sees a longer name than the bare one used in this README.
Hermes also decodes the image block from `goplaces_photo` into a `MEDIA:`
attachment.

## Tools

All ten tools are read-only. Place-returning tools accept `detail_level`, which
sets the Google field tier described under [Cost](#cost).

| Tool | Use it when |
| --- | --- |
| `goplaces_search` | Free-form text search for businesses, landmarks, venues, or services, with filters such as open now, rating, and price. |
| `goplaces_nearby` | You already have coordinates and a radius, optionally filtered by place type. |
| `goplaces_autocomplete` | Turning partial user input into place and query suggestions with place IDs. |
| `goplaces_details` | One place ID needs phone, website, hours, business status, reviews, or photo metadata. |
| `goplaces_photo` | Fetching the image for a photo name returned by `goplaces_details`. Returned as an image block. |
| `goplaces_resolve` | Turning an address, landmark, or city into candidate place IDs and coordinates. |
| `goplaces_directions` | Distance, duration, warnings, and optional steps between two points. Modes: drive (default), walk, bicycle, transit. |
| `goplaces_route_search` | Stops such as charging, fuel, coffee, or hotels along a route, ranked by detour time. |
| `goplaces_route_matrix` | Ranking several destinations by travel time from one or more origins in a single request. |
| `goplaces_reverse_geocode` | Finding out what is at a latitude and longitude, nearest first. |

Directions, route search, and route matrix use the Routes API. The rest use the
Places API (New).

Every tool publishes a title, an input schema, and an output schema through MCP,
and results arrive as `structuredContent`. The authoritative definitions live in
`src/goplaces_mcp/schemas.py`.

## Configuration

The server reads its configuration from environment variables. Only the API key
is required.

| Variable | Default | Purpose |
| --- | --- | --- |
| `GOOGLE_PLACES_API_KEY` | none, required | Google Cloud API key with Places API (New) enabled. |
| `GOOGLE_PLACES_TIMEOUT_SECONDS` | `10` | Per-request HTTP timeout. |
| `GOOGLE_PLACES_MAX_ATTEMPTS` | `3` | Total attempts for a request that returns `429` or `503`. |
| `GOOGLE_PLACES_RETRY_BASE_DELAY_SECONDS` | `0.5` | Base delay for exponential backoff between attempts. |
| `GOPLACES_DEBUG` | off | Set to `1` to log request diagnostics to stderr. Stdout stays reserved for MCP. |
| `GOOGLE_PLACES_BASE_URL` | `https://places.googleapis.com/v1` | Places endpoint. For controlled testing only. |
| `GOOGLE_ROUTES_BASE_URL` | `https://routes.googleapis.com` | Routes endpoint. For controlled testing only. |
| `GOOGLE_DIRECTIONS_BASE_URL` | same as `GOOGLE_ROUTES_BASE_URL` | Directions endpoint. For controlled testing only. |

## Cost

Google bills each Places request at the most expensive field tier named in its
field mask, so the fields a tool asks for decide what a call costs. The
`detail_level` argument picks the tier:

| `detail_level` | Fields returned | Google tier |
| --- | --- | --- |
| `ids` | place IDs only | Essentials |
| `basic` | name, address, location, type, Maps link | Pro |
| `full` (default) | adds rating, price, hours, phone, website | Enterprise |

Two further options add the Enterprise + Atmosphere tier and are off by
default: `include_atmosphere` (editorial summary, dine-in, takeout, delivery,
accessibility, parking) and `include_ev` (charging connectors).

The default stays `full` so that responses never silently lose fields. The
bundled skill tells agents to ask for `basic` when ratings and hours are not
needed, and for `ids` when results only feed another call.

A field that was not requested is absent from the response rather than empty.
A missing `rating` means the tier did not ask for it, not that the place is
unrated.

## Errors and retries

Errors are returned to the agent as JSON with an `error` object and are flagged
as tool errors over MCP. Handler exceptions never escape the transport.

```json
{"error": {"type": "validation", "field": "location.radius_m", "message": "must be > 0"}}
{"error": {"type": "google_api", "status": 403, "message": "..."}}
```

- `validation` errors are raised before any Google request is made, so a bad
  argument costs nothing.
- `google_api` errors carry Google's HTTP status and message.
- Responses with status `429` or `503` are retried with exponential backoff up
  to `GOOGLE_PLACES_MAX_ATTEMPTS`. Every other status fails immediately.
- A missing `GOOGLE_PLACES_API_KEY` is reported as a validation error naming
  the variable.

## How it fits together

**One server, three hosts.** Codex and Claude Code both read the root
`.mcp.json`. Its launcher shells through `/bin/sh` to expand
`${CLAUDE_PLUGIN_ROOT:-.}`: Claude Code supplies that variable, Codex does not,
and the fallback to the working directory covers Codex. That is why one
manifest serves both hosts, and also why the launcher is POSIX-only. Windows
hosts cannot start it as written. Hermes needs no manifest because it has its
own MCP client and takes the command from its config file.

**Frozen startup.** The launcher runs `uv run --frozen`, so starting the server
never resolves or re-locks dependencies. Startup stays fast and deterministic.

**Thin adapter, thick client.** `src/goplaces_mcp/server.py` only translates
between MCP and the handlers: it attaches read-only annotations, sets `isError`
on payloads carrying an `error` key, returns `structuredContent`, and lifts
photo bytes into an image block. Everything Google-specific, including
validation, field masks, retries, and response mapping, lives in
`src/goplaces_mcp/tools.py`. The client depends only on the MCP transport
package.

**Validate before you pay.** Every argument is checked before the first
billable request, and a tool that issues several requests builds all of them
before sending any. A mistake in the second request is caught before the first
one has cost anything.

## Development

```sh
uv sync --dev
uv run pytest
```

Tests never need credentials and never call Google. `tests/fake_google.py`
runs a localhost HTTP server that replays canned Google JSON, wired in through
the base-URL variables above, so handler tests exercise the real
request-building and response-mapping code.

`tests/test_protocol_results.py` checks each tool's real output against the
output schema it publishes, and `tests/test_server.py` covers tool listing,
annotations, server instructions, error flagging, and both plugin launch modes.

Before handing off a change that touches packaging, schemas, the server, or
the manifests, also run:

```sh
uv run python -m compileall -q src tests
uv build
git diff --check
claude plugin validate .
```

`AGENTS.md` records the project's compatibility invariants and the traps that
have produced defects before. Read it before changing a handler.

## Attribution and license

The Google client was converted from OpenClaw `goplaces` by Peter Steinberger.
See `THIRD_PARTY_NOTICES.md`.

Google's terms require attribution when reviews or photos are shown. The
responses carry the author attribution, and the bundled skill tells agents to
keep it.

MIT licensed. See `LICENSE`.
