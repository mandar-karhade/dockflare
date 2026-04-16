"""Global test fixtures."""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlmodel import SQLModel

# Import all models so metadata is populated
import app.models  # noqa: F401
from app.core.vault import VaultService


def _enable_fk(dbapi_conn, _connection_record):
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA foreign_keys = ON")
    cursor.close()


@pytest_asyncio.fixture
async def db_engine(tmp_path):
    """Create a fresh SQLite engine per test with FK enforcement."""
    db_path = tmp_path / "test.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    event.listen(engine.sync_engine, "connect", _enable_fk)
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(db_engine):
    """Yield an async session for a test."""
    async with AsyncSession(db_engine) as session:
        yield session


@pytest.fixture
def vault():
    """VaultService with a deterministic test key."""
    return VaultService(key=b"\x00" * 32)
