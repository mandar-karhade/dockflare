"""Exception hierarchy for Tunnel Manager.

Maps to HTTP responses via FastAPI exception handlers.
"""

from __future__ import annotations


class TMError(Exception):
    """Base error for all Tunnel Manager exceptions."""

    def __init__(self, message: str = "", *, detail: str | None = None) -> None:
        self.detail = detail or message
        super().__init__(message)


class NotFoundError(TMError):
    """Entity not found."""

    def __init__(self, entity_type: str, entity_id: int | str) -> None:
        self.entity_type = entity_type
        self.entity_id = entity_id
        super().__init__(f"{entity_type} {entity_id} not found")


class ConflictError(TMError):
    """Generic conflict."""


class DNSConflictError(ConflictError):
    """DNS record conflict requiring user resolution."""

    def __init__(
        self,
        conflict_type: str,
        existing_record: dict[str, object],
        *,
        resolution_options: list[str] | None = None,
    ) -> None:
        self.conflict_type = conflict_type
        self.existing_record = existing_record
        self.resolution_options = resolution_options or [
            "replace_with_backup",
            "skip",
            "adopt",
        ]
        super().__init__(
            f"DNS conflict ({conflict_type}) for existing record",
            detail=conflict_type,
        )


class RotationError(TMError):
    """Token rotation failure."""


class CFAPIError(TMError):
    """Cloudflare API returned an error."""

    def __init__(self, cf_code: int, message: str) -> None:
        self.cf_code = cf_code
        super().__init__(message)


class DockerError(TMError):
    """Docker operation failure."""


class VaultError(TMError):
    """Encryption/decryption failure."""
