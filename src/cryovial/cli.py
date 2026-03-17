"""Cryovial CLI — host-resident deploy service.

Usage:
    cryovial serve --config services.yml --port 8090 --secret <token>
"""

import argparse
import logging
import os
import sys
from pathlib import Path

import yaml

from .deploy import ServiceConfig
from .server import WebhookServer


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="cryovial",
        description="Host-resident deploy service for container clusters",
    )
    subparsers = parser.add_subparsers(dest="command")

    serve_parser = subparsers.add_parser("serve", help="Start webhook server")
    serve_parser.add_argument("--config", required=True, help="Path to YAML services config")
    serve_parser.add_argument("--port", type=int, default=8090, help="Port (default: 8090)")
    serve_parser.add_argument("--secret", required=True, help="Bearer token for auth")

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        return 1

    if args.command == "serve":
        return cmd_serve(args)

    return 1


def cmd_serve(args: argparse.Namespace) -> int:
    """Start the webhook server."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    config_path = Path(args.config)
    if not config_path.exists():
        print(f"Config file not found: {config_path}", file=sys.stderr)
        return 1

    raw = yaml.safe_load(config_path.read_text())
    if not raw or "services" not in raw:
        print(f"Config must contain a 'services' key: {config_path}", file=sys.stderr)
        return 1

    services: dict[str, ServiceConfig] = {}
    for name, svc in raw["services"].items():
        for field in ("image", "health_url", "namespace", "label", "stack_name"):
            if field not in svc:
                print(f"Service '{name}' missing field: {field}", file=sys.stderr)
                return 1
        services[name] = ServiceConfig(
            name=name,
            image=svc["image"],
            health_url=svc["health_url"],
            namespace=svc["namespace"],
            label=svc["label"],
            stack_name=svc["stack_name"],
        )

    coord_dir = Path(
        os.environ.get("CRYOVIAL_COORD_DIR", str(Path.home() / ".cryovial" / "coord"))
    )
    coord_dir.mkdir(parents=True, exist_ok=True)

    print(f"Starting cryovial on port {args.port} with {len(services)} services")
    for name, svc in services.items():
        print(f"  {name}: {svc.image}")

    server = WebhookServer(
        services=services,
        coord_dir=coord_dir,
        secret=args.secret,
        port=args.port,
    )
    server.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
