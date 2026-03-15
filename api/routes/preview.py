"""
api/routes/preview.py
─────────────────────────────────────────────────────────────────────
Separate PREVIEW and DOWNLOAD endpoints for all asset files.

PREVIEW  — Content-Disposition: inline  (browser renders in-place)
  GET /api/v1/preview/asset/{category}/{filename}
  GET /api/v1/preview/merged

DOWNLOAD — Content-Disposition: attachment  (triggers Save-As dialog)
  GET /api/v1/download/asset/{category}/{filename}
  GET /api/v1/download/merged

Both routers are merged into a single `router` that main.py mounts
under the /api/v1 prefix, producing:
  /api/v1/preview/...
  /api/v1/download/...
"""

from __future__ import annotations

import logging
import mimetypes
import urllib.parse
from pathlib import Path

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import FileResponse

from core.config import settings
from models.schemas import AssetCategory

log = logging.getLogger(__name__)

# ── MIME map ───────────────────────────────────────────────────────
# Code files use text/plain so browsers render them inline rather than
# triggering a download when served from the preview endpoint.
_MIME: dict[str, str] = {
    ".pdf":  "application/pdf",
    ".png":  "image/png",
    ".jpg":  "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif":  "image/gif",
    ".webp": "image/webp",
    ".svg":  "image/svg+xml",
    ".c":    "text/plain; charset=utf-8",
    ".h":    "text/plain; charset=utf-8",
    ".cpp":  "text/plain; charset=utf-8",
    ".cc":   "text/plain; charset=utf-8",
    ".py":   "text/plain; charset=utf-8",
    ".sh":   "text/plain; charset=utf-8",
    ".pl":   "text/plain; charset=utf-8",
    ".s":    "text/plain; charset=utf-8",
    ".asm":  "text/plain; charset=utf-8",
    ".tgz":  "application/gzip",
    ".gz":   "application/gzip",
    ".zip":  "application/zip",
    ".tar":  "application/x-tar",
}

# CORS headers so browsers can embed responses in <iframe> / <img>
# from any origin (needed when served behind ngrok or a reverse proxy).
_CORS: dict[str, str] = {
    "Access-Control-Allow-Origin":  "*",
    "Access-Control-Allow-Methods": "GET, OPTIONS",
    "Access-Control-Allow-Headers": "*",
    "X-Frame-Options":              "ALLOWALL",
    "X-Content-Type-Options":       "nosniff",
}


# ── Helpers ────────────────────────────────────────────────────────

def _detect_mime(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in _MIME:
        return _MIME[ext]
    guessed, _ = mimetypes.guess_type(str(path))
    return guessed or "application/octet-stream"


def _resolve_asset(category: str, raw_filename: str) -> Path:
    """
    Safely resolve an asset path, guarding against directory traversal.

    - URL-decodes the filename (handles %2B, %20, etc.)
    - Rejects filenames containing path separators or '..'
    - Confirms the resolved path stays inside the category directory
    - Raises 400 on bad input, 404 if the file does not exist
    """
    filename = urllib.parse.unquote(raw_filename)

    if ".." in filename or "/" in filename or "\\" in filename:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid filename — path separators are not allowed.",
        )

    cat_dir = (settings.assets_path / category).resolve()
    path    = (cat_dir / filename).resolve()

    # Jail-break check: ensure resolved path is still inside cat_dir
    try:
        path.relative_to(cat_dir)
    except ValueError:
        log.warning("Path traversal attempt: cat=%s file=%s", category, filename)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Path traversal detected.",
        )

    if not path.exists() or not path.is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"'{filename}' not found in category '{category}'.",
        )

    return path


def _file_response(path: Path, disposition: str) -> FileResponse:
    """Build a FileResponse with the correct MIME type and CORS headers."""
    return FileResponse(
        path=str(path),
        media_type=_detect_mime(path),
        headers={
            **_CORS,
            "Content-Disposition": f'{disposition}; filename="{path.name}"',
        },
    )


# ══════════════════════════════════════════════════════════════════
# PREVIEW router  (/api/v1/preview/...)
# Content-Disposition: inline — browser renders without prompting save
# ══════════════════════════════════════════════════════════════════
_preview = APIRouter(prefix="/preview", tags=["Preview"])


@_preview.get(
    "/asset/{category}/{filename:path}",
    response_class=FileResponse,
    summary="Preview an asset file inline in the browser",
    description=(
        "Returns the file with `Content-Disposition: inline` so PDFs render "
        "inside an `<iframe>` and images / code display directly. "
        "**Never triggers a download.**"
    ),
)
def preview_asset(category: AssetCategory, filename: str) -> FileResponse:
    path = _resolve_asset(category.value, filename)
    log.info("PREVIEW  %s / %s", category.value, path.name)
    return _file_response(path, "inline")


@_preview.get(
    "/merged",
    response_class=FileResponse,
    summary="Preview the merged OSTEP PDF inline",
    description=(
        "Streams `OSTEP_Complete_Book.pdf` with `Content-Disposition: inline` "
        "so browsers render it inside an `<iframe>` without downloading."
    ),
)
def preview_merged() -> FileResponse:
    path = settings.pdf_output
    if not path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Merged PDF not found. Run POST /api/v1/merge/start first.",
        )
    log.info("PREVIEW  merged PDF")
    return _file_response(path, "inline")


# ══════════════════════════════════════════════════════════════════
# DOWNLOAD router  (/api/v1/download/...)
# Content-Disposition: attachment — triggers browser Save-As dialog
# ══════════════════════════════════════════════════════════════════
_download = APIRouter(prefix="/download", tags=["Download"])


@_download.get(
    "/asset/{category}/{filename:path}",
    response_class=FileResponse,
    summary="Download an asset file (triggers Save-As)",
    description=(
        "Returns the file with `Content-Disposition: attachment` so the "
        "browser shows a Save-As dialog. Only call this on an explicit "
        "user click — never for auto-preview."
    ),
)
def download_asset(category: AssetCategory, filename: str) -> FileResponse:
    path = _resolve_asset(category.value, filename)
    log.info("DOWNLOAD %s / %s", category.value, path.name)
    return _file_response(path, "attachment")


@_download.get(
    "/merged",
    response_class=FileResponse,
    summary="Download the merged OSTEP PDF as an attachment",
    description=(
        "Returns `OSTEP_Complete_Book.pdf` with `Content-Disposition: attachment` "
        "so the browser prompts a Save-As dialog."
    ),
)
def download_merged() -> FileResponse:
    path = settings.pdf_output
    if not path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Merged PDF not found. Run POST /api/v1/merge/start first.",
        )
    log.info("DOWNLOAD merged PDF")
    return _file_response(path, "attachment")


# ══════════════════════════════════════════════════════════════════
# Combined router — mounted at /api/v1 in main.py
# Exposes: /api/v1/preview/... and /api/v1/download/...
# ══════════════════════════════════════════════════════════════════
router = APIRouter()
router.include_router(_preview)
router.include_router(_download)
