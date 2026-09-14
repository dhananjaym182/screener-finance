"""HTTP session with the anti-blocking stack: browser headers, throttle + jitter,
TTL cache, Retry-After backoff, proxy support."""
from __future__ import annotations

import random
import time
from typing import Any

import requests

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

    def _ensure(self) -> requests.Session:
        if self._session is None:
            self._session = requests.Session()
            self._session.headers.update(DEFAULT_HEADERS)
            self._session.headers.update(self._user_headers)
            if self.proxy:
                self._session.proxies = {"http": self.proxy, "https": self.proxy}
        return self._session

    def configure(self, *, delay: float | None = None, jitter: float | None = None,
                  timeout: int | None = None, max_retries: int | None = None,
                  proxy: str | None = None, headers: dict[str, str] | None = None,
                  cache_ttl: float | None = None, user_agent: str | None = None) -> None:
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

    def get_soup(self, path: str, use_cache: bool = True) -> BeautifulSoup:
        url = path if path.startswith("http") else f"{BASE_URL}{path}"
        if use_cache:
            hit = self.cache.get(url)
            if hit is not None:
                return hit
        s = self._ensure()
        last_exc: Exception | None = None
        for attempt in range(self.max_retries):
            self.throttle.wait()
            try:
                resp = s.get(url, timeout=self.timeout)
            except requests.Timeout as exc:
                last_exc = exc
                continue
            except requests.ConnectionError as exc:
                last_exc = exc
                time.sleep(2 * (attempt + 1))
                continue

            if resp.status_code == 200:
                soup = BeautifulSoup(resp.text, "lxml")
                self.cache.put(url, soup)
                return soup
            if resp.status_code == 404:
                raise CompanyNotFoundError(url)
            if resp.status_code == 429:
                ra = resp.headers.get("Retry-After")
                wait_s = int(ra) if (ra or "").isdigit() else 2 ** (attempt + 1)
                if attempt < self.max_retries - 1:
                    time.sleep(wait_s)
                    continue
                raise RateLimitError(int(ra) if (ra or "").isdigit() else None)
            resp.raise_for_status()
        raise ScreenerError(f"GET {url} failed after {self.max_retries} attempts: {last_exc}")


# module-level singleton, yfinance-style global config
_session = Session()


def configure(**kwargs) -> None:
    """Configure the shared session:

        sf.configure(delay=2.0, proxy="socks5://...", user_agent="...")

    Keys: delay, jitter, timeout, max_retries, proxy, headers, cache_ttl, user_agent.
    """
    _session.configure(**kwargs)


def get_session() -> Session:
    return _session
