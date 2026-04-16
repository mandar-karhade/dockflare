"""Database engine setup with WAL mode and foreign key enforcement."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


def _set_sqlite_pragmas(dbapi_conn: Any, _connection_record: Any) -> None:
    """Enable WAL mode and foreign keys on every new connection."""
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA journal_mode = WAL")
    cursor.execute("PRAGMA synchronous = NORMAL")
    cursor.execute("PRAGMA busy_timeout = 5000")
    cursor.execute("PRAGMA foreign_keys = ON")
    cursor.close()


def create_engine(db_url: str) -> tuple[Any, async_sessionmaker[AsyncSession]]:
    """Create async engine and session factory."""
    engine = create_async_engine(db_url, echo=False)

    # Register pragma listener on the sync engine
    event.listen(engine.sync_engine, "connect", _set_sqlite_pragmas)

    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    return engine, session_factory


async def get_session(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncGenerator[AsyncSession, None]:
    """Dependency that yields a DB session and closes it after use."""
    async with session_factory() as session:
        yield session
