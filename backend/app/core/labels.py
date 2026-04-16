"""Docker label constants — single source of truth.

All label keys used by Tunnel Manager for managed containers.
"""

PREFIX = "tunnel-manager"

# Applied to cloudflared sidecars
MANAGED = f"{PREFIX}.managed"
TUNNEL_ID = f"{PREFIX}.tunnel.id"
TUNNEL_CF_ID = f"{PREFIX}.tunnel.cf-id"
TUNNEL_NAME = f"{PREFIX}.tunnel.name"
TARGET_PROJECT = f"{PREFIX}.target.project"
TARGET_SERVICE = f"{PREFIX}.target.service"
SIDECAR_ROLE = f"{PREFIX}.role"  # always "cloudflared-sidecar"

# Compose labels (read from target containers)
COMPOSE_PROJECT = "com.docker.compose.project"
COMPOSE_SERVICE = "com.docker.compose.service"
