"""Application configuration via Pydantic Settings."""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Dockflare configuration.

    Values loaded from environment variables or Docker secrets.
    """

    # Application
    app_name: str = "Dockflare"
    debug: bool = False
    host: str = "0.0.0.0"  # noqa: S104
    port: int = 8088
    log_level: str = "info"

    # Database
    db_path: str = "tm.db"

    @property
    def db_url(self) -> str:
        return f"sqlite+aiosqlite:///{self.db_path}"

    # Secrets
    master_key_path: str = "/run/secrets/master_key"
    master_key_hex: str = ""  # fallback for dev (hex-encoded 32 bytes)

    # Docker
    docker_host: str = "tcp://socket-proxy:2375"

    # Cloudflare
    cloudflared_image: str = "cloudflare/cloudflared:2024.10.0"

    # Rotation defaults
    rotation_jitter_hours: int = 2
    rotation_stagger_minutes: int = 5

    # CORS (dev only)
    cors_origins: list[str] = ["http://localhost:5173"]

    model_config = {"env_prefix": "TM_", "env_file": ".env", "extra": "ignore"}

    def get_master_key(self) -> bytes:
        """Resolve master key from file or hex env var."""
        key_path = Path(self.master_key_path)
        if key_path.exists():
            return key_path.read_bytes().strip()
        if self.master_key_hex:
            return bytes.fromhex(self.master_key_hex)
        # Dev fallback: deterministic key (NOT for production)
        return b"\x00" * 32
