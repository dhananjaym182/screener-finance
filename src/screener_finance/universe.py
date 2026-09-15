"""Stock symbol universe builder — all active NSE + BSE stocks, keyless.

Sources (both official, one request each):
- NSE:  archives.nseindia.com EQUITY_L.csv (series EQ/BE/BZ)
- BSE:  daily Equity Bhavcopy CSV (equity series only; debt/ETF/rights excluded)

`all_unique()` merges both, deduplicating dual-listed companies by ISIN
(the NSE symbol wins); BSE-only companies are returned with their numeric
BSE code as the screener URL key (Ticker("AADIIND", key="530027")).

Usage:
    from screener_finance import universe
    syms = universe.nse_active()               # ~2,570 NSE symbols
    syms, keys = universe.all_unique()         # ~4,500 unique companies
    universe.save(syms, "all_symbols.txt")
"""
from __future__ import annotations

import csv
import datetime
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
BSE_BHAVCOPY = ("https://www.bseindia.com/download/BhavCopy/Equity/"
                "BhavCopy_BSE_CM_0_0_0_{date:%Y%m%d}_F_0000.CSV")
# BSE series that are common equity. Debt = F/IF, ETFs = E, rights = R.
BSE_EQUITY_SERIES = {"A", "B", "X", "XT", "T", "M", "MT", "Z", "G", "P"}

_SYM_OK = re.compile(r"^[A-Z0-9\-]+$")
_SYM_HREF = re.compile(r"href=\"/company/([^/\"]+)/\"")
_TOTAL_RE = re.compile(r"([\d,]+)\s+results?")


# ---------------------------------------------------------------------------
# Primary: NSE listed-equity CSV (one request)
# ---------------------------------------------------------------------------

def _nse_rows() -> list[dict]:
    """Raw NSE CSV rows: [{symbol, isin, name}] (tradable series only)."""
    sess = get_session()
    last_exc: Exception | None = None
    for url in NSE_EQUITY_URLS:
        try:
            text = sess.get_text(url, use_cache=False)
            out: list[dict] = []
            for row in csv.DictReader(io.StringIO(text)):
                sym = (row.get("SYMBOL") or "").strip().upper()
                ser = (row.get(" SERIES") or row.get("SERIES") or "").strip().upper()
                if sym and ser in TRADABLE_SERIES and _SYM_OK.match(sym):
                    out.append({
                        "symbol": sym,
                        "isin": (row.get(" ISIN NUMBER")
                                 or row.get("ISIN NUMBER") or "").strip(),
                        "name": ((row.get("NAME OF COMPANY")
                                  or row.get(" NAME OF COMPANY") or "").strip()),
                    })
            if out:
                return out
        except Exception as exc:  # try the next mirror
            last_exc = exc
    raise RuntimeError(f"could not download NSE equity list: {last_exc}")


def nse_active(progress: Callable[[str], None] | None = None,
               series: set[str] | None = None) -> list[str]:
    """All active NSE equity symbols (~2,570) in one CSV request.

    series: tradable series filter (default EQ/BE/BZ). Returns symbols sorted
    alphabetically (deduplicated).
    """
    series = series or TRADABLE_SERIES
    rows = [r for r in _nse_rows()]
    syms = sorted({r["symbol"] for r in rows})
    if progress:
        progress(f"{len(syms)} NSE symbols")
    return syms


def nse_active_with_names() -> dict[str, str]:
    """{symbol: company name} for all active NSE equities (one request)."""
    return {r["symbol"]: r["name"] for r in _nse_rows()}


# ---------------------------------------------------------------------------
# BSE: daily equity bhavcopy (equity series only)
# ---------------------------------------------------------------------------

def _parse_bhavcopy(text: str) -> list[dict]:
    """Bhavcopy CSV -> [{symbol, code, isin}] (equity series only)."""
    out: list[dict] = []
    for row in csv.DictReader(io.StringIO(text)):
        sym = (row.get("TckrSymb") or "").strip().upper()
        isin = (row.get("ISIN") or "").strip()
        ser = (row.get("SctySrs") or "").strip().upper()
        if (len(isin) == 12 and isin.startswith("INE")
                and ser in BSE_EQUITY_SERIES
                and sym and _SYM_OK.match(sym)
                and not sym.endswith(("-RE", "-XA"))):
            out.append({"symbol": sym,
                        "code": (row.get("FinInstrmId") or "").strip(),
                        "isin": isin})
    return out


def bse_rows(max_lookback: int = 6) -> list[dict]:
    """BSE equity rows from the most recent daily bhavcopy (one request)."""
    sess = get_session()
    last_exc: Exception | None = None
    day = datetime.date.today()
    for back in range(max_lookback):
        d = day - datetime.timedelta(days=back)
        url = BSE_BHAVCOPY.format(date=d)
        try:
            text = sess.get_text(url, use_cache=False)
            if len(text) > 100_000:  # real bhavcopy, not an error page
                return _parse_bhavcopy(text)
        except Exception as exc:
            last_exc = exc
    raise RuntimeError(f"could not download BSE bhavcopy: {last_exc}")


def bse_active() -> tuple[list[str], dict[str, str]]:
    """(BSE symbols, {symbol: numeric BSE code}) — one request."""
    rows = bse_rows()
    codes = {r["symbol"]: r["code"] for r in rows}
    return sorted(codes), codes


def all_unique() -> tuple[list[str], dict[str, str]]:
    """Full unique NSE + BSE universe, deduplicated by ISIN.

    Dual-listed companies appear once under their NSE symbol; BSE-only
    companies keep their BSE ticker and get a screener URL key (numeric
    BSE code). Returns (symbols, keys) — keys only contains BSE-only
    entries; pass it to batch_download(keys=...) or Ticker(key=...).
    """
    nse = _nse_rows()
    nse_isins = {r["isin"] for r in nse if r["isin"]}
    uniq = {r["symbol"] for r in nse}
    keys: dict[str, str] = {}
    for r in bse_rows():
        if r["isin"] in nse_isins or r["symbol"] in uniq:
            continue  # dual-listed (same ISIN) or ticker collision
        uniq.add(r["symbol"])
        if r["code"]:
            keys[r["symbol"]] = r["code"]
    return sorted(uniq), keys


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
