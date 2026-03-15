"""
utils/file_helpers.py
Pure utility functions for file classification, naming, and HTML escaping.
"""

import hashlib
import re
from pathlib import Path
from urllib.parse import urlparse


# ── Extension sets ─────────────────────────────────────────────────

CODE_EXT    = {".c", ".h", ".py", ".cpp", ".cc", ".s", ".asm", ".sh", ".pl"}
IMAGE_EXT   = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp"}
PDF_EXT     = {".pdf"}
ARCHIVE_EXT = {".tgz", ".tar.gz", ".zip", ".gz"}


def classify_url(url: str) -> str | None:
    """Return asset category string or None if URL is not a known asset type."""
    ext = Path(urlparse(url).path).suffix.lower()
    if ext in CODE_EXT:    return "code"
    if ext in IMAGE_EXT:   return "images"
    if ext in PDF_EXT:     return "pdfs"
    if ext in ARCHIVE_EXT: return "homework"
    return None


def asset_local_path(url: str, category: str, assets_dir: Path) -> Path:
    """Build a collision-safe local path for a downloaded asset."""
    name   = Path(urlparse(url).path).name or "file"
    prefix = hashlib.md5(url.encode()).hexdigest()[:6]
    return assets_dir / category / f"{prefix}_{name}"


def slugify(text: str) -> str:
    """Convert arbitrary text to a URL-safe slug."""
    text = re.sub(r"[^\w\s-]", "", text.lower())
    return re.sub(r"[\s_-]+", "-", text).strip("-")


def html_escape(text: str) -> str:
    """Minimal HTML escaping for embedding text in attributes/content."""
    return (
        text
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def ensure_dirs(*paths: Path) -> None:
    """Create one or more directories (and parents) if they don't exist."""
    for p in paths:
        p.mkdir(parents=True, exist_ok=True)
