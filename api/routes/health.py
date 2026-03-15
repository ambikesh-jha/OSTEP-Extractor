"""
api/routes/health.py
Health-check endpoint — reports application readiness.
"""

from fastapi import APIRouter

from core.config import settings
from models.schemas import HealthResponse

router = APIRouter(tags=["Health"])


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Application health check",
)
def health_check() -> HealthResponse:
    """
    Returns the current health status of the API, including whether
    the output directory exists and how many PDFs have been downloaded.
    """
    pdf_dir  = settings.pdfs_dir
    pdf_count = len(list(pdf_dir.glob("*.pdf"))) if pdf_dir.exists() else 0

    return HealthResponse(
        version=settings.app_version,
        output_dir_exists=settings.output_path.exists(),
        pdf_count=pdf_count,
        html_ready=settings.html_output.exists(),
        merged_pdf_ready=settings.pdf_output.exists(),
    )
