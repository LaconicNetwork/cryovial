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
        repo_dir: Path to the stack repo (cwd for laconic-so commands).
    """

    name: str
    stack_name: str
    repo_dir: str


def deploy(service_config: ServiceConfig) -> None:
    """Restart a service deployment.

    Runs laconic-so deployment restart from the repo directory so
    relative stack-source paths in deployment.yml resolve correctly.
    With imagePullPolicy: Always, k8s pulls the latest image from GHCR.
    """
    import logging

    log = logging.getLogger(__name__)

    result = subprocess.run(
        [
            "laconic-so",
            "deployment",
            "--dir",
            service_config.stack_name,
            "restart",
        ],
        cwd=service_config.repo_dir,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        log.error("laconic-so restart failed (stdout): %s", result.stdout.strip())
        log.error("laconic-so restart failed (stderr): %s", result.stderr.strip())
        result.check_returncode()
