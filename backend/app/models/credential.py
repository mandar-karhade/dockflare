"""CF API credential model."""

from __future__ import annotations

from datetime import datetime

from sqlmodel import Field

from app.models.base import TimestampMixin


class CfCredential(TimestampMixin, table=True):
    """Cloudflare API token stored encrypted."""

    __tablename__ = "cf_credentials"

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(nullable=False)
    account_id: str = Field(nullable=False)
    account_name: str | None = Field(default=None)
    token_encrypted: bytes = Field(nullable=False)
    token_last_four: str = Field(nullable=False)
    token_fingerprint: str = Field(unique=True, nullable=False)
    scopes_json: str | None = Field(default=None)
    expires_at: datetime | None = Field(default=None)
    is_active: bool = Field(default=True, nullable=False, index=True)
    last_verified_at: datetime | None = Field(default=None)
    last_verification_status: str | None = Field(default=None)
