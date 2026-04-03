"""Cryovial CLI — host-resident deploy service.

Usage:
    cryovial serve --config services.yml --port 8090 --secret <token>
    cryovial self-update
"""

import argparse
import logging
import os
import subprocess
import sys
from pathlib import Path

import yaml

from .deploy import ServiceConfig
from .server import WebhookServer

REPO_URL = "git+https://github.com/AFDudley/cryovial.git"


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="cryovial",
        description="Host-resident deploy service for container clusters",
    )
    subparsers = parser.add_subparsers(dest="command")

    serve_parser = subparsers.add_parser("serve", help="Start webhook server")
    serve_parser.add_argument(
        "--config", default=None, help="Path to YAML services config (or CRYOVIAL_CONFIG env var)"
    )
    serve_parser.add_argument("--port", type=int, default=8090, help="Port (default: 8090)")
    serve_parser.add_argument(
        "--secret", default=None, help="Bearer token for auth (or CRYOVIAL_SECRET env var)"
    )

    subparsers.add_parser("self-update", help="Update cryovial to latest version")

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        return 1

    if args.command == "serve":
        return cmd_serve(args)

    if args.command == "self-update":
        return cmd_self_update()

    return 1


def cmd_serve(args: argparse.Namespace) -> int:
    """Start the webhook server."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    secret = args.secret or os.environ.get("CRYOVIAL_SECRET")
    if not secret:
        print("Error: --secret or CRYOVIAL_SECRET env var required", file=sys.stderr)
        return 1

    config = args.config or os.environ.get("CRYOVIAL_CONFIG")
    if not config:
        print("Error: --config or CRYOVIAL_CONFIG env var required", file=sys.stderr)
        return 1

    config_path = Path(config)
    if not config_path.exists():
        print(f"Config file not found: {config_path}", file=sys.stderr)
        return 1

    raw = yaml.safe_load(config_path.read_text())
    if not raw or "services" not in raw:
        print(f"Config must contain a 'services' key: {config_path}", file=sys.stderr)
        return 1

    services: dict[str, ServiceConfig] = {}
    for name, svc in raw["services"].items():
        deploy_type = svc.get("deploy_type", "laconic_so")
        if deploy_type == "artifact":
            for req_field in ("artifact_url_template", "binary_path", "service_name"):
                if req_field not in svc:
                    print(f"Service '{name}' (artifact) missing field: {req_field}", file=sys.stderr)
                    return 1
            services[name] = ServiceConfig(
                name=name,
                deploy_type="artifact",
                artifact_url_template=svc["artifact_url_template"],
                binary_path=svc["binary_path"],
                service_name=svc["service_name"],
            )
        else:
            for req_field in ("stack_name", "repo_dir"):
                if req_field not in svc:
                    print(f"Service '{name}' missing field: {req_field}", file=sys.stderr)
                    return 1
            services[name] = ServiceConfig(
                name=name,
                deploy_type="laconic_so",
                stack_name=svc["stack_name"],
                repo_dir=svc["repo_dir"],
            )

    print(f"Starting cryovial on port {args.port} with {len(services)} services")
    for name, svc in services.items():
        print(f"  {name}: {svc.stack_name}")

    server = WebhookServer(
        services=services,
        secret=secret,
        port=args.port,
    )
    server.run()
    return 0


def cmd_self_update() -> int:
    """Update cryovial to latest version from GitHub."""
    print(f"Updating cryovial from {REPO_URL}")
    result = subprocess.run(
        ["uv", "tool", "install", "--force", "--upgrade", REPO_URL],
        text=True,
    )
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
