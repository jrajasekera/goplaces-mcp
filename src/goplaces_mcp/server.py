"""MCP adapter for the shared Google Places and Routes handlers."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from typing import Any

from mcp import types
from mcp.server import NotificationOptions, Server
from mcp.server.models import InitializationOptions
from mcp.server.stdio import stdio_server

from . import __version__, schemas, tools

_TOOL_DEFINITIONS = (
    schemas.GOPLACES_SEARCH,
    schemas.GOPLACES_NEARBY,
    schemas.GOPLACES_AUTOCOMPLETE,
    schemas.GOPLACES_DETAILS,
    schemas.GOPLACES_PHOTO,
    schemas.GOPLACES_RESOLVE,
    schemas.GOPLACES_DIRECTIONS,
    schemas.GOPLACES_ROUTE_SEARCH,
)

_HANDLERS = {
    "goplaces_search": tools.goplaces_search,
    "goplaces_nearby": tools.goplaces_nearby,
    "goplaces_autocomplete": tools.goplaces_autocomplete,
    "goplaces_details": tools.goplaces_details,
    "goplaces_photo": tools.goplaces_photo,
    "goplaces_resolve": tools.goplaces_resolve,
    "goplaces_directions": tools.goplaces_directions,
    "goplaces_route_search": tools.goplaces_route_search,
}


def create_server() -> Server:
    """Create the server while preserving the existing hand-written schemas."""
    server = Server("goplaces")

    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        return [
            types.Tool(
                name=definition["name"],
                description=definition["description"],
                inputSchema=definition["parameters"],
            )
            for definition in _TOOL_DEFINITIONS
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: Mapping[str, Any]) -> list[types.TextContent]:
        handler = _HANDLERS.get(name)
        if handler is None:
            payload = {"error": {"type": "unknown_tool", "message": f"Unknown tool: {name}"}}
            return [types.TextContent(type="text", text=json.dumps(payload, separators=(",", ":")))]
        result = await asyncio.to_thread(handler, dict(arguments))
        return [types.TextContent(type="text", text=result)]

    return server


async def run() -> None:
    server = create_server()
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            InitializationOptions(
                server_name="goplaces",
                server_version=__version__,
                capabilities=server.get_capabilities(
                    notification_options=NotificationOptions(),
                    experimental_capabilities={},
                ),
            ),
        )


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
