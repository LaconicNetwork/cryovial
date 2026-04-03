"""Webhook server for deploy notifications.

Receives push notifications from GitHub Actions when new container
images are built, triggering deployment restarts.

Uses Python stdlib http.server — no framework dependencies.
"""

import json
import logging
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

from .deploy import DeployRecord, ServiceConfig, deploy

log = logging.getLogger(__name__)

COOLDOWN_SECONDS = 300  # 5 minutes


class _ConfiguredHTTPServer(HTTPServer):
    """HTTPServer that holds webhook configuration for handler access."""

    allow_reuse_address = True

    services: dict[str, ServiceConfig]
    secret: str
    last_deploy: dict[str, float]  # stack_name -> monotonic timestamp


class _WebhookHandler(BaseHTTPRequestHandler):
    """HTTP request handler for deploy webhook notifications.

    Expects POST /deploy/notify with Bearer auth and JSON payload:
        {"service": "service-name"}
    """

    server: _ConfiguredHTTPServer

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

        stack = service_config.stack_name or service_config.service_name
        now = time.monotonic()
        last = self.server.last_deploy.get(stack, 0.0)
        elapsed = now - last
        if last > 0 and elapsed < COOLDOWN_SECONDS:
            remaining = int(COOLDOWN_SECONDS - elapsed)
            log.warning(
                "Cooldown active: service=%s stack=%s retry_after=%ds",
                service_name,
                stack,
                remaining,
            )
            self.send_response(HTTPStatus.TOO_MANY_REQUESTS)
            self.send_header("Content-Type", "application/json")
            self.send_header("Retry-After", str(remaining))
            body = json.dumps({"error": "cooldown active", "retry_after": remaining}).encode()
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        self.server.last_deploy[stack] = now

        image = payload.get("image", "")
        log.info(
            "Accepted deploy notification: service=%s image=%s",
            service_name,
            image or "not specified",
        )

        record = DeployRecord(service=service_name, image=image)
        record.save()

        thread = threading.Thread(
            target=self._run_deploy,
            args=(service_config, image or None, record),
            daemon=True,
        )
        thread.start()

        self._respond(HTTPStatus.ACCEPTED, {"status": "accepted", "deploy_id": record.id})

    def do_GET(self) -> None:
        self._error(HTTPStatus.METHOD_NOT_ALLOWED, "use POST")

    def _check_auth(self) -> bool:
        auth = self.headers.get("Authorization", "")
        if not auth.startswith("Bearer ") or auth[7:] != self.server.secret:
            self._error(HTTPStatus.UNAUTHORIZED, "invalid or missing authorization")
            return False
        return True

    def _read_json(self) -> dict[str, Any] | None:
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

    def _run_deploy(
        self,
        service_config: ServiceConfig,
        image: str | None,
        record: DeployRecord,
    ) -> None:
        try:
            deploy(service_config, image=image, record=record)
            record.complete()
            log.info("Deploy completed: service=%s id=%s", service_config.name, record.id)
        except Exception as exc:
            error_parts = [str(exc)]
            if record.stdout:
                error_parts.append(f"stdout: {record.stdout.strip()}")
            if record.stderr:
                error_parts.append(f"stderr: {record.stderr.strip()}")
            record.fail(error="\n".join(error_parts))
            log.exception("Deploy failed: service=%s id=%s", service_config.name, record.id)

    def _respond(self, status: HTTPStatus, body: dict[str, Any]) -> None:
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
        self._httpd.last_deploy = {}
        self.port = self._httpd.server_address[1]

    def run(self) -> None:
        """Start serving requests. Blocks until shutdown() is called."""
        log.info("Webhook server listening on 0.0.0.0:%d", self.port)
        self._httpd.serve_forever()

    def shutdown(self) -> None:
        """Stop the server."""
        self._httpd.shutdown()
