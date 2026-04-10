"""Deploy operations — laconic-so stacks and bare-host binaries.

Two deploy backends:
  - laconic_so: restarts a laconic-so deployment, optionally with a
    SHA-tagged container image.
  - artifact: downloads a pre-built binary from a URL and restarts
    a systemd service.

Deploy records are written to ~/.cryovial/deploys/ as YAML files,
tracking accept/complete/fail status with timestamps.
"""

import json
import logging
import os
import stat
import subprocess
import tempfile
import urllib.request
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import yaml

log = logging.getLogger(__name__)

DEPLOYS_DIR = Path.home() / ".cryovial" / "deploys"


@dataclass
class ServiceConfig:
    """Identity and location of a deployable service.

    Attributes:
        name: Human-readable service name (e.g., "dumpster-backend").
        deploy_type: "laconic_so" (default) or "artifact".
        stack_name: laconic-so deployment directory path (laconic_so only).
        repo_dir: Path to the stack repo (laconic_so only).
        artifact_url_template: URL template with {tag} placeholder (artifact only).
        binary_path: Install path for downloaded binary (artifact only).
        service_name: systemd service to restart (artifact only).

    """

    name: str
    deploy_type: str = "laconic_so"
    stack_name: str = ""
    repo_dir: str = ""
    artifact_url_template: str = ""
    binary_path: str = ""
    service_name: str = ""


def _short_id() -> str:
    """Generate a short deploy ID (first 8 chars of uuid4)."""
    return uuid.uuid4().hex[:8]


def _now() -> str:
    return datetime.now(tz=UTC).isoformat()


@dataclass
class DeployRecord:
    """Record of a deploy attempt, persisted as YAML.

    Written on accept, updated on completion or failure.
    """

    id: str = field(default_factory=_short_id)
    service: str = ""
    image: str = ""
    status: str = "accepted"
    accepted_at: str = field(default_factory=_now)
    completed_at: str = ""
    error: str = ""
    stdout: str = ""
    stderr: str = ""

    def _path(self) -> Path:
        return DEPLOYS_DIR / f"{self.id}.yml"

    def save(self) -> None:
        """Write record to ~/.cryovial/deploys/<id>.yml."""
        DEPLOYS_DIR.mkdir(parents=True, exist_ok=True)
        data = {
            "id": self.id,
            "service": self.service,
            "image": self.image,
            "status": self.status,
            "accepted_at": self.accepted_at,
            "completed_at": self.completed_at,
            "error": self.error,
            "stdout": self.stdout,
            "stderr": self.stderr,
        }
        self._path().write_text(yaml.dump(data, default_flow_style=False))

    def complete(self) -> None:
        self.status = "completed"
        self.completed_at = _now()
        self.save()

    def fail(self, error: str) -> None:
        self.status = "failed"
        self.completed_at = _now()
        self.error = error
        self.save()


class NamespaceTerminatingError(RuntimeError):
    """Raised when a namespace is still Terminating after timeout."""


NAMESPACE_WAIT_TIMEOUT = 120


def _wait_for_namespace(namespace: str) -> None:
    """Block until the namespace is deleted or not Terminating.

    Delegates polling to ``kubectl wait --for=delete``. If the
    namespace does not exist, kubectl returns immediately. If it
    is still present after the timeout, raises NamespaceTerminatingError.
    """
    result = subprocess.run(
        [
            "kubectl",
            "wait",
            "--for=delete",
            "namespace",
            namespace,
            f"--timeout={NAMESPACE_WAIT_TIMEOUT}s",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0 and "Terminating" in result.stderr:
        raise NamespaceTerminatingError(
            f"Namespace {namespace} still Terminating after {NAMESPACE_WAIT_TIMEOUT}s"
        )


def _deploy_laconic_so(
    service_config: ServiceConfig,
    image: str | None = None,
    record: DeployRecord | None = None,
) -> None:
    """Deploy via laconic-so deployment restart."""
    _wait_for_namespace(service_config.stack_name)

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

    if record is not None:
        record.stdout = result.stdout
        record.stderr = result.stderr

    if result.returncode != 0:
        log.error("Deploy failed (stdout): %s", result.stdout.strip())
        log.error("Deploy failed (stderr): %s", result.stderr.strip())
        result.check_returncode()


def _download_private_release(
    url: str, token: str, dest_file: tempfile.NamedTemporaryFile  # type: ignore[type-arg]
) -> None:
    """Download a release asset from a private GitHub repo.

    GitHub's release download URLs redirect to CDN, which rejects the
    Authorization header. Instead, use the API to resolve the asset ID,
    then request the asset with Accept: application/octet-stream —
    GitHub returns the binary directly without a redirect.

    Args:
        url: Browser-style release download URL containing owner/repo/tag/asset name.
        token: GitHub installation access token.
        dest_file: Open temp file to write the binary into.

    """
    # Parse owner, repo, tag, and asset name from the URL
    # Format: https://github.com/{owner}/{repo}/releases/download/{tag}/{asset}
    parts = url.split("/")
    if len(parts) < 9 or parts[5] != "releases" or parts[6] != "download":
        raise ValueError(f"Cannot parse GitHub release URL: {url}")
    owner, repo = parts[3], parts[4]
    release_tag = parts[7]
    asset_name = "/".join(parts[8:])

    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github+json",
    }

    # Step 1: Get the release by tag
    release_url = f"https://api.github.com/repos/{owner}/{repo}/releases/tags/{release_tag}"
    req = urllib.request.Request(release_url, headers=headers)
    log.info("Looking up release: %s/%s tag=%s", owner, repo, release_tag)
    with urllib.request.urlopen(req) as resp:
        release = json.loads(resp.read())

    # Step 2: Find the asset by name
    asset_id = None
    for asset in release.get("assets", []):
        if asset["name"] == asset_name:
            asset_id = asset["id"]
            break
    if not asset_id:
        available = [a["name"] for a in release.get("assets", [])]
        raise ValueError(
            f"Asset '{asset_name}' not found in release {release_tag}. "
            f"Available: {available}"
        )

    # Step 3: Download the asset via API (returns binary directly, no redirect)
    asset_url = f"https://api.github.com/repos/{owner}/{repo}/releases/assets/{asset_id}"
    dl_headers = {
        "Authorization": f"token {token}",
        "Accept": "application/octet-stream",
    }
    dl_req = urllib.request.Request(asset_url, headers=dl_headers)
    log.info("Downloading asset %s (id=%s) via API", asset_name, asset_id)
    with urllib.request.urlopen(dl_req) as resp:
        dest_file.write(resp.read())


def _deploy_artifact(
    service_config: ServiceConfig,
    tag: str | None = None,
    record: DeployRecord | None = None,
) -> None:
    """Deploy a pre-built binary artifact.

    Downloads the binary from artifact_url_template (with {tag}
    substituted), installs it to binary_path, and restarts the
    systemd service.
    """
    if not tag:
        raise ValueError("artifact deploy requires a tag (pass as 'image' in webhook payload)")

    url = service_config.artifact_url_template.replace("{tag}", tag)
    binary_path = Path(service_config.binary_path)

    log.info("Downloading artifact: %s → %s", url, binary_path)

    from cryovial.github_auth import get_token

    token = get_token()

    # Download to a temp file, then atomic rename
    with tempfile.NamedTemporaryFile(
        dir=binary_path.parent, prefix=f".{binary_path.name}.", delete=False
    ) as tmp:
        tmp_path = Path(tmp.name)
        try:
            if token:
                _download_private_release(url, token, tmp)
            else:
                urllib.request.urlretrieve(url, tmp_path)
            os.chmod(
                tmp_path,
                stat.S_IRWXU | stat.S_IRGRP | stat.S_IXGRP | stat.S_IROTH | stat.S_IXOTH,
            )
            tmp_path.rename(binary_path)
        except Exception:
            tmp_path.unlink(missing_ok=True)
            raise

    log.info("Installed %s, restarting %s", binary_path, service_config.service_name)

    result = subprocess.run(
        ["systemctl", "restart", service_config.service_name],
        capture_output=True,
        text=True,
        check=False,
    )

    if record is not None:
        record.stdout = result.stdout
        record.stderr = result.stderr

    if result.returncode != 0:
        log.error("Restart failed: %s", result.stderr.strip())
        result.check_returncode()


def deploy(
    service_config: ServiceConfig,
    image: str | None = None,
    record: DeployRecord | None = None,
) -> None:
    """Deploy a service. Dispatches to the correct backend based on deploy_type."""
    if service_config.deploy_type == "artifact":
        _deploy_artifact(service_config, tag=image, record=record)
    else:
        _deploy_laconic_so(service_config, image=image, record=record)
