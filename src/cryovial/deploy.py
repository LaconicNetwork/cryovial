"""Deploy operations for cluster management.

Wraps laconic-so deployment lifecycle: deploy, rollback, restart.
All cluster mutations go through laconic-so — never raw kubectl.

Deploy records are written to coord/deploys/{id}.yaml for state tracking.
"""

import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import yaml

from b62ids import generate_id


@dataclass
class ServiceConfig:
    """Identity and location of a deployable service.

    Attributes:
        name: Human-readable service name (e.g., "dumpster-backend").
        image: Base image without digest (e.g., "ghcr.io/org/dumpster-backend").
        health_url: URL for HTTP health checks.
        namespace: Kubernetes namespace.
        label: Kubernetes label selector (e.g., "app=dumpster").
        stack_name: laconic-so deployment directory path.
    """

    name: str
    image: str
    health_url: str
    namespace: str
    label: str
    stack_name: str


VALID_DEPLOY_STATUSES = {"pending", "deploying", "deployed", "failed", "rolled_back"}


@dataclass
class DeployRecord:
    """Record of a deployment operation.

    Written to coord/deploys/{id}.yaml after each deploy, rollback, or restart.
    """

    id: str
    service: str
    image: str
    previous_image: str
    status: str = "pending"
    deployed_at: str = ""
    health_check: dict | None = field(default=None)

    def save(self, coord_dir: Path) -> None:
        """Write deploy record to coord/deploys/{id}.yaml."""
        deploys_dir = coord_dir / "deploys"
        deploys_dir.mkdir(parents=True, exist_ok=True)
        path = deploys_dir / f"{self.id}.yaml"
        with open(path, "w") as f:
            yaml.dump(self.to_dict(), f, default_flow_style=False, sort_keys=False)

    def to_dict(self) -> dict:
        """Convert to a plain dict for YAML serialization."""
        return {
            "id": self.id,
            "service": self.service,
            "image": self.image,
            "previous_image": self.previous_image,
            "status": self.status,
            "deployed_at": self.deployed_at,
            "health_check": self.health_check,
        }

    @classmethod
    def from_yaml(cls, path: Path) -> "DeployRecord":
        """Load deploy record from a YAML file."""
        with open(path) as f:
            data = yaml.safe_load(f)

        if not data or not isinstance(data, dict):
            raise ValueError(f"Invalid deploy record: {path}")

        for required in ("id", "service", "image"):
            if required not in data:
                raise ValueError(f"Missing required field '{required}': {path}")

        return cls(
            id=data["id"],
            service=data["service"],
            image=data["image"],
            previous_image=data.get("previous_image", ""),
            status=data.get("status", "pending"),
            deployed_at=data.get("deployed_at", ""),
            health_check=data.get("health_check"),
        )


def _run(args: list[str], check: bool = True) -> subprocess.CompletedProcess[str]:
    """Run a subprocess, capturing output."""
    return subprocess.run(args, capture_output=True, text=True, check=check)


def _get_cluster_name(service_config: ServiceConfig) -> str:
    """Get the kind cluster name from the deployment's deployment.yml.

    Each laconic-so deployment records its cluster-id in deployment.yml.
    This is the source of truth — not `kind get clusters`.
    """
    deployment_yml = Path(service_config.stack_name) / "deployment.yml"
    with open(deployment_yml) as f:
        data = yaml.safe_load(f)
    cluster_id = data.get("cluster-id")
    if not cluster_id:
        raise RuntimeError(f"No cluster-id in {deployment_yml}")
    return cluster_id


def _execute_deploy_operation(
    record: DeployRecord,
    coord_dir: Path,
    operation: Callable[[], None],
    success_status: str,
) -> DeployRecord:
    """Execute a deploy operation with standardized error handling."""
    try:
        operation()
    except subprocess.CalledProcessError as e:
        record.status = "failed"
        record.deployed_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        record.health_check = {"error": e.stderr}
        record.save(coord_dir)
        raise

    record.status = success_status
    record.deployed_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    record.save(coord_dir)
    return record


def deploy(
    service_config: ServiceConfig,
    image: str,
    coord_dir: Path,
) -> DeployRecord:
    """Deploy a new image for a service.

    Steps: docker pull -> kind load docker-image -> laconic-so deployment restart.
    """
    previous = get_latest_deploy(service_config.name, coord_dir)
    previous_image = previous.image if previous else ""

    record = DeployRecord(
        id=generate_id("deploy"),
        service=service_config.name,
        image=image,
        previous_image=previous_image,
        status="deploying",
    )
    record.save(coord_dir)

    def _do_deploy() -> None:
        _run(["docker", "pull", image])
        _run(["kind", "load", "docker-image", image, "--name", _get_cluster_name(service_config)])
        _run(
            [
                "laconic-so",
                "deployment",
                "--dir",
                service_config.stack_name,
                "restart",
            ]
        )

    return _execute_deploy_operation(record, coord_dir, _do_deploy, "deployed")


def rollback(
    deploy_record: DeployRecord,
    service_config: ServiceConfig,
    coord_dir: Path,
) -> DeployRecord:
    """Rollback to the previous image."""
    if not deploy_record.previous_image:
        raise ValueError(f"No previous image for deploy {deploy_record.id}")

    record = DeployRecord(
        id=generate_id("deploy"),
        service=deploy_record.service,
        image=deploy_record.previous_image,
        previous_image=deploy_record.image,
        status="deploying",
    )
    record.save(coord_dir)

    def _do_rollback() -> None:
        _run(["kind", "load", "docker-image", deploy_record.previous_image, "--name", _get_cluster_name(service_config)])
        _run(
            [
                "laconic-so",
                "deployment",
                "--dir",
                service_config.stack_name,
                "restart",
            ]
        )

    return _execute_deploy_operation(record, coord_dir, _do_rollback, "rolled_back")


def get_latest_deploy(service: str, coord_dir: Path) -> DeployRecord | None:
    """Get the most recent deploy record for a service."""
    deploys_dir = coord_dir / "deploys"
    if not deploys_dir.exists():
        return None

    latest: DeployRecord | None = None
    for path in deploys_dir.glob("*.yaml"):
        record = DeployRecord.from_yaml(path)
        if record.service != service:
            continue
        if latest is None or record.id > latest.id:
            latest = record

    return latest
