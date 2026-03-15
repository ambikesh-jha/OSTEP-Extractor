"""
api/routes/merge.py
─────────────────────────────────────────────────────────────────────
POST /api/v1/merge/start
  • Reuses the SAME pipeline job_id as the scrape phase.
  • Blocked if scrape has not completed successfully yet.
  • Blocked if another job is currently pending/running.

GET /api/v1/merge/download
  • Streams the merged PDF file as a file download.
  • Returns 404 if the file has not been generated yet.
"""

from __future__ import annotations

import logging
import threading
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse

from api.dependencies import get_job_store
from core.config import settings
from models.schemas import (
    JobStatus,
    MergeRequest,
    PipelinePhase,
    PipelineStartResponse,
)
from services.job_store import JobStore, PIPELINE_JOB_ID
from services.merger import PDFMerger

log = logging.getLogger(__name__)
router = APIRouter(prefix="/merge", tags=["Merge"])


# ── Background worker ──────────────────────────────────────────────

def _run_merge(include_extras: bool, store: JobStore) -> None:
    store.update(status=JobStatus.running, phase=PipelinePhase.merge)
    try:
        result = PDFMerger(include_extras=include_extras, store=store).run()
        store.update(
            status=JobStatus.completed,
            phase=PipelinePhase.merge,
            chapters_merged=result["chapters_merged"],
            total_pages=result["total_pages"],
            file_size_mb=result["file_size_mb"],
            merge_output=result["output_file"],
            missing_files=result["missing_files"],
        )
        log.info("Merge phase completed.")
    except Exception as exc:
        log.exception("Merge phase failed: %s", exc)
        store.update(status=JobStatus.failed, error=str(exc))


# ── Routes ─────────────────────────────────────────────────────────

@router.post(
    "/start",
    response_model=PipelineStartResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Start the merge phase",
    description=(
        "Reuses the pipeline `job_id` from the scrape phase. "
        "Requires scrape to have **completed** before merge can begin. "
        "Returns 409 if the pipeline is still active, or 412 if scrape "
        "has not run / did not complete successfully."
    ),
)
def start_merge(
    payload: MergeRequest,
    store: Annotated[JobStore, Depends(get_job_store)],
) -> PipelineStartResponse:
    state = store.get()

    if store.is_active():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Pipeline is already active "
                f"(phase={state['phase']}, status={state['status']}). "
                "Wait for it to finish before starting merge."
            ),
        )

    if state["phase"] != PipelinePhase.scrape or state["status"] != JobStatus.completed:
        raise HTTPException(
            status_code=status.HTTP_412_PRECONDITION_FAILED,
            detail=(
                "Scrape phase must complete successfully before merge can start. "
                f"Current state: phase={state['phase']}, status={state['status']}."
            ),
        )

    store.update(status=JobStatus.pending, phase=PipelinePhase.merge, error=None)

    threading.Thread(
        target=_run_merge,
        args=(payload.include_extras, store),
        daemon=True,
    ).start()

    return PipelineStartResponse(
        job_id=PIPELINE_JOB_ID,
        phase=PipelinePhase.merge,
        status=JobStatus.pending,
        message=(
            "Merge started. Connect to ws://<host>/api/v1/ws/status "
            "for live updates. Download the result from GET /api/v1/merge/download."
        ),
    )


@router.get(
    "/download",
    summary="Download the merged PDF",
    description=(
        "Streams `OSTEP_Complete_Book.pdf` as a file download. "
        "Returns **404** if the merge has not completed yet."
    ),
    response_class=FileResponse,
)
def download_merged_pdf() -> FileResponse:
    pdf_path = settings.pdf_output

    if not pdf_path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                "Merged PDF not found. "
                "Run POST /api/v1/merge/start and wait for it to complete."
            ),
        )

    return FileResponse(
        path=str(pdf_path),
        media_type="application/pdf",
        filename=pdf_path.name,
    )
