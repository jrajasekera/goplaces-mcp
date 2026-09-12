"""Guard the facts that are duplicated across packaging files."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest

from goplaces_mcp import __version__, schemas, server

ROOT = Path(__file__).parents[1]


def _pyproject_version() -> str:
    return tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]


def _manifest_version(relative: str) -> str:
    return json.loads((ROOT / relative).read_text())["version"]


@pytest.mark.parametrize(
    "source",
    ["pyproject.toml", ".claude-plugin/plugin.json", ".codex-plugin/plugin.json"],
)
def test_release_version_is_consistent(source: str) -> None:
    """The version lives in four places; drift between them ships a wrong number."""
    found = _pyproject_version() if source == "pyproject.toml" else _manifest_version(source)
    assert found == __version__, f"{source} says {found}, package says {__version__}"


def test_schema_registry_matches_handler_registry() -> None:
    """Schemas and handlers must stay in one-to-one correspondence."""
    schema_names = [definition["name"] for definition in server._TOOL_DEFINITIONS]
    assert schema_names == list(server._HANDLERS)
    assert len(schema_names) == len(set(schema_names))


def test_every_tool_declares_an_output_schema() -> None:
    for definition in server._TOOL_DEFINITIONS:
        assert definition.get("output_schema"), f"{definition['name']} has no output_schema"
        assert definition["output_schema"]["type"] == "object"


def test_server_instructions_are_present() -> None:
    assert schemas.SERVER_INSTRUCTIONS.strip()
    assert "goplaces_search" in schemas.SERVER_INSTRUCTIONS
