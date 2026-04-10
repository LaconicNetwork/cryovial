"""GitHub App authentication for private release asset downloads.

Each cryovial host has its own GitHub App with a unique PEM key.
The flow:
  1. Sign a JWT with the app's private key (RS256, 10 min expiry)
  2. Exchange JWT for a short-lived installation access token (1 hour)
  3. Use the token to download release assets from private repos

Config (environment variables):
  GITHUB_APP_ID: The GitHub App's numeric ID
  GITHUB_APP_INSTALLATION_ID: The installation ID for the target org
  GITHUB_APP_PEM: Path to the PEM private key file

See docs/github-app-setup.md for creating per-host apps.
"""

import json
import logging
import os
import time
import urllib.request
from pathlib import Path

import jwt

log = logging.getLogger(__name__)

# Cache the token until it expires (with 60s safety margin)
_cached_token: str | None = None
_cached_token_expires: float = 0


def _load_config() -> tuple[str, str, str]:
    """Load GitHub App config from environment.

    Returns:
        Tuple of (app_id, installation_id, pem_path).

    """
    app_id = os.environ.get("GITHUB_APP_ID", "")
    installation_id = os.environ.get("GITHUB_APP_INSTALLATION_ID", "")
    pem_path = os.environ.get("GITHUB_APP_PEM", "/etc/cryovial/github-app.pem")
    return app_id, installation_id, pem_path


def _generate_jwt(app_id: str, pem_path: str) -> str:
    """Generate a JWT signed with the app's private key.

    Returns:
        Encoded JWT string (RS256).

    """
    pem = Path(pem_path).read_bytes()
    now = int(time.time())
    payload = {
        "iat": now - 60,  # issued at (60s clock skew allowance)
        "exp": now + (10 * 60),  # expires in 10 minutes (GitHub max)
        "iss": app_id,
    }
    return jwt.encode(payload, pem, algorithm="RS256")


def _exchange_for_installation_token(jwt_token: str, installation_id: str) -> tuple[str, float]:
    """Exchange a JWT for an installation access token.

    Returns:
        Tuple of (token, expires_at_unix_timestamp).

    """
    url = f"https://api.github.com/app/installations/{installation_id}/access_tokens"
    req = urllib.request.Request(
        url,
        method="POST",
        headers={
            "Authorization": f"Bearer {jwt_token}",
            "Accept": "application/vnd.github+json",
        },
    )
    with urllib.request.urlopen(req) as resp:
        data = json.loads(resp.read())

    token: str = data["token"]
    # Parse ISO 8601 expiry, fall back to 1 hour from now
    expires_at = time.time() + 3500
    return token, expires_at


def get_token() -> str | None:
    """Get a valid GitHub installation access token.

    Caches the token and refreshes when expired.

    Returns:
        Installation token string, or None if GitHub App auth is not configured.

    """
    global _cached_token, _cached_token_expires  # noqa: PLW0603

    app_id, installation_id, pem_path = _load_config()
    if not app_id or not installation_id:
        return None

    # Return cached token if still valid (60s safety margin)
    if _cached_token and time.time() < (_cached_token_expires - 60):
        return _cached_token

    if not Path(pem_path).exists():
        log.error("GitHub App PEM not found at %s", pem_path)
        return None

    log.info("Generating GitHub App installation token (app_id=%s)", app_id)
    jwt_token = _generate_jwt(app_id, pem_path)
    _cached_token, _cached_token_expires = _exchange_for_installation_token(
        jwt_token, installation_id
    )
    return _cached_token
