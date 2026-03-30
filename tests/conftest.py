"""Shared test fixtures for cryovial."""

import pytest

from cryovial.deploy import ServiceConfig


@pytest.fixture()
def dummy_services() -> dict[str, ServiceConfig]:
    """Minimal service config for testing."""
    return {
        "test-svc": ServiceConfig(
            name="test-svc",
            stack_name="/tmp/test-stack",
            repo_dir="/tmp",
        )
    }
