"""Stock symbol universe builder — all active NSE stocks, keyless.

Primary source: NSE's public listed-equity CSV (EQUITY_L.csv) — the same
official archive file used to build Nifty 500 constituent lists. One request,
no login, includes SYMBOL + company name + ISIN + series filter (EQ/BE/BZ are
the tradable equity series).

Fallback: any screener.in public screen URL (id or URL) — results are parsed
from the server-rendered table, page by page.

Usage:
    from screener_finance import universe
    syms = universe.nse_active()               # ~2,570 symbols, ONE request
    syms = universe.from_screen("178")         # any public screener screen
    universe.save(syms, "nse_symbols.txt")
"""
from __future__ import annotations

import csv
import io
import re
import time
from typing import Callable

from .session import get_session

NSE_EQUITY_URLS = (
    "https://archives.nseindia.com/content/equities/EQUITY_L.csv",
    "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv",
)
TRADABLE_SERIES = {"EQ", "BE", "BZ"}

_SYM_HREF = re.compile(r"href=\"/company/([^/\"]+)/\"")
_TOTAL_RE = re.compile(r"([\d,]+)\s+results?")


# ---------------------------------------------------------------------------
# Primary: NSE listed-equity CSV (one request)
# ---------------------------------------------------------------------------

def nse_active(progress: Callable[[str], None] | None = None,
               series: set[str] | None = None) -> list[str]:
    """All active NSE equity symbols (~2,570) in one CSV request.

    series: tradable series filter (default EQ/BE/BZ). Returns symbols sorted
    alphabetically (deduplicated).
    """
    series = series or TRADABLE_SERIES
    sess = get_session()
    last_exc: Exception | None = None
    for url in NSE_EQUITY_URLS:
        try:
            text = sess.get_text(url, use_cache=False)
            rows = csv.DictReader(io.StringIO(text))
            syms: list[str] = []
            names: dict[str, str] = {}
            for row in rows:
                sym = (row.get("SYMBOL") or "").strip().upper()
                ser = (row.get(" SERIES") or row.get("SERIES") or "").strip().upper()
                if sym and ser in series:
                    syms.append(sym)
                    names[sym] = (row.get("NAME OF COMPANY") or "").strip()
            if progress:
                progress(f"{len(syms)} symbols from {url.split('/')[2]}")
            return sorted(set(syms))
        except Exception as exc:  # try the next mirror
            last_exc = exc
    raise RuntimeError(f"could not download NSE equity list: {last_exc}")


def nse_active_with_names() -> dict[str, str]:
    """{symbol: company name} for all active NSE equities (one request)."""
    sess = get_session()
    last_exc: Exception | None = None
    for url in NSE_EQUITY_URLS:
        try:
            text = sess.get_text(url, use_cache=False)
            out: dict[str, str] = {}
            for row in csv.DictReader(io.StringIO(text)):
                sym = (row.get("SYMBOL") or "").strip().upper()
                ser = (row.get(" SERIES") or row.get("SERIES") or "").strip().upper()
                if sym and ser in TRADABLE_SERIES:
                    out[sym] = (row.get("NAME OF COMPANY") or "").strip()
            return out
        except Exception as exc:
            last_exc = exc
    raise RuntimeError(f"could not download NSE equity list: {last_exc}")


# ---------------------------------------------------------------------------
# Fallback: any public screener.in screen (paginated)
# ---------------------------------------------------------------------------

def _screen_url(screen: int | str) -> str:
    if isinstance(screen, int) or (isinstance(screen, str) and screen.isdigit()):
        return f"https://www.screener.in/screens/{screen}/"
    url = str(screen)
    return url if url.startswith("http") else f"https://www.screener.in{url}"


def from_screen(screen: int | str, max_pages: int = 100,
                progress: Callable[[int, int], None] | None = None) -> list[str]:
    """All symbols in a public screener.in screen, merged across ?page=N."""
    sess = get_session()
    base = _screen_url(screen)
    all_syms: list[str] = []
    seen: set[str] = set()
    total_hint: int | None = None

    for page in range(1, max_pages + 1):
        sep = "&" if "?" in base else "?"
        html = sess.get_text(f"{base}{sep}page={page}")
        syms = [m.group(1).strip().upper()
                for m in _SYM_HREF.finditer(html)]
        fresh = [s for s in syms if s and s not in seen]
        if not fresh:
            break
        seen.update(fresh)
        all_syms.extend(fresh)
        if progress:
            progress(page, len(all_syms))
        tm = _TOTAL_RE.search(html)
        if tm and total_hint is None:
            try:
                total_hint = int(tm.group(1).replace(",", ""))
            except ValueError:
                total_hint = None
        if total_hint is not None and len(all_syms) >= total_hint:
            break
    return all_syms


# ---------------------------------------------------------------------------

def save(symbols: list[str], path: str, note: str = "") -> str:
    """Write symbols to a one-per-line file (# comments allowed)."""
    with open(path, "w", encoding="utf-8") as fp:
        if note:
            fp.write(f"# {note}\n")
            fp.write(f"# generated {time.strftime('%Y-%m-%d')} via screener-finance\n")
        fp.write("\n".join(symbols) + "\n")
    return path
