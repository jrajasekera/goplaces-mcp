"""Shared fixtures: a localhost Google stand-in wired through the base-URL vars."""

from __future__ import annotations

import json
from typing import Any, Iterator

import pytest

from goplaces_mcp import tools

from .fake_google import FakeGoogle, FakeGoogleServer

_BASE_URL_VARS = (
    "GOOGLE_PLACES_BASE_URL",
    "GOOGLE_ROUTES_BASE_URL",
    "GOOGLE_DIRECTIONS_BASE_URL",
)


@pytest.fixture(scope="session")
def _google_server() -> Iterator[FakeGoogle]:
    """One background HTTP server for the whole run; state is reset per test."""
    with FakeGoogleServer() as fake:
        yield fake


@pytest.fixture
def google(_google_server: FakeGoogle, monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeGoogle]:
    """Point the client at a canned-response server for the duration of a test."""
    _google_server.reset()
    monkeypatch.setenv("GOOGLE_PLACES_API_KEY", "test-key")
    monkeypatch.setenv("GOOGLE_PLACES_TIMEOUT_SECONDS", "5")
    for name in _BASE_URL_VARS:
        monkeypatch.setenv(name, _google_server.base_url)
    # Retries sleep between attempts; keep the backoff negligible in tests.
    monkeypatch.setenv("GOOGLE_PLACES_RETRY_BASE_DELAY_SECONDS", "0")
    yield _google_server


def call(handler: Any, args: dict[str, Any] | None = None) -> dict[str, Any]:
    """Invoke a tool handler and decode its JSON string result."""
    return json.loads(handler(args or {}))
