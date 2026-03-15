"""
api/routes/scrape.py
─────────────────────────────────────────────────────────────────────
POST /api/v1/scrape/start
  • Allowed only when no pipeline job is currently pending/running.
  • Resets the shared pipeline job, sets phase=scrape, spawns worker.
  • Returns the fixed PIPELINE_JOB_ID so clients know what to subscribe to.

GET /api/v1/scrape/status   (REST snapshot)
"""

from __future__ import annotations

import logging
import threading
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from api.dependencies import get_job_store
from core.config import settings
from models.schemas import (
    JobStatus,
    MergeRequest,
    PipelinePhase,
    PipelineStartResponse,
    PipelineStatusResponse,
    ScrapeRequest,
)
from services.job_store import JobStore, PIPELINE_JOB_ID
from services.scraper import OSTEPScraper

log = logging.getLogger(__name__)
router = APIRouter(prefix="/scrape", tags=["Scrape"])


# ── Background worker ──────────────────────────────────────────────

def _run_scrape(index_url: str | None, store: JobStore) -> None:
    store.update(status=JobStatus.running, phase=PipelinePhase.scrape)
    try:
        scraper = OSTEPScraper(index_url=index_url, store=store)
        scraper.run()
        store.update(
            status=JobStatus.completed,
            phase=PipelinePhase.scrape,
            chapters_found=scraper.stats["chapters_found"],
            chapters_fetched=scraper.stats["chapters_fetched"],
            assets_downloaded=scraper.stats["assets_downloaded"],
            scrape_output=str(settings.html_output),
        )
        log.info("Scrape phase completed.")
    except Exception as exc:
        log.exception("Scrape phase failed: %s", exc)
        store.update(status=JobStatus.failed, error=str(exc))


# ── Routes ─────────────────────────────────────────────────────────

@router.post(
    "/start",
    response_model=PipelineStartResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Start the scrape phase",
    description=(
        "Allowed **only once** while the pipeline is idle (not pending/running). "
        "Returns 409 Conflict if a job is already active. "
        "The returned `job_id` is the shared pipeline ID used for WebSocket status."
    ),
)
def start_scrape(
    payload: ScrapeRequest,
    store: Annotated[JobStore, Depends(get_job_store)],
) -> PipelineStartResponse:
    if store.is_active():
        current = store.get()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Pipeline is already active "
                f"(phase={current['phase']}, status={current['status']}). "
                f"Connect to WS /api/v1/ws/status to monitor progress."
            ),
        )

    store.reset()
    threading.Thread(
        target=_run_scrape,
        args=(payload.index_url, store),
        daemon=True,
    ).start()

    return PipelineStartResponse(
        job_id=PIPELINE_JOB_ID,
        phase=PipelinePhase.scrape,
        status=JobStatus.pending,
        message=(
            f"Scrape started. "
            f"Connect to ws://<host>/api/v1/ws/status for live updates, "
            f"or poll GET /api/v1/scrape/status."
        ),
    )


@router.get(
    "/status",
    response_model=PipelineStatusResponse,
    summary="Current pipeline status snapshot (REST)",
)
def scrape_status(
    store: Annotated[JobStore, Depends(get_job_store)],
) -> PipelineStatusResponse:
    state = store.get()
    return PipelineStatusResponse(**state)

