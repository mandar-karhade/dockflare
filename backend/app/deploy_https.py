"""Provision the persistent HTTPS endpoint used by deploy.sh."""

from __future__ import annotations

import asyncio
import fcntl
import ipaddress
import json
import os
import re
import secrets
import string
import sys
from contextlib import redirect_stdout
from pathlib import Path

from app.api.deps import load_cf_token
from app.clients.cloudflare.client import CloudflareClient
from app.config import Settings

DOMAIN = "abbrv8.com"


def new_hostname(ip: str) -> str:
    octets = str(ipaddress.IPv4Address(ip)).split(".")
    suffix = "".join(secrets.choice(string.ascii_lowercase) for _ in range(4))
    return f"dockflare-{octets[0]}-{suffix}-{octets[-1]}.{DOMAIN}"


def save_private(path: Path, content: str) -> None:
    temporary = path.with_name(path.name + ".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as file:
        file.write(content)
    temporary.chmod(0o600)
    temporary.replace(path)


async def provision(ip: str, directory: Path, client: CloudflareClient) -> str:
    ip = str(ipaddress.IPv4Address(ip))
    zones = (await client.get("/zones", params={"name": DOMAIN}))["result"]
    if len(zones) != 1:
        raise RuntimeError(f"Exactly one accessible {DOMAIN} zone is required")
    zone = zones[0]
    account_id, zone_id = zone["account"]["id"], zone["id"]
    state_path = directory / "deployment-https.json"
    state = json.loads(state_path.read_text()) if state_path.exists() else None
    if state and state["ip"] == ip:
        hostname, tunnel_id = state["hostname"], state["tunnel_id"]
        first, *_, last = ip.split(".")
        if not re.fullmatch(rf"dockflare-{first}-[a-z]{{4}}-{last}\.abbrv8\.com", hostname):
            raise RuntimeError("Saved hostname does not match this server")
        tunnel = await client.get_tunnel(account_id, tunnel_id)
        if tunnel["name"] != hostname.split(".")[0]:
            raise RuntimeError("Saved tunnel does not belong to this deployment")
    else:
        # Old IPs, DNS records and tunnels are deliberately left for manual cleanup.
        tunnels = await client.list_tunnels(account_id)
        names = {t["name"] for t in tunnels}
        for _ in range(20):
            hostname = new_hostname(ip)
            if hostname.split(".")[0] not in names and not await client.list_dns_records(
                zone_id, name=hostname
            ):
                break
        else:
            raise RuntimeError("Could not find an unused deployment hostname")
        tunnel = await client.create_tunnel(account_id, hostname.split(".")[0])
        tunnel_id = tunnel["id"]
        # Save before configuring DNS so an interrupted provisioning run can resume.
        save_private(
            state_path, json.dumps({"ip": ip, "hostname": hostname, "tunnel_id": tunnel_id}) + "\n"
        )

    target = f"{tunnel_id}.cfargotunnel.com"
    records = await client.list_dns_records(zone_id, name=hostname)
    if any(r["type"] != "CNAME" or r["content"] != target for r in records):
        raise RuntimeError(f"Refusing to overwrite another service at {hostname}")
    await client.update_tunnel_config(
        account_id,
        tunnel_id,
        {
            "ingress": [
                {"hostname": hostname, "service": "http://frontend:80"},
                {"service": "http_status:404"},
            ]
        },
    )
    if not records:
        await client.create_dns_record(
            zone_id,
            record_type="CNAME",
            name=hostname,
            content=target,
            proxied=True,
            comment="Dockflare deployment HTTPS",
        )
    else:
        for record in records:
            if not record["proxied"]:
                await client.update_dns_record(zone_id, record["id"], proxied=True)
    token = await client.get_tunnel_token(account_id, tunnel_id)
    if not token or "\n" in token or "\r" in token:
        raise RuntimeError("Invalid tunnel token")
    save_private(directory / ".https-tunnel.env", f"TUNNEL_TOKEN={token}\n")
    return hostname


async def main(ip: str, directory: Path) -> str:
    token = load_cf_token()
    if not token:
        raise RuntimeError("CF_TOKEN is required to provision HTTPS")
    client = CloudflareClient(token)
    try:
        return await provision(ip, directory, client)
    finally:
        await client.close()


if __name__ == "__main__":
    directory = Path(Settings().db_path).parent
    with (directory / "deployment-https.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        # Retry logs must not contaminate the hostname consumed by deploy.sh.
        with redirect_stdout(sys.stderr):
            hostname = asyncio.run(main(sys.argv[1], directory))
        print(hostname)
