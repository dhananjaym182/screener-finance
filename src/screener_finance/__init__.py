"""Screener Finance — a yfinance-style API for Indian stock fundamentals from Screener.in.

    import screener_finance as sf

    t = sf.Ticker("RELIANCE")
    t.info                # dict: market cap, PE, ROE, ...
    t.quarterly_results   # pandas DataFrame
    t.profit_loss         # pandas DataFrame
    t.balance_sheet
    t.cash_flow
    t.ratios
    t.shareholding
    t.history(period="5y")          # experimental
    sf.download(["SBIN", "TCS"])    # batch -> dict of DataFrames

Made for the Indian market (NSE/BSE). Data source: https://www.screener.in
"""
from __future__ import annotations

__version__ = "0.1.0"

from .ticker import Ticker
from .download import download, batch_download
from .search import search, compare
from .session import configure
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
    "ScreenerError",
    "CompanyNotFoundError",
    "RateLimitError",
    "ViewUnavailableError",
    "__version__",
]
