"""Localhost HTTP bridge so a local settings page can read and tweak live config."""

from __future__ import annotations

import json
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
}


class SettingsServer:
    """Serves the local playground bridge and Preferences UI on 127.0.0.1."""

    def __init__(
        self,
        get_state,
        apply_state,
        port: int = 8377,
        *,
        get_preferences=None,
        apply_preferences=None,
        preferences_html: str | None = None,
    ):
        self._get_state = get_state
        self._apply_state = apply_state
        self._get_preferences = get_preferences or get_state
        self._apply_preferences = apply_preferences or apply_state
        self._preferences_html = preferences_html
        self._port = port
        self._token = secrets.token_urlsafe(24)
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> int:
        handler = self._make_handler()
        try:
            self._server = ThreadingHTTPServer(("127.0.0.1", self._port), handler)
        except OSError:
            # Requested port taken (stale instance, another app): fall back to ephemeral.
            self._server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self._thread = threading.Thread(
            target=self._server.serve_forever, name="settings-server", daemon=True
        )
        self._thread.start()
        return self._server.server_address[1]

    def stop(self) -> None:
        if self._server is None:
            return
        self._server.shutdown()
        self._server.server_close()
        self._server = None
        self._thread = None

    def _make_handler(self) -> type[BaseHTTPRequestHandler]:
        bridge = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format, *args):  # noqa: A002 - stdlib signature
                pass

            def _respond(
                self,
                status: int,
                payload=None,
                *,
                cors: bool = False,
                content_type: str = "application/json",
            ) -> None:
                if payload is None:
                    body = b""
                elif isinstance(payload, bytes):
                    body = payload
                elif isinstance(payload, str):
                    body = payload.encode()
                else:
                    body = json.dumps(payload).encode()
                self.send_response(status)
                if cors:
                    for name, value in CORS_HEADERS.items():
                        self.send_header(name, value)
                if body:
                    self.send_header("Content-Type", content_type)
                    self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                if body:
                    self.wfile.write(body)

            def do_OPTIONS(self):
                path = urlparse(self.path).path
                self._respond(204, cors=path == "/config")

            def do_GET(self):
                path = urlparse(self.path).path
                if path == "/" or path == "/index.html":
                    html = bridge._preferences_html or _default_preferences_html()
                    html = (
                        html.replace("__WQ_TOKEN__", bridge._token)
                        .replace("__WQ_PORT__", str(bridge._server.server_address[1]))
                    )
                    return self._respond(200, html, content_type="text/html; charset=utf-8")
                if path == "/api/state":
                    try:
                        state = bridge._get_preferences()
                    except Exception as exc:  # handler must never kill the server
                        return self._respond(500, {"ok": False, "error": str(exc)})
                    return self._respond(200, state)
                if path != "/config":
                    return self._respond(404, {"ok": False, "error": "not found"})
                try:
                    state = bridge._get_state()
                except Exception as exc:  # handler must never kill the server
                    return self._respond(500, {"ok": False, "error": str(exc)})
                self._respond(200, state, cors=True)

            def do_POST(self):
                path = urlparse(self.path).path
                if path not in ("/config", "/api/state"):
                    return self._respond(404, {"ok": False, "error": "not found"})
                if path == "/api/state" and self.headers.get("X-WQ-Token") != bridge._token:
                    return self._respond(403, {"ok": False, "error": "forbidden"})
                length = int(self.headers.get("Content-Length") or 0)
                try:
                    data = json.loads(self.rfile.read(length))
                except (json.JSONDecodeError, UnicodeDecodeError):
                    return self._respond(
                        400,
                        {"ok": False, "error": "malformed JSON"},
                        cors=path == "/config",
                    )
                if not isinstance(data, dict):
                    return self._respond(
                        400,
                        {"ok": False, "error": "expected a JSON object"},
                        cors=path == "/config",
                    )
                try:
                    if path == "/api/state":
                        bridge._apply_preferences(data)
                    else:
                        bridge._apply_state(data)
                except Exception as exc:  # handler must never kill the server
                    return self._respond(
                        500,
                        {"ok": False, "error": str(exc)},
                        cors=path == "/config",
                    )
                self._respond(200, {"ok": True, "applied": len(data)}, cors=path == "/config")

        return Handler


def _default_preferences_html() -> str:
    return """<!doctype html>
<meta charset="utf-8">
<title>WhisperQuiet Preferences</title>
<body>
  <h1>WhisperQuiet Preferences</h1>
  <p>This build has no bundled preferences UI.</p>
</body>
"""
