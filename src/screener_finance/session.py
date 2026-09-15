"""HTTP session with the anti-blocking stack: browser headers, throttle + jitter,
TTL cache, Retry-After backoff, proxy support.

Logging
-------
All requests are logged on the "screener_finance" logger:

- DEBUG: every 200 response (url, elapsed, User-Agent)
- WARNING: 403 (blocked/WAF), 429 (rate-limited), 404, 5xx, timeouts,
  connection errors, retries
- ERROR: symbol failed after all retries

Attach a handler via `sf.configure(log_file="run.log")` or standard logging.
"""
from __future__ import annotations

import logging
import random
import time
from typing import Any

import requests

_log = logging.getLogger("screener_finance")
_log.addHandler(logging.NullHandler())

try:
    from bs4 import BeautifulSoup
except ImportError as exc:  # pragma: no cover
    raise ImportError("Install scraping deps: pip install 'screener-finance[network]' "
                      "(requests, beautifulsoup4, lxml)") from exc

BASE_URL = "https://www.screener.in"
DEFAULT_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"),
    "Accept": ("text/html,application/xhtml+xml,application/xml;q=0.9,"
               "image/avif,image/webp,*/*;q=0.8"),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
}

from .exceptions import CompanyNotFoundError, RateLimitError, ScreenerError  # noqa: E402


class Throttle:
    def __init__(self, min_interval: float, jitter: float = 0.4):
        self.min_interval = min_interval
        self.jitter = jitter
        self._last = 0.0

    def wait(self) -> None:
        now = time.monotonic()
        target = self._last + self.min_interval + random.uniform(0, self.jitter)
        if now < target:
            time.sleep(target - now)
        self._last = time.monotonic()


class TTLCache:
    def __init__(self, ttl: float = 300.0, maxsize: int = 128):
        self.ttl = ttl
        self.maxsize = maxsize
        self._d: dict[str, tuple[float, Any]] = {}

    def get(self, key: str) -> Any | None:
        hit = self._d.get(key)
        if not hit:
            return None
        ts, val = hit
        if time.monotonic() - ts > self.ttl:
            del self._d[key]
            return None
        return val

    def put(self, key: str, val: Any) -> None:
        if len(self._d) >= self.maxsize:
            oldest = min(self._d, key=lambda k: self._d[k][0])
            del self._d[oldest]
        self._d[key] = (time.monotonic(), val)


class Session:
    """One shared HTTP session. Configure via module-level `configure()`."""

    def __init__(self) -> None:
        self._session: requests.Session | None = None
        self.throttle = Throttle(1.5, 0.5)
        self.cache = TTLCache()
        self.timeout = 20
        self.max_retries = 3
        self.proxy = ""
        self._user_headers: dict[str, str] = {}
        # counters for monitoring (surfaced in logs and by callers)
        self.stats: dict[str, int] = {
            "requests": 0, "retries": 0, "timeouts": 0,
            "conn_errors": 0, "rate_limited": 0, "blocked": 0,
            "not_found": 0,
        }

    def _ensure(self) -> requests.Session:
        if self._session is None:
            self._session = requests.Session()
            self._session.headers.update(DEFAULT_HEADERS)
            self._session.headers.update(self._user_headers)
            if self.proxy:
                self._session.proxies = {"http": self.proxy, "https": self.proxy}
            _log.info("session started: ua=%r delay=%.2fs(+jitter %.2fs) proxy=%s",
                      self._session.headers.get("User-Agent", "?"),
                      self.throttle.min_interval, self.throttle.jitter,
                      self.proxy or "none")
        return self._session

    def configure(self, *, delay: float | None = None, jitter: float | None = None,
                  timeout: int | None = None, max_retries: int | None = None,
                  proxy: str | None = None, headers: dict[str, str] | None = None,
                  cache_ttl: float | None = None, user_agent: str | None = None,
                  log_file: str | None = None,
                  log_level: int | str | None = None) -> None:
        if log_file is not None:
            enable_file_logging(log_file, level=log_level)
        elif log_level is not None:
            _log.setLevel(log_level)
        if delay is not None:
            self.throttle.min_interval = delay
        if jitter is not None:
            self.throttle.jitter = jitter
        if timeout is not None:
            self.timeout = timeout
        if max_retries is not None:
            self.max_retries = max_retries
        if cache_ttl is not None:
            self.cache.ttl = cache_ttl
        if proxy is not None:
            self.proxy = proxy
        if user_agent is not None:
            self._user_headers["User-Agent"] = user_agent
        if headers:
            self._user_headers.update(headers)
        # re-apply if a session already exists
        if self._session is not None:
            if headers or user_agent:
                self._session.headers.update(self._user_headers)
            if proxy:
                self._session.proxies = {"http": self.proxy, "https": self.proxy}

    # ---- core fetch -------------------------------------------------------

    def _request(self, path: str) -> "requests.Response":
        """GET with throttle, retries and 429 handling. Returns a 200 response."""
        url = path if path.startswith("http") else f"{BASE_URL}{path}"
        s = self._ensure()
        ua = s.headers.get("User-Agent", "?")
        last_exc: Exception | None = None
        for attempt in range(self.max_retries):
            self.throttle.wait()
            t0 = time.monotonic()
            try:
                resp = s.get(url, timeout=self.timeout)
            except requests.Timeout as exc:
                self.stats["timeouts"] += 1
                last_exc = exc
                _log.warning("TIMEOUT (attempt %d/%d) %s", attempt + 1,
                             self.max_retries, url)
                continue
            except requests.ConnectionError as exc:
                self.stats["conn_errors"] += 1
                self.stats["retries"] += 1
                last_exc = exc
                _log.warning("CONNECTION-ERROR (attempt %d/%d) %s", attempt + 1,
                             self.max_retries, url)
                time.sleep(2 * (attempt + 1))
                continue

            elapsed = time.monotonic() - t0
            self.stats["requests"] += 1

            if resp.status_code == 200:
                _log.debug("GET %s -> 200 (%.2fs) ua=%r", url, elapsed, ua)
                return resp
            if resp.status_code == 404:
                self.stats["not_found"] += 1
                _log.warning("GET %s -> 404 NOT FOUND", url)
                raise CompanyNotFoundError(url)
            if resp.status_code == 403:
                self.stats["blocked"] += 1
                _log.warning("GET %s -> 403 FORBIDDEN — likely BLOCKED "
                             "(WAF/User-Agent). ua=%r", url, ua)
                resp.raise_for_status()
            if resp.status_code == 429:
                self.stats["rate_limited"] += 1
                ra = resp.headers.get("Retry-After")
                wait_s = int(ra) if (ra or "").isdigit() else 2 ** (attempt + 1)
                _log.warning("GET %s -> 429 RATE-LIMITED (Retry-After=%s); "
                             "backing off %.0fs (attempt %d/%d)",
                             url, ra, wait_s, attempt + 1, self.max_retries)
                if attempt < self.max_retries - 1:
                    self.stats["retries"] += 1
                    time.sleep(wait_s)
                    continue
                raise RateLimitError(int(ra) if (ra or "").isdigit() else None)
            _log.warning("GET %s -> HTTP %d", url, resp.status_code)
            resp.raise_for_status()
        raise ScreenerError(f"GET {url} failed after {self.max_retries} attempts: {last_exc}")

    def get_soup(self, path: str, use_cache: bool = True) -> BeautifulSoup:
        url = path if path.startswith("http") else f"{BASE_URL}{path}"
        if use_cache:
            hit = self.cache.get(url)
            if hit is not None:
                return hit
        resp = self._request(path)
        soup = BeautifulSoup(resp.text, "lxml")
        self.cache.put(url, soup)
        return soup

    def get_text(self, path: str, use_cache: bool = True) -> str:
        """Raw text for CSV/JSON endpoints (same throttle/retry/cache stack)."""
        url = path if path.startswith("http") else f"{BASE_URL}{path}"
        key = "text:" + url
        if use_cache:
            hit = self.cache.get(key)
            if hit is not None:
                return hit
        text = self._request(path).text
        self.cache.put(key, text)
        return text


# module-level singleton with global config
_session = Session()


def enable_file_logging(path: str, level: int | str | None = None) -> None:
    """Attach a file handler to the "screener_finance" logger.

    Every request afterwards is logged with status/elapsed/UA; blocks (403),
    rate limits (429) and retries come through as WARNING lines you can grep.
    """
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)-7s %(message)s"))
    _log.addHandler(handler)
    _log.setLevel(level if level is not None else logging.DEBUG)


def configure(**kwargs) -> None:
    """Configure the shared session:

        sf.configure(delay=2.0, proxy="socks5://...", user_agent="...",
                     log_file="run.log")

    Keys: delay, jitter, timeout, max_retries, proxy, headers, cache_ttl,
    user_agent, log_file, log_level.
    """
    _session.configure(**kwargs)


def get_session() -> Session:
    return _session
