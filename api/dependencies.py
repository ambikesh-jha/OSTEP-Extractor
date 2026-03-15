"""
api/dependencies.py
FastAPI dependency providers (injected via Depends).
"""

from services.job_store import JobStore, job_store


def get_job_store() -> JobStore:
    """Yield the singleton JobStore for dependency injection."""
    return job_store
