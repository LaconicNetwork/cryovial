"""Deploy operations for cluster management.

When a SHA-tagged image is provided, restarts the deployment with
that specific image via laconic-so --image flag. Falls back to a
plain laconic-so deployment restart when no image is specified.
"""

import logging
import subprocess
from dataclasses import dataclass

log = logging.getLogger(__name__)


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


def deploy(service_config: ServiceConfig, image: str | None = None) -> None:
    """Deploy a service, optionally with a specific image tag.

    When image is provided, passes --image to laconic-so deployment
    restart so the container is updated to the exact SHA-tagged image
    from CI. When no image is provided, does a plain restart.
    """
    cmd = [
        "laconic-so",
        "deployment",
        "--dir",
        service_config.stack_name,
        "restart",
    ]
    if image:
        cmd.extend(["--image", f"{service_config.name}={image}"])
        log.info("Deploying with image: %s=%s", service_config.name, image)
    else:
        log.info("No image specified, restarting with current image")

    result = subprocess.run(
        cmd,
        cwd=service_config.repo_dir,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        log.error("Deploy failed (stdout): %s", result.stdout.strip())
        log.error("Deploy failed (stderr): %s", result.stderr.strip())
        result.check_returncode()
