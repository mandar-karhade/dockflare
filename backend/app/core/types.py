"""Shared type aliases for Tunnel Manager."""

from __future__ import annotations

from typing import Literal

TunnelStatus = Literal["active", "disabled", "error", "provisioning"]
RouteStatus = Literal["provisioning", "active", "disabled", "orphaned", "error", "target_down"]
RotationPolicy = Literal["manual", "7d", "30d", "90d"]
RotationTrigger = Literal["scheduled", "manual", "alert", "force_recreate"]
RotationStatus = Literal["in_progress", "success", "noop", "failed", "rolled_back"]
DriftResolution = Literal["reconciled_to_db", "accepted_external", "ignored", "pending"]
ConflictResolutionChoice = Literal["replace_with_backup", "skip", "adopt", "accepted_external"]
CredentialVerificationStatus = Literal["valid", "invalid", "expired"]
Actor = str  # "user:{id}" | "system" | "scheduler"
