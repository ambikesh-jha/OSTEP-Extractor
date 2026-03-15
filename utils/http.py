"""
utils/http.py
Rate-limited, robots-aware HTTP session wrapper.
"""

import logging
import time
import urllib.robotparser
from typing import Optional

import requests

from core.config import settings

log = logging.getLogger(__name__)


class RateLimitedSession:
    """
    Wraps requests.Session with:
    - Configurable request delay (polite crawling)
    - Automatic retry-free error logging
    - User-Agent injection
    """

    def __init__(
        self,
        delay: float = settings.request_delay,
        timeout: int = settings.request_timeout,
        user_agent: str = settings.user_agent,
    ) -> None:
        self._session = requests.Session()
        self._session.headers["User-Agent"] = user_agent
        self._delay = delay
        self._timeout = timeout
        self._last_request: float = 0.0

    def _throttle(self) -> None:
        elapsed = time.time() - self._last_request
        wait = self._delay - elapsed
        if wait > 0:
            time.sleep(wait)

    def get(self, url: str, **kwargs) -> Optional[requests.Response]:
        self._throttle()
        try:
            response = self._session.get(url, timeout=self._timeout, **kwargs)
            self._last_request = time.time()
            if response.status_code == 200:
                return response
            log.warning("HTTP %s — %s", response.status_code, url)
            return None
        except requests.RequestException as exc:
            log.error("Request failed: %s — %s", url, exc)
            return None

    def close(self) -> None:
        self._session.close()


class RobotsChecker:
    """
    Caches per-host robots.txt parsers and answers can_fetch queries.
    """

    def __init__(self, session: RateLimitedSession) -> None:
        self._session = session
        self._parsers: dict[str, urllib.robotparser.RobotFileParser] = {}

    def _get_parser(self, base_url: str) -> urllib.robotparser.RobotFileParser:
        if base_url not in self._parsers:
            rp = urllib.robotparser.RobotFileParser()
            rp.set_url(f"{base_url}/robots.txt")
            resp = self._session.get(f"{base_url}/robots.txt")
            rp.parse(resp.text.splitlines() if resp else [])
            self._parsers[base_url] = rp
        return self._parsers[base_url]

    def is_allowed(self, url: str) -> bool:
        from urllib.parse import urlparse
        parsed = urlparse(url)
        base = f"{parsed.scheme}://{parsed.netloc}"
        try:
            parser = self._get_parser(base)
            return parser.can_fetch(settings.user_agent, url)
        except Exception as exc:
            log.warning("robots.txt check failed for %s: %s", url, exc)
            return True  # fail open — don't block on parse errors
