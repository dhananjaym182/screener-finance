"""Search screener.in and build peer-style comparisons."""
from __future__ import annotations

import re
from typing import Any

from .session import BASE_URL, get_session
from .ticker import Ticker


def search(query: str, limit: int = 10) -> list[dict[str, Any]]:
    """Search companies by name/symbol via screener.in's JSON search endpoint.

        sf.search("tata", 5)
        -> [{"name": "Tata Consultancy Services Ltd", "url": "/company/TCS/"}, ...]
    """
    sess = get_session()
    soup = sess.get_soup(f"/search/?q={re.quote(query)}")
    # results render as <li> rows with an <a href="/company/SYMBOL/">
    out: list[dict[str, Any]] = []
    for a in soup.select("a[href^='/company/']"):
        name = a.get_text(" ", strip=True)
        url = a["href"]
        m = re.match(r"/company/([^/]+)/", url)
        symbol = m.group(1).upper() if m else url
        if name:
            out.append({"name": name, "symbol": symbol, "url": url})
        if len(out) >= limit:
            break
    return out


def compare(symbols: list[str], view: str = "consolidated") -> "Any":  # pd.DataFrame
    """Side-by-side key metrics for several symbols.

        sf.compare(["TCS", "INFY", "WIPRO"])  -> DataFrame (index=symbol)
    """
    import pandas as pd
    rows: dict[str, dict] = {}
    for sym in symbols:
        try:
            info = Ticker(sym, view).info
            rows[sym.upper()] = {
                k: v for k, v in info.items()
                if k in ("name", "market_cap", "current_price", "stock_pe",
                         "book_value", "dividend_yield", "roce", "roe",
                         "face_value")
            }
        except Exception as exc:
            rows[sym.upper()] = {"error": str(exc)}
    return pd.DataFrame.from_dict(rows, orient="index")
