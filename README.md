# goplaces MCP

Google Places and Routes tools for Codex and Claude Code, exposed through a shared Model Context Protocol server.

The server provides:

- `goplaces_search`
- `goplaces_nearby`
- `goplaces_autocomplete`
- `goplaces_details`
- `goplaces_photo`
- `goplaces_resolve`
- `goplaces_directions`
- `goplaces_route_search`

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
```

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

## Development

```sh
uv sync --dev
uv run pytest
```

The Google API client is dependency-free apart from the MCP transport package. Tests mock network behavior and do not require an API key.

## Attribution

The Google client was converted from OpenClaw `goplaces` by Peter Steinberger. See `THIRD_PARTY_NOTICES.md`.
