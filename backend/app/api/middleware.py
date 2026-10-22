"""Request middleware for logging, request IDs, and security headers."""

from __future__ import annotations

import base64
import binascii
import secrets
import uuid

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Attach a unique request ID to every request/response."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        request_id = str(uuid.uuid4())
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response


class BasicAuthMiddleware(BaseHTTPMiddleware):
    """Require HTTP Basic Auth for protected routes."""

    def __init__(
        self,
        app,
        *,
        username: str,
        password: str,
        exempt_paths: set[str] | None = None,
    ) -> None:
        super().__init__(app)
        self.username = username
        self.password = password
        self.exempt_paths = exempt_paths or set()

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if request.method == "OPTIONS" or request.url.path in self.exempt_paths:
            return await call_next(request)

        if not self._authorized(request.headers.get("Authorization", "")):
            return JSONResponse(
                {"detail": "Authentication required"},
                status_code=401,
                headers={"WWW-Authenticate": "Basic"},
            )

        return await call_next(request)

    def _authorized(self, authorization: str) -> bool:
        scheme, _, encoded = authorization.partition(" ")
        if scheme.lower() != "basic" or not encoded:
            return False

        try:
            decoded = base64.b64decode(encoded, validate=True).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError):
            return False

        username, separator, password = decoded.partition(":")
        if not separator:
            return False

        return secrets.compare_digest(
            username.encode("utf-8"), self.username.encode("utf-8")
        ) and secrets.compare_digest(password.encode("utf-8"), self.password.encode("utf-8"))
