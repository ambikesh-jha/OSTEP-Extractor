"""
api/routes/chapters.py
Endpoints for inspecting discovered chapters and downloaded assets.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, status
from bs4 import BeautifulSoup

from core.config import settings
from models.schemas import (
    AssetCategory,
    AssetInfo,
    AssetListResponse,
    ChapterInfo,
    ChapterListResponse,
)
from services.chapter_parser import parse_chapters
from utils.http import RateLimitedSession

log = logging.getLogger(__name__)
router = APIRouter(prefix="/chapters", tags=["Chapters"])


@router.get(
    "/",
    response_model=ChapterListResponse,
    summary="List chapters from the OSTEP index",
    description=(
        "Fetches the live OSTEP index page and returns all discovered "
        "chapter links in reading order. Does NOT download anything."
    ),
)
def list_chapters() -> ChapterListResponse:
    session = RateLimitedSession()
    try:
        resp = session.get(settings.ostep_index_url)
        if not resp:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Could not reach the OSTEP index page.",
            )
        soup     = BeautifulSoup(resp.text, "html.parser")
        raw_list = parse_chapters(soup, settings.ostep_index_url)
    finally:
        session.close()

    chapters = [
        ChapterInfo(
            id=f"{i:03d}",
            title=ch["title"],
            url=ch["url"],
            downloaded=_is_downloaded(ch["url"]),
        )
        for i, ch in enumerate(raw_list)
    ]
    return ChapterListResponse(total=len(chapters), chapters=chapters)


@router.get(
    "/assets",
    response_model=AssetListResponse,
    summary="List all downloaded assets",
    description="Returns metadata for every file in the local assets directory.",
)
def list_assets(category: AssetCategory | None = None) -> AssetListResponse:
    assets_path = settings.assets_path
    if not assets_path.exists():
        return AssetListResponse(total=0, assets=[])

    results: list[AssetInfo] = []
    categories = [category.value] if category else ["code", "images", "pdfs", "homework"]

    for cat in categories:
        cat_dir = assets_path / cat
        if not cat_dir.exists():
            continue
        for f in sorted(cat_dir.iterdir()):
            if f.is_file():
                results.append(
                    AssetInfo(
                        filename=f.name,
                        category=AssetCategory(cat),
                        size_bytes=f.stat().st_size,
                        relative_path=str(f.relative_to(settings.output_path)),
                    )
                )

    return AssetListResponse(total=len(results), assets=results)


# ── Helper ─────────────────────────────────────────────────────────

def _is_downloaded(url: str) -> bool:
    """Check if a PDF URL has already been downloaded to the local asset store."""
    from pathlib import Path
    from urllib.parse import urlparse
    filename = Path(urlparse(url).path).name
    pdf_dir  = settings.pdfs_dir
    if not pdf_dir.exists():
        return False
    # Match by bare filename ignoring the hash prefix
    return any(
        f.name.endswith(f"_{filename}") or f.name == filename
        for f in pdf_dir.glob("*.pdf")
    )
