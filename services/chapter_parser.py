"""
services/chapter_parser.py
Parses the OSTEP index page and discovers chapter PDFs in reading order.
"""

import logging
import re
from urllib.parse import urljoin, urldefrag, urlparse
from pathlib import Path

from bs4 import BeautifulSoup

log = logging.getLogger(__name__)

# Keywords whose presence in a link's text marks it as a non-chapter navigation link
_NON_CHAPTER_KEYWORDS = frozenset({
    "hardcover", "softcover", "buy pdf", "kindle", "donate",
    "book news", "news", "errata", "other books", "acknowledgements",
    "projects", "homework", "merch", "teachers", "for teachers",
    "combined", "toc", "bibliography",
})


def _is_chapter_link(anchor, full_url: str) -> bool:
    """
    Return True only when the anchor href resolves to a chapter PDF
    hosted on the OSTEP site (not a purchase / external link).
    """
    path = urlparse(full_url).path.lower()
    if not path.endswith(".pdf"):
        return False

    ostep_url_lower = full_url.lower()
    if "/ostep/" not in ostep_url_lower:
        return False

    if any(domain in full_url for domain in ("amazon", "lulu")):
        return False

    link_text = anchor.get_text(strip=True).lower()
    return not any(kw in link_text for kw in _NON_CHAPTER_KEYWORDS)


def _find_chapter_table(soup: BeautifulSoup):
    """
    Heuristic: return the <table> with the most PDF hrefs.
    Falls back to the whole soup when no table is found.
    """
    best_table, best_count = None, 0
    for table in soup.find_all("table"):
        count = len(table.find_all("a", href=re.compile(r"\.pdf$", re.I)))
        if count > best_count:
            best_table, best_count = table, count

    log.info("Chapter table PDF link count: %d", best_count)
    return best_table


def parse_chapters(soup: BeautifulSoup, index_url: str) -> list[dict]:
    """
    Walk the chapter table row-by-row, left-to-right to preserve
    the book's canonical reading order.

    Returns:
        list of {"title": str, "url": str}
    """
    chapters: list[dict] = []
    seen: set[str] = set()

    table = _find_chapter_table(soup)
    rows  = table.find_all("tr") if table else [soup]

    for row in rows:
        cells = row.find_all(["td", "th"]) if table else [row]
        for cell in cells:
            for anchor in cell.find_all("a", href=True):
                raw      = anchor["href"].strip()
                full_url = urljoin(index_url, raw)
                clean, _ = urldefrag(full_url)

                if clean in seen:
                    continue
                if not _is_chapter_link(anchor, clean):
                    continue

                title = (
                    anchor.get_text(strip=True)
                    or Path(urlparse(clean).path).stem
                )
                chapters.append({"title": title, "url": clean})
                seen.add(clean)
                log.info("  Discovered chapter: %-35s  %s", title, clean)

    log.info("Total chapters discovered: %d", len(chapters))
    return chapters
