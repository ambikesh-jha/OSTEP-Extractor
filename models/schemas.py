"""
models/schemas.py
Pydantic request / response models used across the API.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


# ── Enums ──────────────────────────────────────────────────────────

class JobStatus(str, Enum):
    idle      = "idle"       # no job running / pipeline not yet started
    pending   = "pending"
    running   = "running"
    completed = "completed"
    failed    = "failed"


class PipelinePhase(str, Enum):
    """Tracks which stage of the two-step pipeline is active."""
    idle   = "idle"
    scrape = "scrape"
    merge  = "merge"


class AssetCategory(str, Enum):
    code     = "code"
    images   = "images"
    pdfs     = "pdfs"
    homework = "homework"


# ── Chapter ────────────────────────────────────────────────────────

class ChapterInfo(BaseModel):
    id: str        = Field(..., description="Unique slug ID for the chapter")
    title: str     = Field(..., description="Display title of the chapter")
    url: str       = Field(..., description="Source URL of the chapter PDF")
    downloaded: bool = Field(False, description="Whether the PDF has been downloaded")

    model_config = {"from_attributes": True}


class ChapterListResponse(BaseModel):
    total: int
    chapters: list[ChapterInfo]


# ── Shared pipeline job ────────────────────────────────────────────

class PipelineStartResponse(BaseModel):
    """Returned by both /scrape/start and /merge/start."""
    job_id: str
    phase:  PipelinePhase
    status: JobStatus
    message: str


class PipelineStatusResponse(BaseModel):
    """
    Unified status snapshot for the single pipeline job.
    Sent over REST and pushed over WebSocket.
    """
    job_id: str
    phase:  PipelinePhase
    status: JobStatus

    # scrape phase fields
    chapters_found:    int = 0
    chapters_fetched:  int = 0
    assets_downloaded: int = 0
    scrape_output:     Optional[str] = None

    # merge phase fields
    chapters_merged: int   = 0
    total_pages:     int   = 0
    file_size_mb:    float = 0.0
    merge_output:    Optional[str] = None
    missing_files:   list[str]     = Field(default_factory=list)

    # shared
    error:      Optional[str] = None
    started_at: Optional[str] = None
    updated_at: Optional[str] = None


# ── Request bodies ─────────────────────────────────────────────────

class ScrapeRequest(BaseModel):
    index_url: Optional[str] = Field(
        None,
        description="Override the default OSTEP index URL",
        examples=["https://pages.cs.wisc.edu/~remzi/OSTEP/"],
    )


class MergeRequest(BaseModel):
    include_extras: bool = Field(
        True,
        description="Append any downloaded PDFs not in the canonical order list",
    )


# ── Asset ──────────────────────────────────────────────────────────

class AssetInfo(BaseModel):
    filename:      str
    category:      AssetCategory
    size_bytes:    int
    relative_path: str


class AssetListResponse(BaseModel):
    total:  int
    assets: list[AssetInfo]


# ── Health ─────────────────────────────────────────────────────────

class HealthResponse(BaseModel):
    status:            str  = "ok"
    version:           str
    output_dir_exists: bool
    pdf_count:         int  = 0
    html_ready:        bool = False
    merged_pdf_ready:  bool = False
