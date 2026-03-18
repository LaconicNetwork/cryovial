"""Deploy operations for cluster management.

Triggers laconic-so deployment restart. The cluster pulls images
from GHCR directly (imagePullPolicy: Always).
"""

import subprocess
from dataclasses import dataclass


@dataclass
class ServiceConfig:
    """Identity and location of a deployable service.

    Attributes:
        name: Human-readable service name (e.g., "dumpster-backend").
        stack_name: laconic-so deployment directory path.
    """

    name: str
    stack_name: str


def deploy(service_config: ServiceConfig) -> None:
    """Restart a service deployment.

    Runs laconic-so deployment restart, which recreates pods.
    With imagePullPolicy: Always, k8s pulls the latest image from GHCR.
    """
    subprocess.run(
        [
            "laconic-so",
            "deployment",
            "--dir",
            service_config.stack_name,
            "restart",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
