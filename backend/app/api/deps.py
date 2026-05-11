"""Common FastAPI dependencies — CF client, Docker client, DB session, Vault."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.clients.cloudflare.client import CloudflareClient
from app.clients.cloudflare.fake import FakeCloudflareClient
from app.clients.docker.client import DockerClient
from app.config import Settings
from app.core.vault import VaultService


@lru_cache
def get_settings() -> Settings:
    return Settings()


def load_cf_token() -> str:
    """Load CF token from Docker secret, env var, or .env file (in that order)."""
    secret_path = Path("/run/secrets/cf_token")
    if secret_path.exists():
        return secret_path.read_text().strip()
    env_token = os.environ.get("CF_TOKEN", "").strip()
    if env_token:
        return env_token
    env_path = Path(__file__).resolve().parents[3] / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line.startswith("CF_TOKEN=") and not line.startswith("#"):
                return line.split("=", 1)[1].strip()
    return ""


_cf_client: CloudflareClient | None = None
_docker_client: DockerClient | None = None
_vault: VaultService | None = None


def get_cf_client() -> CloudflareClient:
    global _cf_client
    if _cf_client is None:
        token = load_cf_token()
        if not token:
            raise RuntimeError("CF_TOKEN not configured in .env")
        _cf_client = CloudflareClient(token=token)
    return _cf_client


def get_docker_client() -> DockerClient:
    global _docker_client
    if _docker_client is None:
        _docker_client = DockerClient(base_url="unix:///var/run/docker.sock")
    return _docker_client


def get_vault() -> VaultService:
    global _vault
    if _vault is None:
        settings = get_settings()
        _vault = VaultService(key=settings.get_master_key())
    return _vault
