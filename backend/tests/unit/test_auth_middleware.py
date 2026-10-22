"""Test API Basic Auth middleware."""

from __future__ import annotations

import base64

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.middleware import BasicAuthMiddleware
from app.config import Settings
from app.main import create_app


def _app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(
        BasicAuthMiddleware,
        username="admin",
        password="secret",
        exempt_paths={"/api/v1/health"},
    )

    @app.get("/api/v1/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/v1/zones")
    async def zones() -> dict[str, list[str]]:
        return {"zones": []}

    return app


@pytest.mark.asyncio
async def test_basic_auth_rejects_missing_credentials() -> None:
    transport = ASGITransport(app=_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/v1/zones")

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Basic"


@pytest.mark.asyncio
async def test_basic_auth_rejects_wrong_credentials() -> None:
    transport = ASGITransport(app=_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/v1/zones", auth=("admin", "wrong"))

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_basic_auth_accepts_configured_credentials() -> None:
    transport = ASGITransport(app=_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/v1/zones", auth=("admin", "secret"))

    assert response.status_code == 200
    assert response.json() == {"zones": []}


@pytest.mark.asyncio
async def test_basic_auth_exempts_health_check() -> None:
    transport = ASGITransport(app=_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_create_app_protects_api_when_basic_auth_env_is_set(monkeypatch) -> None:
    monkeypatch.setenv("DOCKFLARE_BASIC_AUTH_USER", "admin")
    monkeypatch.setenv("DOCKFLARE_BASIC_AUTH_PASSWORD", "secret")

    transport = ASGITransport(app=create_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/v1/zones")

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_create_app_hides_openapi_when_debug_is_disabled() -> None:
    settings = Settings(
        _env_file=None,
        DOCKFLARE_BASIC_AUTH_USER="",
        DOCKFLARE_BASIC_AUTH_PASSWORD="",
    )
    transport = ASGITransport(app=create_app(settings))
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/openapi.json")

    assert response.status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize("credentials", ["é:secret", "admin:sécret", "missing-colon"])
async def test_basic_auth_rejects_invalid_credentials_without_server_error(credentials) -> None:
    token = base64.b64encode(credentials.encode("utf-8")).decode("ascii")
    transport = ASGITransport(app=_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/v1/zones", headers={"Authorization": f"Basic {token}"})
    assert response.status_code == 401


def test_basic_auth_accepts_unicode_credentials() -> None:
    middleware = BasicAuthMiddleware(None, username="é", password="sécret")
    token = base64.b64encode("é:sécret".encode()).decode("ascii")
    assert middleware._authorized(f"Basic {token}")
