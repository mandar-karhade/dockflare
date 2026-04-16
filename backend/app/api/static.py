"""SPA fallthrough for serving the React frontend."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, HTMLResponse
from starlette.responses import Response

router = APIRouter()

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


@router.get("/{full_path:path}", response_model=None)
async def serve_spa(request: Request, full_path: str) -> Response:
    """Serve static files, falling back to index.html for SPA routing."""
    # Don't intercept API or WebSocket routes
    if full_path.startswith("api/") or full_path.startswith("ws"):
        return HTMLResponse(status_code=404, content='{"detail":"Not found"}')

    file_path = STATIC_DIR / full_path
    if file_path.is_file():
        return FileResponse(file_path)
    index = STATIC_DIR / "index.html"
    if index.is_file():
        return FileResponse(index)
    return HTMLResponse("<h1>Tunnel Manager</h1><p>Frontend not built yet.</p>")
