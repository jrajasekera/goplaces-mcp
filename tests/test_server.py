from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


def server_parameters() -> StdioServerParameters:
    env = os.environ.copy()
    env.pop("GOOGLE_PLACES_API_KEY", None)
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "goplaces_mcp.server"],
        env=env,
    )


@pytest.mark.parametrize("claude_mode", [False, True], ids=["codex", "claude"])
@pytest.mark.asyncio
async def test_shared_plugin_manifest_launches_in_both_hosts(
    claude_mode: bool, tmp_path: Path
) -> None:
    plugin_root = Path(__file__).parents[1]
    config = json.loads((plugin_root / ".mcp.json").read_text())
    definition = config["mcpServers"]["goplaces"]
    env = os.environ.copy()
    env.pop("GOOGLE_PLACES_API_KEY", None)
    if claude_mode:
        env["CLAUDE_PLUGIN_ROOT"] = str(plugin_root)
        cwd = tmp_path
    else:
        env.pop("CLAUDE_PLUGIN_ROOT", None)
        cwd = plugin_root

    parameters = StdioServerParameters(
        command=definition["command"],
        args=definition["args"],
        cwd=cwd,
        env=env,
    )
    async with stdio_client(parameters) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            result = await session.list_tools()

    assert len(result.tools) == 8


@pytest.mark.asyncio
async def test_lists_all_tools_with_existing_schemas() -> None:
    async with stdio_client(server_parameters()) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            result = await session.list_tools()

    assert [tool.name for tool in result.tools] == [
        "goplaces_search",
        "goplaces_nearby",
        "goplaces_autocomplete",
        "goplaces_details",
        "goplaces_photo",
        "goplaces_resolve",
        "goplaces_directions",
        "goplaces_route_search",
    ]
    assert all(tool.inputSchema["type"] == "object" for tool in result.tools)


@pytest.mark.asyncio
async def test_unknown_tool_returns_machine_readable_error() -> None:
    async with stdio_client(server_parameters()) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            result = await session.call_tool("missing", {})

    assert json.loads(result.content[0].text)["error"]["type"] == "unknown_tool"


@pytest.mark.asyncio
async def test_missing_api_key_is_returned_as_validation_error() -> None:
    async with stdio_client(server_parameters()) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            result = await session.call_tool("goplaces_search", {"query": "coffee"})

    payload = json.loads(result.content[0].text)
    assert payload["error"]["type"] == "validation"
    assert payload["error"]["field"] == "GOOGLE_PLACES_API_KEY"
