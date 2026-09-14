"""Ticker: one company record from screener.in, served many ways.

Request model
-------------
- `fetch()` / `data`  -> the ONE company-page request; everything else reads
  from it. Calling accessors in any order costs nothing extra.
- `peers`             -> one small AJAX fragment (only when you touch it), cached.
- `history(period)`   -> one chart-API call per (company, period), cached.

Everything else is a zero-cost view over the fetched record.
"""
from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import pandas as pd

from .exceptions import ScreenerError, ViewUnavailableError
from .parse import num, parse_company
from .session import BASE_URL, get_session

VALID_VIEWS = ("consolidated", "standalone")

RATIO_ALIASES = {
    "Market Cap": "market_cap",
    "Current Price": "current_price",
    "High / Low": "high_low",
    "Stock P/E": "stock_pe",
    "Book Value": "book_value",
    "Dividend Yield": "dividend_yield",
    "ROCE": "roce",
    "ROE": "roe",
    "Face Value": "face_value",
}


class Ticker:
    """One company from screener.in.

    One-request model:
        t = sf.Ticker("RELIANCE")
        t.fetch()             # the single company-page request
        t.data                # the parsed record (free after fetch)
        t.info                # free
        t.quarterly_results   # free (DataFrame view)
        t.peers               # lazy: one AJAX call, then cached
        t.history("5y")       # lazy: one chart call per period, then cached

    Exports (free):
        t.to_json("sb.json") / t.to_csv("csv_dir/")
    """

    def __init__(self, symbol: str, view: str = "consolidated"):
        symbol = str(symbol).strip().upper()
        if not symbol:
            raise ValueError("symbol must be a non-empty NSE/BSE ticker, e.g. 'RELIANCE'")
        if view not in VALID_VIEWS:
            raise ValueError(f"view must be one of {VALID_VIEWS}")
        self.symbol = symbol
        self.view = view
        self._record: dict[str, Any] | None = None
        self._company_id: str | None = None
        self._history_cache: dict[str, "pd.DataFrame"] = {}

    # ------------------------------------------------------------------
    # The ONE request
    # ------------------------------------------------------------------

    def fetch(self, force: bool = False) -> dict[str, Any]:
        """Fetch + parse the company page (once per Ticker unless force=True).

        This is the only company-page network call. Every property/method that
        follows reads from the parsed record it returns.
        """
        if self._record is not None and not force:
            return self._record

        sess = get_session()
        path = (f"/company/{self.symbol}/"
                if self.view == "standalone"
                else f"/company/{self.symbol}/consolidated/")
        soup = sess.get_soup(path)
        final_view = "consolidated" if "/consolidated" in path else "standalone"
        url = f"{BASE_URL}{path}"

        record = parse_company(self.symbol, final_view, soup, url)

        # Blank page -> the requested view may not exist; try the other once.
        if not record["top_ratios"] and not any(
                record["sections"][k]["rows"] for k in record["sections"]):
            other = "standalone" if self.view == "consolidated" else "consolidated"
            p2 = (f"/company/{self.symbol}/"
                  if other == "standalone" else f"/company/{self.symbol}/consolidated/")
            soup2 = sess.get_soup(p2, use_cache=False)
            record = parse_company(self.symbol, other, soup2, f"{BASE_URL}{p2}")
            if not record["top_ratios"] and not any(
                    record["sections"][k]["rows"] for k in record["sections"]):
                raise ViewUnavailableError(self.symbol)
            self.view = other

        self._record = record
        return record

    @property
    def data(self) -> dict[str, Any]:
        """The parsed company record (fetches once if needed)."""
        return self.fetch()

    def refresh(self) -> dict[str, Any]:
        """Force a re-fetch of the company page (bypasses cache)."""
        self._history_cache.clear()
        return self.fetch(force=True)

    # ------------------------------------------------------------------
    # Zero-cost views over the fetched record
    # ------------------------------------------------------------------

    @property
    def info(self) -> dict[str, Any]:
        """Key metrics dict (from the fetched record — no extra request)."""
        rec = self.fetch()
        out: dict[str, Any] = {
            "symbol": rec["symbol"],
            "name": rec["name"],
            "view": rec["view"],
            "about": rec["about"],
            "source_url": rec["source_url"],
            "scraped_at": rec["scraped_at"],
        }
        for k, v in rec["top_ratios"].items():
            out[RATIO_ALIASES.get(k, k.lower().replace(" ", "_").replace("/", "_"))] = v
        return out

    def _section(self, key: str) -> dict:
        return self.fetch()["sections"][key]

    @property
    def quarterly_results(self) -> "pd.DataFrame":
        from .dataframe import table_to_df
        return table_to_df(self._section("quarterly_results"))

    @property
    def profit_loss(self) -> "pd.DataFrame":
        from .dataframe import table_to_df
        return table_to_df(self._section("profit_loss"))

    @property
    def balance_sheet(self) -> "pd.DataFrame":
        from .dataframe import table_to_df
        return table_to_df(self._section("balance_sheet"))

    @property
    def cash_flow(self) -> "pd.DataFrame":
        from .dataframe import table_to_df
        return table_to_df(self._section("cash_flow"))

    @property
    def ratios(self) -> "pd.DataFrame":
        from .dataframe import table_to_df
        return table_to_df(self._section("ratios"))

    @property
    def shareholding(self) -> "pd.DataFrame":
        from .dataframe import table_to_df
        return table_to_df(self._section("shareholding"))

    @property
    def pros(self) -> list[str]:
        return self.fetch()["pros_cons"]["pros"]

    @property
    def cons(self) -> list[str]:
        return self.fetch()["pros_cons"]["cons"]

    @property
    def about(self) -> str:
        return self.fetch()["about"]

    @property
    def documents(self) -> list[dict]:
        return self.fetch()["documents"]

    # ------------------------------------------------------------------
    # Lazy secondary endpoints (one small call each, then cached)
    # ------------------------------------------------------------------

    @property
    def peers(self) -> "pd.DataFrame":
        """Peer comparison DataFrame. One AJAX fragment per company, cached."""
        import pandas as pd
        rec = self.fetch()
        p = rec["peers"]
        if not p["peers"]:
            wid = rec.get("warehouse_id")
            if not wid:
                return pd.DataFrame()
            sess = get_session()
            s = sess._ensure()
            sess.throttle.wait()
            resp = s.get(
                f"{BASE_URL}/api/company/{wid}/peers/",
                headers={
                    "Referer": rec["source_url"],
                    "X-Requested-With": "XMLHttpRequest",
                },
                timeout=sess.timeout,
            )
            resp.raise_for_status()
            from bs4 import BeautifulSoup
            from .parse import parse_peers_fragment
            p = parse_peers_fragment(BeautifulSoup(resp.text, "lxml"))
            rec["peers"] = p
        if not p["peers"]:
            return pd.DataFrame()
        rows = {peer["name"]: peer["values"] for peer in p["peers"]}
        return pd.DataFrame.from_dict(rows, orient="index")

    def history(self, period: str = "1y") -> "pd.DataFrame":
        """Daily price series from the chart API (one call per period, cached).

        Columns: close, dma50, dma200, volume, delivery_pct (where available).
        period: 1m, 3m, 6m, 1y, 2y, 5y, max
        """
        import json as _json
        import pandas as pd

        if period in self._history_cache:
            return self._history_cache[period]

        days = {"1m": 30, "3m": 91, "6m": 182, "1y": 365,
                "2y": 730, "5y": 1825, "max": 4000}.get(period)
        if days is None:
            raise ValueError("period must be one of 1m, 3m, 6m, 1y, 2y, 5y, max")

        sess = get_session()
        rec = self.fetch()
        company_id = self._company_id
        if company_id is None:
            from bs4 import BeautifulSoup
            soup = sess.get_soup(rec["source_url"].replace(BASE_URL, ""))
            el = soup.select_one("[data-company-id]")
            if el is None:
                raise ScreenerError(f"could not find company id for {self.symbol}")
            company_id = el["data-company-id"]
            self._company_id = company_id

        soup_json = sess.get_soup(
            f"/api/company/{company_id}/chart/?period={days}&interval=1d")
        try:
            data = _json.loads(soup_json.get_text())
        except Exception as exc:
            raise ScreenerError(
                f"chart endpoint returned non-JSON for {self.symbol}") from exc

        datasets = data.get("datasets", []) if isinstance(data, dict) else []
        series: dict[str, dict[str, float]] = {}
        deliveries: dict[str, float] = {}
        for ds in datasets:
            metric = (ds.get("metric") or "value").lower().replace(" ", "_")
            series.setdefault(metric, {})
            for v in ds.get("values", []):
                if not v or len(v) < 2:
                    continue
                date, val = v[0], num(v[1])
                if val is not None:
                    series[metric][date] = val
                if len(v) >= 3 and isinstance(v[2], dict):
                    dlv = v[2].get("delivery")
                    if dlv is not None:
                        try:
                            deliveries[date] = float(dlv)
                        except (TypeError, ValueError):
                            pass

        for m, s in list(series.items()):
            if not s:
                del series[m]
        if not series:
            raise ScreenerError(f"empty chart payload for {self.symbol}")

        df = pd.DataFrame(series)
        df.index = pd.to_datetime(df.index)
        df.index.name = "date"
        df = df.sort_index()
        if "price" in df.columns and "close" not in df.columns:
            df = df.rename(columns={"price": "close"})
        if deliveries:
            df["delivery_pct"] = pd.Series(deliveries, dtype="float64")

        self._history_cache[period] = df
        return df

    # ------------------------------------------------------------------
    # Exports (free — operate on the fetched record)
    # ------------------------------------------------------------------

    def to_json(self, path: str) -> str:
        import json
        import os
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fp:
            json.dump(self.data, fp, indent=1, ensure_ascii=False)
        return path

    def to_csv(self, directory: str) -> list[str]:
        """One CSV per statement + top_ratios + peers + pros/cons + documents."""
        import csv
        import os
        import re as _re
        os.makedirs(directory, exist_ok=True)
        safe = _re.sub(r"[^A-Za-z0-9_-]+", "_", self.symbol)
        paths: list[str] = []

        def _write(name: str, headers: list[str], rows: list[list]) -> None:
            path = os.path.join(directory, f"{safe}_{name}.csv")
            with open(path, "w", newline="", encoding="utf-8") as fp:
                w = csv.writer(fp)
                w.writerow(headers)
                w.writerows(rows)
            paths.append(path)

        for key in ("quarterly_results", "profit_loss", "balance_sheet",
                    "cash_flow", "ratios", "shareholding"):
            sec = self._section(key)
            if sec["rows"]:
                _write(key, ["Item"] + sec["headers"],
                       [[r["label"]] + r["values"] for r in sec["rows"]])

        tr = self.fetch()["top_ratios"]
        if tr:
            _write("top_ratios", ["Ratio", "Value"], [[k, v] for k, v in tr.items()])

        peers = self.fetch()["peers"]
        if peers["peers"]:
            cols = peers["columns"]
            _write("peers", ["Name"] + cols,
                   [[p["name"]] + [p["values"].get(c) for c in cols] for p in peers["peers"]])

        pc = self.fetch()["pros_cons"]
        if pc["pros"] or pc["cons"]:
            _write("pros_cons", ["type", "text"],
                   [["pro", x] for x in pc["pros"]] + [["con", x] for x in pc["cons"]])

        docs = self.fetch()["documents"]
        if docs:
            _write("documents", ["title", "url"], [[d["title"], d["url"]] for d in docs])
        return paths

    # ------------------------------------------------------------------

    def __repr__(self) -> str:
        if self._record is not None:
            return f"<Ticker {self.symbol} ({self._record['view']}) {self._record['name']!r}>"
        return f"<Ticker {self.symbol} (not fetched — call .fetch() or any accessor)>"
