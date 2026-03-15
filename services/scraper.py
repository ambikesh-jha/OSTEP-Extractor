"""
services/scraper.py
Core scraping logic: fetches chapters, downloads assets,
rewrites links, and compiles everything into a single HTML file.
"""

from __future__ import annotations

import logging
from collections import OrderedDict
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin, urldefrag, urlparse

from bs4 import BeautifulSoup

from core.config import settings
from utils.file_helpers import (
    asset_local_path,
    classify_url,
    ensure_dirs,
    html_escape,
    slugify,
)
from utils.http import RateLimitedSession, RobotsChecker
from .chapter_parser import parse_chapters

log = logging.getLogger(__name__)


class OSTEPScraper:
    """
    Orchestrates the full scrape-and-compile pipeline.

    Attributes:
        chapters   – ordered dict {chapter_id: {title, url, content}}
        asset_map  – {original_url: local Path}
        stats      – runtime counters for status reporting
    """

    def __init__(self, index_url: str | None = None, store=None) -> None:
        self.index_url  = index_url or settings.ostep_index_url
        self.session    = RateLimitedSession()
        self.robots     = RobotsChecker(self.session)
        self.chapters: OrderedDict[str, dict] = OrderedDict()
        self.asset_map: dict[str, Path]       = {}
        self.stats = {
            "chapters_found":    0,
            "chapters_fetched":  0,
            "assets_downloaded": 0,
        }
        # Optional live store reference for real-time WS updates
        self._store = store

    # ── Public entry point ─────────────────────────────────────────

    def run(self) -> Path:
        """
        Execute the full pipeline.  Returns the path to the compiled HTML.
        Raises RuntimeError on unrecoverable failures.
        """
        self._setup_dirs()

        log.info("=" * 65)
        log.info("OSTEP Scraper — index: %s", self.index_url)
        log.info("=" * 65)

        index_soup = self._fetch_index()
        chapter_list = parse_chapters(index_soup, self.index_url)

        if not chapter_list:
            raise RuntimeError("No chapters discovered from index page.")

        self.stats["chapters_found"] = len(chapter_list)
        self._push(chapters_found=len(chapter_list))

        # Register chapters
        for i, ch in enumerate(chapter_list):
            cid = f"{i:03d}-{slugify(ch['title'])[:40]}"
            self.chapters[cid] = {
                "title":   ch["title"],
                "url":     ch["url"],
                "content": None,
            }

        # Fetch each chapter — push a live update after every chapter
        for cid, cd in self.chapters.items():
            cd["content"] = self._fetch_chapter(cd["url"]) or ""
            if cd["content"]:
                self.stats["chapters_fetched"] += 1
            self.stats["assets_downloaded"] = len(self.asset_map)
            self._push(
                chapters_fetched=self.stats["chapters_fetched"],
                assets_downloaded=self.stats["assets_downloaded"],
            )

        html = self._compile_html()
        out  = settings.html_output
        out.write_text(html, encoding="utf-8")

        self.stats["assets_downloaded"] = len(self.asset_map)

        log.info("=" * 65)
        log.info("Done! Output: %s", out.resolve())
        log.info(
            "Chapters: %d / %d   Assets: %d",
            self.stats["chapters_fetched"],
            self.stats["chapters_found"],
            len(self.asset_map),
        )
        log.info("=" * 65)

        return out

    def _push(self, **fields) -> None:
        """Forward incremental stats to the job store (no-op if store not set)."""
        if self._store is not None:
            try:
                self._store.update(**fields)
            except Exception as exc:
                log.debug("store.update failed (non-fatal): %s", exc)

    # ── Private helpers ────────────────────────────────────────────

    def _setup_dirs(self) -> None:
        ensure_dirs(
            settings.output_path,
            settings.assets_path / "code",
            settings.assets_path / "images",
            settings.assets_path / "pdfs",
            settings.assets_path / "homework",
        )

    def _fetch_index(self) -> BeautifulSoup:
        resp = self.session.get(self.index_url)
        if not resp:
            raise RuntimeError(f"Cannot reach OSTEP index: {self.index_url}")
        return BeautifulSoup(resp.text, "html.parser")

    # ── Asset download ─────────────────────────────────────────────

    def _download_asset(self, url: str, category: str) -> Path | None:
        url, _ = urldefrag(url)
        if url in self.asset_map:
            return self.asset_map[url]
        if not self.robots.is_allowed(url):
            log.debug("robots.txt disallows: %s", url)
            return None

        local_path = asset_local_path(url, category, settings.assets_path)
        if local_path.exists():
            self.asset_map[url] = local_path
            return local_path

        log.info("  Downloading [%s] %s", category, url)
        resp = self.session.get(url)
        if not resp:
            return None

        local_path.parent.mkdir(parents=True, exist_ok=True)
        local_path.write_bytes(resp.content)
        self.asset_map[url] = local_path
        return local_path

    # ── Chapter fetching ───────────────────────────────────────────

    def _fetch_chapter(self, url: str) -> str | None:
        url, _ = urldefrag(url)
        if not self.robots.is_allowed(url):
            return None

        if url.lower().endswith(".pdf"):
            if url not in self.asset_map:
                self._download_asset(url, "pdfs")
            return self._embed_pdf(url)

        log.info("Fetching HTML chapter: %s", url)
        resp = self.session.get(url)
        if not resp:
            return None

        soup = BeautifulSoup(resp.text, "html.parser")
        self._download_linked_assets(soup, url)
        return self._rewrite_links(soup, url)

    def _embed_pdf(self, url: str) -> str:
        local = self._download_asset(url, "pdfs")
        if not local:
            return '<p class="err">PDF download failed.</p>'

        rel  = str(local.relative_to(settings.output_path)).replace("\\", "/")
        name = Path(url).name
        return (
            f'<div class="pdf-wrap">'
            f'<p><a class="pdf-btn" href="{rel}" target="_blank">'
            f'&#128196; Open / download: {html_escape(name)}</a></p>'
            f'<iframe src="{rel}" width="100%" height="950px"></iframe>'
            f'</div>'
        )

    def _download_linked_assets(self, soup: BeautifulSoup, page_url: str) -> None:
        for tag, attr in [("img", "src"), ("a", "href"), ("link", "href"), ("script", "src")]:
            for el in soup.find_all(tag, **{attr: True}):
                raw = el[attr].strip()
                if not raw or raw.startswith(("data:", "javascript:", "#", "mailto:")):
                    continue
                abs_url = urljoin(page_url, raw)
                cat = classify_url(abs_url)
                if cat:
                    self._download_asset(abs_url, cat)

    # ── Link rewriting ─────────────────────────────────────────────

    def _rewrite_links(self, soup: BeautifulSoup, page_url: str) -> str:
        for tag, attr in [("a", "href"), ("img", "src"), ("link", "href"), ("script", "src")]:
            for el in soup.find_all(tag, **{attr: True}):
                raw = el[attr].strip()
                if not raw or raw.startswith(("data:", "javascript:", "mailto:")):
                    continue

                abs_url, frag = urldefrag(urljoin(page_url, raw))

                # Rewrite to internal chapter anchor?
                rewired = False
                for cid, cd in self.chapters.items():
                    if abs_url == cd["url"]:
                        anchor = f"#chapter-{cid}"
                        if frag:
                            anchor += f"-{slugify(frag)}"
                        el[attr] = anchor
                        rewired = True
                        break
                if rewired:
                    continue

                # Rewrite to downloaded asset?
                if abs_url in self.asset_map:
                    try:
                        rel = self.asset_map[abs_url].relative_to(settings.output_path)
                        el[attr] = str(rel).replace("\\", "/")
                    except ValueError:
                        pass
                    continue

                # Leave external links absolute
                el[attr] = abs_url

        body = soup.find("body")
        return str(body) if body else str(soup)

    # ── HTML compilation ───────────────────────────────────────────

    def _compile_html(self) -> str:
        log.info("Compiling final HTML …")

        toc_items:    list[str] = []
        section_html: list[str] = []

        for cid, cd in self.chapters.items():
            title   = cd["title"]
            content = cd.get("content") or '<p class="err">Content unavailable.</p>'
            toc_items.append(f'<li><a href="#chapter-{cid}">{html_escape(title)}</a></li>')
            section_html.append(
                f'<section id="chapter-{cid}" class="chapter">\n'
                f'<div class="chapter-inner">{content}</div>\n'
                f'</section>'
            )

        compiled_at = datetime.now().strftime("%Y-%m-%d %H:%M")
        toc_html    = "\n".join(toc_items)
        body_html   = "\n\n".join(section_html)

        return _HTML_TEMPLATE.format(
            toc_html=toc_html,
            body_html=body_html,
            compiled_at=compiled_at,
            index_url=self.index_url,
        )


# ── HTML template ──────────────────────────────────────────────────

_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>OSTEP — Operating Systems: Three Easy Pieces (Offline)</title>
<style>
*,*::before,*::after{{box-sizing:border-box;margin:0;padding:0}}
html{{scroll-behavior:smooth}}
body{{display:flex;background:#f5f4f0;font-size:16px}}

#toc{{
  position:fixed;top:0;left:0;width:275px;height:100vh;overflow-y:auto;
  background:#0f0f28;color:#aab;
  padding:1.5rem 1.1rem 3rem;
  font-family:"Helvetica Neue",Arial,sans-serif;font-size:.78rem;z-index:200
}}
#toc .logo{{font-size:1.6rem;font-weight:900;color:#fff;letter-spacing:-.5px}}
#toc .sub{{font-size:.68rem;color:#4455aa;line-height:1.5;margin-bottom:1.3rem}}
#toc ol{{list-style:none;counter-reset:c}}
#toc li{{counter-increment:c;margin:.18rem 0}}
#toc li::before{{content:counter(c)". ";color:#334499;min-width:1.8rem;display:inline-block}}
#toc a{{color:#7788cc;text-decoration:none}}
#toc a:hover{{color:#fff}}

#topbtn{{
  position:fixed;bottom:1.2rem;right:1.2rem;
  background:#0f0f28;color:#aac;border:1px solid #334;
  padding:.4rem .75rem;border-radius:5px;cursor:pointer;font-size:.8rem;
  opacity:.75;z-index:300
}}
#topbtn:hover{{opacity:1}}

#content{{margin-left:275px;max-width:900px;padding:2.5rem 2.5rem 4rem;width:100%}}

#banner{{
  background:linear-gradient(130deg,#0f0f28 0%,#1a2060 100%);
  color:#fff;padding:2rem 2.5rem;border-radius:8px;margin-bottom:2.5rem
}}
#banner h1{{font-family:Georgia,serif;font-size:1.8rem;margin-bottom:.35rem}}
#banner .m{{font-size:.8rem;color:#8899cc;line-height:1.6}}
#banner .m a{{color:#99aadd}}

.chapter{{border-top:2px solid #dedad4;padding-top:2rem;margin-top:2.5rem}}
.chapter:first-of-type{{border-top:none;margin-top:0}}
.chapter-inner{{font-family:Georgia,"Times New Roman",serif;line-height:1.78;color:#1a1a1a}}
.chapter-inner img{{max-width:100%;height:auto;display:block;margin:1rem auto;border-radius:3px}}
.chapter-inner h1{{font-size:2.1rem;margin:1.6rem 0 .7rem;color:#080820}}
.chapter-inner h2{{font-size:1.55rem;margin:1.4rem 0 .6rem;color:#141430}}
.chapter-inner h3{{font-size:1.2rem;margin:1.2rem 0 .5rem;color:#222240}}
.chapter-inner h4{{font-size:1rem;margin:1rem 0 .4rem}}
.chapter-inner p{{margin:.75rem 0}}
.chapter-inner ul,.chapter-inner ol{{margin:.5rem 0 .5rem 1.5rem}}
.chapter-inner li{{margin:.2rem 0}}
.chapter-inner pre{{
  background:#1c1c30;color:#d0d8f4;border-radius:6px;
  padding:1rem 1.2rem;overflow-x:auto;margin:1rem 0;
  font-family:"SFMono-Regular",Consolas,"Courier New",monospace;font-size:.84em;
  border-left:4px solid #4455cc
}}
.chapter-inner code{{
  background:#ece8f4;border-radius:3px;padding:.1em .35em;
  font-family:"SFMono-Regular",Consolas,"Courier New",monospace;font-size:.86em
}}
.chapter-inner pre code{{background:transparent;color:inherit;padding:0}}
.chapter-inner table{{border-collapse:collapse;width:100%;margin:.8rem 0;font-size:.9em}}
.chapter-inner th,.chapter-inner td{{border:1px solid #ccc;padding:.38rem .6rem}}
.chapter-inner th{{background:#e6e4f0;font-weight:700}}
.chapter-inner blockquote{{
  border-left:4px solid #8888cc;margin:1rem 0;padding:.5rem 1rem;
  color:#444;background:#f0eef8;border-radius:0 4px 4px 0
}}
.chapter-inner a{{color:#1a3a9a}}
.chapter-inner a:visited{{color:#552299}}
.chapter-inner hr{{border:none;border-top:1px solid #ddd;margin:1.5rem 0}}

.pdf-wrap{{margin:1rem 0}}
.pdf-btn{{
  display:inline-block;margin-bottom:.5rem;
  background:#0f0f28;color:#fff;padding:.38rem .85rem;
  border-radius:4px;text-decoration:none;
  font-family:"Helvetica Neue",Arial,sans-serif;font-size:.83rem
}}
.pdf-btn:hover{{background:#222244}}
.pdf-wrap iframe{{border:1px solid #ccc;border-radius:4px;display:block}}
.err{{color:#c00;font-style:italic}}

@media(max-width:760px){{
  #toc{{display:none}}
  #content{{margin-left:0;padding:1.5rem}}
}}
</style>
</head>
<body>

<nav id="toc">
  <div class="logo">OSTEP</div>
  <div class="sub">Operating Systems:<br>Three Easy Pieces<br>Arpaci-Dusseau · UW-Madison</div>
  <ol>
{toc_html}
  </ol>
</nav>

<button id="topbtn" onclick="window.scrollTo({{top:0,behavior:'smooth'}})">▲ Top</button>

<main id="content">
  <div id="banner">
    <h1>Operating Systems: Three Easy Pieces</h1>
    <p class="m">
      Remzi H. Arpaci-Dusseau &amp; Andrea C. Arpaci-Dusseau<br>
      University of Wisconsin-Madison<br>
      Offline edition compiled {compiled_at} &mdash;
      source: <a href="{index_url}">{index_url}</a>
    </p>
  </div>

{body_html}

</main>
</body>
</html>"""
