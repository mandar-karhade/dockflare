"""Audit context manager for recording state-changing operations."""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit import AuditLog


class AuditContext:
    """Mutable context passed into the audit block."""

    def __init__(self, entity_id: int | None = None) -> None:
        self.entity_id = entity_id
        self.after: dict[str, Any] | None = None

    def set_entity_id(self, eid: int) -> None:
        self.entity_id = eid

    def set_after(self, after: dict[str, Any]) -> None:
        self.after = after


@asynccontextmanager
async def audit_context(
    db: AsyncSession,
    actor: str,
    action: str,
    entity_type: str,
    entity_id: int | None = None,
    before: dict[str, Any] | None = None,
    request_id: str | None = None,
) -> AsyncGenerator[AuditContext, None]:
    """Context manager that records success/failure to audit_log.

    Usage:
        async with audit_context(db, actor="user:1", action="route.create",
                                 entity_type="route") as ctx:
            route = await do_create(...)
            ctx.set_entity_id(route.id)
            ctx.set_after(route.model_dump())
    """
    ctx = AuditContext(entity_id=entity_id)
    try:
        yield ctx
        await db.execute(
            insert(AuditLog).values(
                actor=actor,
                action=action,
                entity_type=entity_type,
                entity_id=ctx.entity_id,
                before_json=json.dumps(before, default=str) if before else None,
                after_json=json.dumps(ctx.after, default=str) if ctx.after else None,
                status="success",
                request_id=request_id,
            )
        )
        await db.flush()
    except Exception as e:
        await db.execute(
            insert(AuditLog).values(
                actor=actor,
                action=action,
                entity_type=entity_type,
                entity_id=ctx.entity_id,
                status="failed",
                error_message=str(e),
                request_id=request_id,
            )
        )
        await db.flush()
        raise
