"""Screener Finance — one-request Python API for Indian stock fundamentals.

Built on top of the proven scraping patterns of screenercli and screener-india,
unified into a single clean library:

    import screener_finance as sf

    t = sf.Ticker("RELIANCE")
    record = t.fetch()          # ONE request -> full parsed record (cached)
    t.data                      # same record, free view
    t.info                      # key metrics dict        (no extra request)
    t.quarterly_results         # pandas DataFrame        (no extra request)
    t.profit_loss               # pandas DataFrame        (no extra request)
    t.balance_sheet / t.cash_flow / t.ratios / t.shareholding
    t.peers                     # one small AJAX call, lazy + cached
    t.history("5y")             # one chart-API call, lazy + cached

    sf.download(["SBIN", "TCS"])        # batch, paced by one shared session
    sf.batch_download(symbols, "out/")  # bulk to disk with resume
    sf.search("tata") / sf.compare([...]) / sf.configure(delay=…)

Design rule: exactly ONE scrape per company page. Every accessor after
`fetch()` is a zero-cost view over that parsed record.
"""
from __future__ import annotations

__version__ = "0.3.0"

from .ticker import Ticker
from .download import download, batch_download
from .search import search, compare
from .session import configure
from .normalize import canonical, parse_period
from . import universe
from .exceptions import (
    CompanyNotFoundError,
    RateLimitError,
    ScreenerError,
    ViewUnavailableError,
)

__all__ = [
    "Ticker",
    "download",
    "batch_download",
    "search",
    "compare",
    "configure",
    "canonical",
    "parse_period",
    "universe",
    "ScreenerError",
    "CompanyNotFoundError",
    "RateLimitError",
    "ViewUnavailableError",
    "__version__",
]
