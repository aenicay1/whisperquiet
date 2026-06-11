"""Localhost HTTP bridge so a local settings page can read and tweak live config."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
}


class SettingsServer:
    """Serves GET/POST /config on 127.0.0.1 from a daemon thread."""

    def __init__(self, get_state, apply_state, port: int = 8377):
        self._get_state = get_state
        self._apply_state = apply_state
        self._port = port
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

            def _respond(self, status: int, payload=None) -> None:
                body = b"" if payload is None else json.dumps(payload).encode()
                self.send_response(status)
                for name, value in CORS_HEADERS.items():
                    self.send_header(name, value)
                if body:
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                if body:
                    self.wfile.write(body)

            def do_OPTIONS(self):
                self._respond(204)

            def do_GET(self):
                if self.path != "/config":
                    return self._respond(404, {"ok": False, "error": "not found"})
                try:
                    state = bridge._get_state()
                except Exception as exc:  # handler must never kill the server
                    return self._respond(500, {"ok": False, "error": str(exc)})
                self._respond(200, state)

            def do_POST(self):
                if self.path != "/config":
                    return self._respond(404, {"ok": False, "error": "not found"})
                length = int(self.headers.get("Content-Length") or 0)
                try:
                    data = json.loads(self.rfile.read(length))
                except (json.JSONDecodeError, UnicodeDecodeError):
                    return self._respond(400, {"ok": False, "error": "malformed JSON"})
                if not isinstance(data, dict):
                    return self._respond(400, {"ok": False, "error": "expected a JSON object"})
                try:
                    bridge._apply_state(data)
                except Exception as exc:  # handler must never kill the server
                    return self._respond(500, {"ok": False, "error": str(exc)})
                self._respond(200, {"ok": True, "applied": len(data)})

        return Handler
