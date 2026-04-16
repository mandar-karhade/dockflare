"""SQLModel classes — import all to register with metadata."""

from app.models.audit import AuditLog
from app.models.cache import ContainerCache, ZoneCache
from app.models.credential import CfCredential
from app.models.dns import DnsBackup, DnsConflict, DnsOperation
from app.models.drift import DriftFinding
from app.models.rotation import RotationEvent
from app.models.route import Route
from app.models.settings import AppSetting
from app.models.tunnel import Tunnel

__all__ = [
    "AuditLog",
    "AppSetting",
    "CfCredential",
    "ContainerCache",
    "DnsBackup",
    "DnsConflict",
    "DnsOperation",
    "DriftFinding",
    "RotationEvent",
    "Route",
    "Tunnel",
    "ZoneCache",
]
