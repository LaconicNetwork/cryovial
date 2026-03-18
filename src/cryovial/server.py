"""Webhook server for deploy notifications.

Receives push notifications from GitHub Actions when new container
images are built, triggering deployment restarts.

Uses Python stdlib http.server — no framework dependencies.
"""

import json
import logging
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer

from .deploy import ServiceConfig, deploy

log = logging.getLogger(__name__)


class _ConfiguredHTTPServer(HTTPServer):
    """HTTPServer that holds webhook configuration for handler access."""

    services: dict[str, ServiceConfig]
    secret: str


class _WebhookHandler(BaseHTTPRequestHandler):
    """HTTP request handler for deploy webhook notifications.

    Expects POST /deploy/notify with Bearer auth and JSON payload:
        {"service": "service-name"}
    """

    server: _ConfiguredHTTPServer  # type: ignore[assignment]

    def do_POST(self) -> None:
        if self.path != "/deploy/notify":
            self._error(HTTPStatus.NOT_FOUND, "unknown endpoint")
            return

        if not self._check_auth():
            return

        payload = self._read_json()
        if payload is None:
            return

        service_name = payload.get("service")
        if not service_name:
            self._error(HTTPStatus.BAD_REQUEST, "missing required field: service")
            return

        service_config = self.server.services.get(service_name)
        if service_config is None:
            self._error(HTTPStatus.NOT_FOUND, f"unknown service: {service_name}")
            return

        image = payload.get("image", "")
        log.info("Accepted deploy notification: service=%s image=%s", service_name, image or "not specified")

        thread = threading.Thread(
            target=self._run_deploy,
            args=(service_config, image or None),
            daemon=True,
        )
        thread.start()

        self._respond(HTTPStatus.ACCEPTED, {"status": "accepted"})

    def do_GET(self) -> None:
        self._error(HTTPStatus.METHOD_NOT_ALLOWED, "use POST")

    def _check_auth(self) -> bool:
        auth = self.headers.get("Authorization", "")
        if not auth.startswith("Bearer ") or auth[7:] != self.server.secret:
            self._error(HTTPStatus.UNAUTHORIZED, "invalid or missing authorization")
            return False
        return True

    def _read_json(self) -> dict | None:
        content_length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(content_length)
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            self._error(HTTPStatus.BAD_REQUEST, "invalid JSON")
            return None
        if not isinstance(data, dict):
            self._error(HTTPStatus.BAD_REQUEST, "expected JSON object")
            return None
        return data

    def _run_deploy(self, service_config: ServiceConfig, image: str | None) -> None:
        try:
            deploy(service_config, image=image)
            log.info("Deploy completed: service=%s", service_config.name)
        except Exception:
            log.exception("Deploy failed: service=%s", service_config.name)

    def _respond(self, status: HTTPStatus, body: dict) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        data = json.dumps(body).encode()
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _error(self, status: HTTPStatus, message: str) -> None:
        self._respond(status, {"error": message})

    def log_message(self, format: str, *args: object) -> None:
        """Route http.server logs through the logging module."""
        log.debug(format, *args)


class WebhookServer:
    """HTTP webhook server for deploy notifications."""

    def __init__(
        self,
        services: dict[str, ServiceConfig],
        secret: str,
        port: int = 8090,
    ) -> None:
        self._httpd = _ConfiguredHTTPServer(("0.0.0.0", port), _WebhookHandler)
        self._httpd.services = services
        self._httpd.secret = secret
        self.port = self._httpd.server_address[1]

    def run(self) -> None:
        """Start serving requests. Blocks until shutdown() is called."""
        log.info("Webhook server listening on 0.0.0.0:%d", self.port)
        self._httpd.serve_forever()

    def shutdown(self) -> None:
        """Stop the server."""
        self._httpd.shutdown()
