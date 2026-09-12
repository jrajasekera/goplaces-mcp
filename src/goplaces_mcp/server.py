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
    schemas.GOPLACES_ROUTE_MATRIX,
    schemas.GOPLACES_REVERSE_GEOCODE,
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
    "goplaces_route_matrix": tools.goplaces_route_matrix,
    "goplaces_reverse_geocode": tools.goplaces_reverse_geocode,
}

# Keys the photo handler uses to hand image bytes to this adapter. They are
# lifted into an ImageContent block rather than repeated in the text payload.
_IMAGE_DATA_KEY = "image_base64"
_IMAGE_MIME_KEY = "image_mime_type"


def _tool_annotations(definition: Mapping[str, Any]) -> types.ToolAnnotations:
    """Every goplaces tool reads remote data and changes nothing."""
    return types.ToolAnnotations(
        title=definition.get("title"),
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    )


def _error_result(payload: dict[str, Any]) -> types.CallToolResult:
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=text)],
        structuredContent=payload,
        isError=True,
    )


def _tool_result(payload: Any, text: str) -> types.CallToolResult:
    """Wrap a handler payload, flagging error payloads on the wire.

    Hosts use ``isError`` to decide whether to retry or surface a failure, so a
    handler's machine-readable error must not arrive as ordinary text.
    """
    if not isinstance(payload, dict):
        return types.CallToolResult(content=[types.TextContent(type="text", text=text)])
    if "error" in payload:
        return _error_result(payload)

    content: list[types.ContentBlock] = []
    image_data = payload.get(_IMAGE_DATA_KEY)
    if isinstance(image_data, str) and image_data:
        payload = {key: value for key, value in payload.items() if key != _IMAGE_DATA_KEY}
        content.append(
            types.ImageContent(
                type="image",
                data=image_data,
                mimeType=str(payload.get(_IMAGE_MIME_KEY) or "image/jpeg"),
            )
        )
        text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))

    content.append(types.TextContent(type="text", text=text))
    return types.CallToolResult(content=content, structuredContent=payload)


def create_server() -> Server:
    """Create the server while preserving the existing hand-written schemas."""
    server = Server("goplaces", version=__version__, instructions=schemas.SERVER_INSTRUCTIONS)

    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        return [
            types.Tool(
                name=definition["name"],
                title=definition.get("title"),
                description=definition["description"],
                inputSchema=definition["parameters"],
                outputSchema=definition.get("output_schema"),
                annotations=_tool_annotations(definition),
            )
            for definition in _TOOL_DEFINITIONS
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: Mapping[str, Any]) -> types.CallToolResult:
        handler = _HANDLERS.get(name)
        if handler is None:
            return _error_result({"error": {"type": "unknown_tool", "message": f"Unknown tool: {name}"}})
        text = await asyncio.to_thread(handler, dict(arguments))
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return types.CallToolResult(content=[types.TextContent(type="text", text=text)])
        return _tool_result(payload, text)

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
                instructions=schemas.SERVER_INSTRUCTIONS,
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
