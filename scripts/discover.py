#!/usr/bin/env python3
"""Discover existing Docker containers and Cloudflare tunnels.

Shows what's currently running and what the tunnel manager could adopt.
Usage: source .venv/bin/activate && python scripts/discover.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app.clients.cloudflare.client import CloudflareClient
from app.clients.docker.helpers import (
    get_compose_identity,
    get_container_name,
    get_exposed_ports,
    get_networks,
    is_managed,
)


def load_token() -> str:
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if line.startswith("CF_TOKEN="):
                return line.split("=", 1)[1].strip()
    return ""


async def discover_docker() -> None:
    """Discover containers via local Docker socket."""
    import docker

    client = docker.from_env()
    containers = client.containers.list(all=True)

    print("=" * 70)
    print("DOCKER CONTAINERS")
    print("=" * 70)

    # Group by compose project
    projects: dict[str, list] = {}
    standalone: list = []

    for c in containers:
        attrs = c.attrs
        project, service = get_compose_identity(attrs)
        name = get_container_name(attrs)
        status = attrs["State"]["Status"]
        image = attrs["Config"]["Image"]
        nets = get_networks(attrs)
        ports = get_exposed_ports(attrs)
        is_cf = "cloudflared" in image.lower()

        entry = {
            "name": name,
            "service": service,
            "image": image,
            "status": status,
            "networks": nets,
            "ports": ports,
            "is_cloudflared": is_cf,
            "managed": is_managed(attrs),
            "container_id": attrs["Id"][:12],
        }

        if project:
            projects.setdefault(project, []).append(entry)
        else:
            standalone.append(entry)

    for project, entries in sorted(projects.items()):
        cf_entries = [e for e in entries if e["is_cloudflared"]]
        app_entries = [e for e in entries if not e["is_cloudflared"]]

        print(f"\n  Project: {project}")
        if cf_entries:
            print(f"  {'⚡' * 1} Has cloudflared sidecar(s):")
            for e in cf_entries:
                print(f"      {e['name']} ({e['status']}) — {e['image']}")
                print(f"      Networks: {', '.join(e['networks'])}")
        else:
            print(f"    No cloudflared sidecar")

        print(f"    Services:")
        for e in app_entries:
            ports_str = f" ports={e['ports']}" if e["ports"] else ""
            print(f"      {e['service'] or e['name']} ({e['status']}){ports_str}")
            print(f"        image: {e['image']}")
            print(f"        networks: {', '.join(e['networks'])}")

    if standalone:
        print(f"\n  Standalone (no compose):")
        for e in standalone:
            print(f"    {e['name']} — {e['image']} ({e['status']})")

    print()
    total_cf = sum(1 for p in projects.values() for e in p if e["is_cloudflared"])
    print(f"  Summary: {len(projects)} compose projects, {total_cf} cloudflared container(s)")
    print()


async def discover_cloudflare(token: str) -> None:
    """Discover tunnels and DNS via Cloudflare API."""
    if not token:
        print("=" * 70)
        print("CLOUDFLARE (skipped — no CF_TOKEN in .env)")
        print("=" * 70)
        return

    client = CloudflareClient(token=token)
    try:
        print("=" * 70)
        print("CLOUDFLARE API")
        print("=" * 70)

        # Verify token
        result = await client.verify_token()
        print(f"\n  Token status: {result.get('status', 'unknown')}")

        # Accounts
        accounts = await client.list_accounts()
        if not accounts:
            print("  No accounts accessible with this token")
            return

        for acc in accounts:
            account_id = acc["id"]
            print(f"\n  Account: {acc['name']} ({account_id[:12]}...)")

            # Zones
            zones = await client.list_zones(account_id)
            print(f"\n    Zones ({len(zones)}):")
            for z in zones:
                print(f"      {z['name']} — {z.get('status', '?')} ({z.get('plan', {}).get('name', '?')})")

            # Tunnels
            tunnels = await client.list_tunnels(account_id)
            print(f"\n    Tunnels ({len(tunnels)}):")
            if not tunnels:
                print(f"      (none)")
            for t in tunnels:
                conns = await client.get_tunnel_connections(account_id, t["id"])
                conn_str = f"{len(conns)} connections" if conns else "disconnected"
                print(f"      {t['name']} — {t['id'][:12]}... ({conn_str})")

                # Show ingress config
                try:
                    config = await client.get_tunnel_config(account_id, t["id"])
                    ingress = config.get("config", {}).get("ingress", [])
                    for rule in ingress:
                        hostname = rule.get("hostname", "(catch-all)")
                        service = rule.get("service", "?")
                        print(f"        {hostname} -> {service}")
                except Exception:
                    print(f"        (could not fetch config)")

        print()
    except Exception as e:
        print(f"\n  ERROR: {type(e).__name__}: {e}")
    finally:
        await client.close()


async def main() -> None:
    token = load_token()

    await discover_docker()
    await discover_cloudflare(token)

    print("=" * 70)
    print("ADOPTION CANDIDATES")
    print("=" * 70)
    print("""
  The tunnel manager can adopt existing setups by:
  1. Importing existing CF tunnels into its database
  2. Tracking existing cloudflared containers
  3. Managing DNS records it creates going forward

  Existing containers will NOT be disrupted during adoption.
""")


if __name__ == "__main__":
    asyncio.run(main())
