# goplaces MCP

Google Places and Routes tools for Codex, Claude Code, and Hermes Agent, exposed through a shared Model Context Protocol server.

The server provides:

- `goplaces_search`
- `goplaces_nearby`
- `goplaces_autocomplete`
- `goplaces_details`
- `goplaces_photo`
- `goplaces_resolve`
- `goplaces_directions`
- `goplaces_route_search`
- `goplaces_route_matrix`
- `goplaces_reverse_geocode`

## Requirements

- Python 3.11 or newer
- [`uv`](https://docs.astral.sh/uv/)
- `GOOGLE_PLACES_API_KEY` with Places API (New) enabled
- Routes API enabled for directions and route search

Optional configuration:

```sh
export GOOGLE_PLACES_BASE_URL=https://places.googleapis.com/v1
export GOOGLE_ROUTES_BASE_URL=https://routes.googleapis.com
export GOOGLE_DIRECTIONS_BASE_URL=https://routes.googleapis.com
export GOOGLE_PLACES_TIMEOUT_SECONDS=10
export GOOGLE_PLACES_MAX_ATTEMPTS=3
export GOOGLE_PLACES_RETRY_BASE_DELAY_SECONDS=0.5
export GOPLACES_DEBUG=1   # request diagnostics on stderr
```

Requests that come back `429` or `503` are retried with exponential backoff, up
to `GOOGLE_PLACES_MAX_ATTEMPTS`. Every other status fails immediately.

## Cost

Google bills each Places request at the most expensive field tier named in its
field mask. Every place-returning tool takes `detail_level`:

| `detail_level` | Fields | Tier |
| --- | --- | --- |
| `ids` | place IDs only | Essentials |
| `basic` | name, address, location, type, Maps link | Pro |
| `full` (default) | adds rating, price, hours, phone, website | Enterprise |

`include_atmosphere` (editorial summary, dine-in/takeout/delivery, accessibility,
parking) and `include_ev` add the Enterprise + Atmosphere tier. The default is
`full` so responses do not regress; the bundled skill tells agents to ask for
`basic` when ratings and hours are not needed.

## Run the MCP server

```sh
export GOOGLE_PLACES_API_KEY=your_google_api_key
uv run goplaces-mcp
```

The server uses stdio, so an MCP client normally launches it rather than a user running it interactively.

## Codex

This repository is a Codex plugin. Its `.codex-plugin/plugin.json` manifest registers the bundled skill and shared `.mcp.json` server configuration. Install it through a Codex plugin marketplace or use the MCP configuration directly from a local checkout.

The MCP child process inherits `GOOGLE_PLACES_API_KEY` and the optional configuration variables from the Codex environment.

## Claude Code

This repository is also a Claude Code plugin. Its `.claude-plugin/plugin.json`, root `.mcp.json`, and `skills/goplaces/SKILL.md` are discovered by Claude Code when the plugin is installed.

For local development:

```sh
claude --plugin-dir /absolute/path/to/goplaces-mcp
```

Start Claude from an environment containing `GOOGLE_PLACES_API_KEY`.

## Hermes Agent

Hermes has its own MCP client, so it consumes this server rather than a native
Python plugin. Clone the repository on the Hermes host, install the dependencies,
and add one `mcp_servers` entry to `~/.hermes/config.yaml`:

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

Hermes only inherits an environment allowlist (`PATH`, `HOME`, and similar) into
stdio subprocesses, so the API key has to be named in `env`. The `${VAR}` form
resolves from `~/.hermes/.env`, which keeps the key out of `config.yaml`. Use an
absolute path for `uv`, because the config takes no working directory and the
inherited `PATH` may not be the login shell's.

Install the skill alongside it:

```sh
cp -R skills/goplaces ~/.hermes/skills/goplaces
```

Hermes registers the tools into an `mcp-goplaces` toolset and prefixes each
tool name with `mcp_` and the server name, the way Claude Code prefixes MCP
tools, so the agent sees a longer name than the bare one this README documents.
It also decodes the `goplaces_photo` image block into a Hermes `MEDIA:`
attachment.
Verify a deployment with `hermes mcp test goplaces`, which connects and lists
tools without issuing a billable Google request.

This replaces the standalone `hermes-goplaces` plugin, which is retired.

## Development

```sh
uv sync --dev
uv run pytest
```

The Google API client is dependency-free apart from the MCP transport package.

`tests/fake_google.py` runs a localhost HTTP server that replays canned Google
JSON, wired in through the base-URL variables above. Handler tests therefore
exercise the real request-building and response-mapping code without
credentials, network access, or billable calls.

Note that the launcher shells through `/bin/sh` so one `.mcp.json` can serve
both Codex and Claude Code by expanding `${CLAUDE_PLUGIN_ROOT:-.}` itself. That
makes it POSIX-only; Windows hosts cannot start it as written.

## Attribution

The Google client was converted from OpenClaw `goplaces` by Peter Steinberger. See `THIRD_PARTY_NOTICES.md`.
