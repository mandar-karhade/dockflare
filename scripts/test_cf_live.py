#!/usr/bin/env python3
"""Live test against real Cloudflare API.

Usage: source .venv/bin/activate && python scripts/test_cf_live.py
Reads CF_TOKEN from .env file in project root.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

# Add backend to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app.clients.cloudflare.client import CloudflareClient


def load_token() -> str:
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if line.startswith("CF_TOKEN="):
                return line.split("=", 1)[1].strip()
    token = os.environ.get("CF_TOKEN", "")
    if not token:
        print("ERROR: Set CF_TOKEN in .env or environment")
        sys.exit(1)
    return token


async def main() -> None:
    token = load_token()
    print(f"Token: ...{token[-4:]}")
    print()

    client = CloudflareClient(token=token)

    try:
        # 1. Verify token
        print("=== Token Verification ===")
        result = await client.verify_token()
        print(f"  Status: {result.get('status', 'unknown')}")
        print()

        # 2. List accounts
        print("=== Accounts ===")
        accounts = await client.list_accounts()
        for acc in accounts:
            print(f"  {acc['id']} — {acc['name']}")
        print()

        if not accounts:
            print("ERROR: No accounts found. Check token scopes.")
            return

        account_id = accounts[0]["id"]
        print(f"Using account: {account_id}")
        print()

        # 3. List zones
        print("=== Zones ===")
        zones = await client.list_zones(account_id)
        for z in zones:
            print(f"  {z['id'][:12]}... — {z['name']} ({z.get('status', '?')})")
        print(f"  Total: {len(zones)} zones")
        print()

        # 4. List existing tunnels
        print("=== Existing Tunnels ===")
        tunnels = await client.list_tunnels(account_id)
        for t in tunnels:
            print(f"  {t['id'][:12]}... — {t['name']}")
        print(f"  Total: {len(tunnels)} tunnels")
        print()

        # 5. Create a test tunnel
        print("=== Create Test Tunnel ===")
        test_tunnel = await client.create_tunnel(account_id, "cftm-live-test")
        tunnel_id = test_tunnel["id"]
        print(f"  Created: {tunnel_id}")

        # 6. Fetch tunnel token
        tunnel_token = await client.get_tunnel_token(account_id, tunnel_id)
        print(f"  Token: ...{tunnel_token[-4:]}")

        # 7. Get tunnel config
        config = await client.get_tunnel_config(account_id, tunnel_id)
        ingress = config.get("config", {}).get("ingress", [])
        print(f"  Ingress rules: {len(ingress)}")

        # 8. Check connections (should be 0 since no cloudflared running)
        conns = await client.get_tunnel_connections(account_id, tunnel_id)
        print(f"  Active connections: {len(conns)}")
        print()

        # 9. Cleanup — delete the test tunnel
        print("=== Cleanup ===")
        await client.delete_tunnel(account_id, tunnel_id)
        print(f"  Deleted tunnel: {tunnel_id}")
        print()

        print("ALL TESTS PASSED")

    except Exception as e:
        print(f"\nERROR: {type(e).__name__}: {e}")
        sys.exit(1)
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
