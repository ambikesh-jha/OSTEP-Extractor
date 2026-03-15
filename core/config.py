"""
core/config.py
Centralised settings loaded from environment / .env file.
"""

from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Application ────────────────────────────────────────────────
    app_name: str = "OSTEP EXTRACTOR"
    app_version: str = "1.0.1"
    debug: bool = False

    # ── Server ─────────────────────────────────────────────────────
    host: str = "0.0.0.0"
    port: int = 8000

    # ── Scraper ────────────────────────────────────────────────────
    ostep_index_url: str = "https://pages.cs.wisc.edu/~remzi/OSTEP/"
    request_delay: float = 1.2
    request_timeout: int = 25
    user_agent: str = (
        "OSTEP-OfflineBuilder/2.0 "
        "(educational archiver; contact@example.com)"
    )

    # ── Output ─────────────────────────────────────────────────────
    output_dir: str = "ostep_offline"
    assets_subdir: str = "ostep_assets"

    # ── Derived paths (not from .env) ──────────────────────────────
    @property
    def output_path(self) -> Path:
        return Path(self.output_dir)

    @property
    def assets_path(self) -> Path:
        return self.output_path / self.assets_subdir

    @property
    def log_file(self) -> Path:
        return self.output_path / "scrape.log"

    @property
    def html_output(self) -> Path:
        return self.output_path / "ostep_complete.html"

    @property
    def pdf_output(self) -> Path:
        return self.output_path / "OSTEP_Complete_Book.pdf"

    @property
    def pdfs_dir(self) -> Path:
        return self.assets_path / "pdfs"


settings = Settings()
