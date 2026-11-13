"""Local HTTP bridge -- lets any device that can send a web request use Jarvis.

Phone shortcut, TV app, smart speaker, home-automation hub, a script on another
machine: they all speak the same tiny protocol.

    POST http://HOST:PORT/ask      {"text": "turn the desk lamp off"}
    ->   {"reply": "I cannot control that device yet."}

    GET  http://HOST:PORT/health
    ->   {"ok": true, "device": "http_bridge"}

Safety: it binds to ``127.0.0.1`` by default, so only this PC can reach it. If
you change ``host`` to a network address to reach it from your phone, an
``auth_token`` becomes mandatory, and clients must send it as
``Authorization: Bearer <token>`` or ``X-Jarvis-Token: <token>``.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Optional

from devices import DeviceAdapter

LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


class HttpBridge(DeviceAdapter):
    name = "http_bridge"

    def __init__(self, assistant: Any, options: Optional[dict[str, Any]] = None) -> None:
        super().__init__(assistant, options)
        self.host = str(self.options.get("host", "127.0.0.1"))
        self.port = int(self.options.get("port", 8765))
        self.token = str(self.options.get("auth_token", "") or "")
        self.speak = bool(self.options.get("speak", True))
        self._server: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    # -- lifecycle ----------------------------------------------------------

    def start(self) -> None:
        if self.host not in LOOPBACK_HOSTS and not self.token:
            raise RuntimeError(
                "refusing to listen on a network address without 'auth_token' in devices.json"
            )

        bridge = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "JarvisBridge/1.0"

            def log_message(self, fmt: str, *args: Any) -> None:  # noqa: D102 - quiet server
                pass

            def _send(self, code: int, payload: dict[str, Any]) -> None:
                body = json.dumps(payload).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _authorised(self) -> bool:
                if not bridge.token:
                    return True
                header = self.headers.get("Authorization", "")
                provided = (
                    header[7:]
                    if header.lower().startswith("bearer ")
                    else self.headers.get("X-Jarvis-Token", "")
                )
                return provided == bridge.token

            def do_GET(self) -> None:  # noqa: N802 - http.server API
                if self.path != "/health":
                    self._send(404, {"error": "not found"})
                    return
                self._send(200, {"ok": True, "device": "http_bridge"})

            def do_POST(self) -> None:  # noqa: N802 - http.server API
                if self.path != "/ask":
                    self._send(404, {"error": "not found"})
                    return
                if not self._authorised():
                    self._send(401, {"error": "unauthorised"})
                    return

                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b"{}"
                try:
                    payload = json.loads(raw or b"{}")
                except ValueError:
                    self._send(400, {"error": "body must be JSON"})
                    return

                text = str(payload.get("text", "")).strip()
                if not text:
                    self._send(400, {"error": "missing 'text'"})
                    return

                try:
                    reply = bridge.assistant.respond(text, speak=bridge.speak)
                except Exception as exc:  # noqa: BLE001 - report, keep serving
                    self._send(500, {"error": str(exc)})
                    return
                self._send(200, {"reply": reply})

        self._server = ThreadingHTTPServer((self.host, self.port), Handler)
        self._thread = threading.Thread(
            target=self._server.serve_forever, name="jarvis-http-bridge", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        server = self._server
        self._server = None
        if server is not None:
            try:
                server.shutdown()
                server.server_close()
            except Exception:  # noqa: BLE001
                pass

    def describe(self) -> str:
        return f"{self.name} on http://{self.host}:{self.port}"


DEVICE = HttpBridge
