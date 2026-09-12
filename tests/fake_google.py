"""In-process HTTP stand-in for the Google Places and Routes APIs.

Handlers talk to Google through :mod:`urllib`, so the cheapest way to exercise
them end to end is to point the documented base-URL environment variables at a
localhost server that replays canned JSON. Nothing here touches the network or
needs credentials.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse


@dataclass
class RecordedRequest:
    """One request the handlers made, captured for assertions."""

    method: str
    path: str
    query: dict[str, list[str]]
    headers: dict[str, str]
    body: dict[str, Any] | None

    @property
    def field_mask(self) -> str:
        return self.headers.get("x-goog-fieldmask", "")

    @property
    def api_key(self) -> str:
        return self.headers.get("x-goog-api-key", "")

    def mask_tokens(self) -> set[str]:
        return {token for token in self.field_mask.split(",") if token}


@dataclass
class Response:
    """A canned reply. ``delay`` lets tests assert on concurrency."""

    payload: Any
    status: int = 200
    delay: float = 0.0


# A route handler receives the request and returns a Response.
Route = Callable[[RecordedRequest], Response]


@dataclass
class FakeGoogle:
    """Routes requests by path suffix to canned responses."""

    requests: list[RecordedRequest] = field(default_factory=list)
    _routes: dict[str, Route] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    base_url: str = ""

    def reset(self) -> None:
        """Clear recorded traffic and canned routes between tests."""
        with self._lock:
            self.requests.clear()
            self._routes.clear()

    def reply(self, path_suffix: str, payload: Any, *, status: int = 200, delay: float = 0.0) -> None:
        """Always answer requests whose path ends with ``path_suffix``."""
        response = Response(payload=payload, status=status, delay=delay)
        self.route(path_suffix, lambda _request: response)

    def sequence(self, path_suffix: str, responses: list[Response]) -> None:
        """Answer successive requests to ``path_suffix`` from a queue.

        The final entry repeats once the queue is drained, which keeps retry
        tests from depending on an exact call count.
        """
        remaining = list(responses)

        def handler(_request: RecordedRequest) -> Response:
            with self._lock:
                if len(remaining) > 1:
                    return remaining.pop(0)
                return remaining[0]

        self.route(path_suffix, handler)

    def route(self, path_suffix: str, handler: Route) -> None:
        self._routes[path_suffix] = handler

    def resolve(self, request: RecordedRequest) -> Response:
        for suffix, handler in self._routes.items():
            if request.path.endswith(suffix):
                return handler(request)
        return Response(
            payload={"error": {"code": 404, "message": f"no fixture for {request.path}"}},
            status=404,
        )

    def record(self, request: RecordedRequest) -> None:
        with self._lock:
            self.requests.append(request)

    def requests_to(self, path_suffix: str) -> list[RecordedRequest]:
        with self._lock:
            return [item for item in self.requests if item.path.endswith(path_suffix)]

    def last_request(self) -> RecordedRequest:
        with self._lock:
            return self.requests[-1]


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    fake: FakeGoogle

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self._handle("GET")

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self._handle("POST")

    def _handle(self, method: str) -> None:
        parsed = urlparse(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            body = json.loads(raw.decode("utf-8")) if raw else None
        except json.JSONDecodeError:
            body = None
        request = RecordedRequest(
            method=method,
            path=parsed.path,
            query=parse_qs(parsed.query),
            headers={key.lower(): value for key, value in self.headers.items()},
            body=body,
        )
        self.fake.record(request)
        response = self.fake.resolve(request)
        if response.delay:
            import time

            time.sleep(response.delay)
        if isinstance(response.payload, (bytes, bytearray)):
            encoded = bytes(response.payload)
            content_type = "image/jpeg"
        else:
            encoded = json.dumps(response.payload).encode("utf-8")
            content_type = "application/json"
        self.send_response(response.status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, *_args: Any) -> None:
        """Silence the default stderr access log."""


class FakeGoogleServer:
    """Context manager owning the background HTTP thread."""

    def __init__(self) -> None:
        self.fake = FakeGoogle()
        handler = type("_BoundHandler", (_Handler,), {"fake": self.fake})
        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        host, port = self._httpd.server_address[:2]
        self.fake.base_url = f"http://{host}:{port}"

    def __enter__(self) -> FakeGoogle:
        self._thread.start()
        return self.fake

    def __exit__(self, *_exc: Any) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
        self._thread.join(timeout=5)
