"""Tests for cryovial.server — SO_REUSEADDR and server lifecycle."""

import socket

from cryovial.deploy import ServiceConfig
from cryovial.server import WebhookServer


def _make_server(port: int = 0) -> WebhookServer:
    """Create a WebhookServer on an ephemeral port."""
    services = {
        "test-svc": ServiceConfig(
            name="test-svc",
            stack_name="/tmp/test-stack",
            repo_dir="/tmp",
        )
    }
    return WebhookServer(services=services, secret="test-secret", port=port)


def test_so_reuseaddr_set() -> None:
    """Server socket must have SO_REUSEADDR enabled."""
    server = _make_server()
    try:
        sock = server._httpd.socket
        reuse = sock.getsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR)
        assert reuse != 0, "SO_REUSEADDR must be set on the server socket"
    finally:
        server._httpd.server_close()


def test_sequential_bind_same_port() -> None:
    """Two servers must be able to bind the same port sequentially."""
    server1 = _make_server()
    port = server1.port
    server1._httpd.server_close()

    # Without SO_REUSEADDR this would fail with OSError: Address already in use
    server2 = _make_server(port=port)
    try:
        assert server2.port == port
    finally:
        server2._httpd.server_close()
