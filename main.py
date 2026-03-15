"""
main.py
FastAPI application — entry point and app factory.

Run directly:
    python main.py
    — or —
    uvicorn main:app --host 0.0.0.0 --port 8000

The app automatically:
  • Serves ostep_dashboard.html at GET /
  • Serves ostep_offline/ static files at /files/
  • Exposes all API routes under /api/v1/
  • Exposes Swagger UI at /docs
"""

from __future__ import annotations

import subprocess
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from api import (
    chapters_router,
    health_router,
    merge_router,
    preview_router,
    scrape_router,
    ws_router,
)
from core.config import settings
from core.logging import setup_logging

# ── Paths ──────────────────────────────────────────────────────────
_BASE      = Path(__file__).parent
_DASHBOARD = _BASE / "index.html"


# ── Lifespan ───────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: configure logging and bootstrap output directories."""
    setup_logging(log_file=settings.log_file, debug=settings.debug)

    # Create all asset subdirectories up front
    for sub in ("code", "images", "pdfs", "homework"):
        (settings.assets_path / sub).mkdir(parents=True, exist_ok=True)

    import logging
    log = logging.getLogger(__name__)
    log.info("=" * 60)
    log.info("  %s  v%s", settings.app_name, settings.app_version)
    log.info("  Dashboard  →  http://0.0.0.0:%s/", settings.port)
    log.info("  API docs   →  http://0.0.0.0:%s/docs", settings.port)
    log.info("  Output dir →  %s", settings.output_path.resolve())
    log.info("=" * 60)

    yield

    log.info("Shutting down.")


# ── App factory ────────────────────────────────────────────────────
def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description=(
            "REST API for scraping, compiling and merging "
            "Operating Systems: Three Easy Pieces into an offline PDF."
        ),
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=lifespan,
    )

    # Allow all origins — needed for ngrok tunnel and local file:// access
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["Content-Disposition", "Content-Length"],
    )

    # Global error handler — always return JSON, never HTML error pages
    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception):
        import logging
        logging.getLogger(__name__).exception("Unhandled: %s", exc)
        return JSONResponse(
            status_code=500,
            content={"detail": "Internal server error."},
        )

    # ── API routers ────────────────────────────────────────────────
    app.include_router(health_router)
    app.include_router(scrape_router,   prefix="/api/v1")
    app.include_router(merge_router,    prefix="/api/v1")
    app.include_router(chapters_router, prefix="/api/v1")
    app.include_router(ws_router,       prefix="/api/v1")
    app.include_router(preview_router,  prefix="/api/v1")

    # ── Serve dashboard at root ────────────────────────────────────
    @app.get("/", include_in_schema=False)
    async def serve_dashboard():
        if _DASHBOARD.exists():
            return FileResponse(
                str(_DASHBOARD),
                media_type="text/html",
                headers={"Cache-Control": "no-cache"},
            )
        return JSONResponse(
            {"detail": "Dashboard not found. Place index.html next to main.py."},
            status_code=404,
        )

    # ── Serve ostep_offline/ as static files at /files/ ───────────
    # This lets the browser load compiled HTML output directly.
    # output_dir = settings.output_path
    # output_dir.mkdir(parents=True, exist_ok=True)
    # app.mount(
    #     "/files",
    #     StaticFiles(directory=str(output_dir), html=False),
    #     name="output_files",
    # )
    # return app

    # ── Serve ostep_offline/ as static files at /files/ ───────────
    output_dir = settings.output_path
    output_dir.mkdir(parents=True, exist_ok=True)
    app.mount(
        "/files",
        StaticFiles(directory=str(output_dir), html=False),
        name="output_files",
    )

    # ── Serve frontend assets (css/js) ────────────────────────────
    static_dir = _BASE / "static"
    if static_dir.exists():
        app.mount(
            "/static",
            StaticFiles(directory=str(static_dir)),
            name="static",
        )

    return app


app = create_app()


# ── Entry point — runs uvicorn when executed directly ──────────────
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
        log_level="debug" if settings.debug else "info",
    )
