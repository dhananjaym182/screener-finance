"""yfinance-style Ticker class for Screener.in company data."""
from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pandas is required at use time, imported lazily
    import pandas as pd

from .exceptions import ScreenerError, ViewUnavailableError
from .parse import num, parse_company
from .session import BASE_URL, get_session

VALID_VIEWS = ("consolidated", "standalone")


class Ticker:
    """One company from Screener.in.

        t = sf.Ticker("RELIANCE")            # consolidated (default)
        t = sf.Ticker("RELIANCE", "standalone")

        t.info                                # dict of key metrics
        t.quarterly_results                   # DataFrame (index=row label)
        t.profit_loss / balance_sheet / cash_flow / ratios / shareholding
        t.pros / t.cons                       # lists
        t.peers                               # DataFrame
        t.documents                           # list of {"title", "url"}
        t.to_json("sb.json") / t.to_csv("dir/")
    """

    def __init__(self, symbol: str, view: str = "consolidated"):
        symbol = symbol.strip().upper()
        if view not in VALID_VIEWS:
            raise ValueError(f"view must be one of {VALID_VIEWS}")
        self.symbol = symbol
        self.view = view
        self._record: dict[str, Any] | None = None
        self._fetched_view: str | None = None

    # ---- internals --------------------------------------------------------

    def _fetch(self, force: bool = False) -> dict[str, Any]:
        """Fetch + parse the page once; auto-fallback consolidated->standalone."""
        if self._record is not None and not force:
            return self._record
        sess = get_session()
        path = (f"/company/{self.symbol}/"
                if self.view == "standalone"
                else f"/company/{self.symbol}/consolidated/")
        soup = sess.get_soup(path)
        final_view = "consolidated" if "/consolidated" in path else "standalone"
        url = f"{BASE_URL}{path}"
        try:
            record = parse_company(self.symbol, final_view, soup, url)
        except Exception:
            raise
        # empty page (no tables & no ratios) -> try the other view once
        if not record["top_ratios"] and not any(record["sections"][k]["rows"]
                                                for k in record["sections"]):
            other = "standalone" if self.view == "consolidated" else "consolidated"
            p2 = (f"/company/{self.symbol}/"
                  if other == "standalone" else f"/company/{self.symbol}/consolidated/")
            soup2 = sess.get_soup(p2, use_cache=False)
            record = parse_company(self.symbol, other, soup2, f"{BASE_URL}{p2}")
            if not record["top_ratios"] and not any(record["sections"][k]["rows"]
                                                    for k in record["sections"]):
                raise ViewUnavailableError(self.symbol)
            self.view = other
        self._record = record
        self._fetched_view = record["view"]
        return record

    def _section(self, key: str) -> dict:
        return self._fetch()["sections"][key]

    # ---- info ---------------------------------------------------------

    @property
    def info(self) -> dict[str, Any]:
        """Key metrics dict, yfinance-style. Numeric values where possible.

        Includes: name, symbol, view, market_cap, current_price, stock_pe,
        book_value, dividend_yield, roce, roe, face_value, high_52w, low_52w,
        plus about/pros/cons and scraped_at/source_url.
        """
        rec = self._fetch()
        out: dict[str, Any] = {
            "symbol": rec["symbol"],
            "name": rec["name"],
            "view": rec["view"],
            "about": rec["about"],
            "source_url": rec["source_url"],
            "scraped_at": rec["scraped_at"],
        }
        mapping = {
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
        for k, v in rec["top_ratios"].items():
            key = mapping.get(k)
            if key is None:
                key = k.lower().replace(" ", "_").replace("/", "_")
            out[key] = v
        # split "High / Low" into two keys when present
        hl = rec["top_ratios"].get("High / Low")
        if hl is not None:
            # value parse keeps only the first number; reparse raw text
            ul = None
            from bs4 import BeautifulSoup  # local import; cheap
            # re-derive from raw page text is unnecessary — screener renders
            # "High / Low" as one combined value; keep both halves if possible:
            # we stored the numeric first half; also expose raw combined.
            out["high_52w"] = hl  # best-effort (first number)
            out["low_52w"] = None
        return out

    # ---- statement DataFrames -------------------------------------------

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

    # yfinance-friendly aliases -------------------------------------------
    # (yfinance uses .financials/.balance_sheet/.cashflow for annual statements)
    @property
    def financials(self) -> "pd.DataFrame":
        """Alias for profit_loss (yfinance-style naming)."""
        return self.profit_loss

    @property
    def balance_sheet_annual(self) -> "pd.DataFrame":
        return self.balance_sheet

    @property
    def cashflow(self) -> "pd.DataFrame":
        return self.cash_flow

    @property
    def quarterly_financials(self) -> "pd.DataFrame":
        return self.quarterly_results

    # ---- misc data --------------------------------------------------------

    @property
    def pros(self) -> list[str]:
        return self._fetch()["pros_cons"]["pros"]

    @property
    def cons(self) -> list[str]:
        return self._fetch()["pros_cons"]["cons"]

    @property
    def about(self) -> str:
        return self._fetch()["about"]

    @property
    def documents(self) -> list[dict]:
        return self._fetch()["documents"]

    @property
    def peers(self) -> "pd.DataFrame":
        """Peer comparison as DataFrame (index=peer name).

        Peers live behind a small AJAX endpoint, fetched on demand with the
        company's warehouse id (with Referer/X-Requested-With headers).
        """
        import pandas as pd
        rec = self._fetch()
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
        rows = {}
        for peer in p["peers"]:
            rows[peer["name"]] = peer["values"]
        return pd.DataFrame.from_dict(rows, orient="index")

    @property
    def raw(self) -> dict[str, Any]:
        """The full parsed record (everything above, as plain dict)."""
        return self._fetch()

    # ---- experimental price history ---------------------------------------

    def history(self, period: str = "1y", interval: str = "1d") -> "pd.DataFrame":
        """Daily price history (close) from screener.in's chart API.

            t.history("1y")          # 1 year of daily closes
            t.history("5y")          # 5 years

        Returns DataFrame indexed by date with a `close` column (plus any
        extra series screener includes, e.g. `volume` when available).
        """
        import json as _json
        import pandas as pd
        days = {"1m": 30, "3m": 91, "6m": 182, "1y": 365,
                "2y": 730, "5y": 1825, "max": 4000}.get(period)
        if days is None:
            raise ValueError("period must be one of 1m, 3m, 6m, 1y, 2y, 5y, max")
        if interval != "1d":
            raise ValueError("only interval='1d' is supported by screener.in")

        sess = get_session()
        # company_id lives on the already-cached company page
        record = self._fetch()
        company_id = getattr(self, "_company_id", None)
        if company_id is None:
            from bs4 import BeautifulSoup
            soup = sess.get_soup(record["source_url"].replace(BASE_URL, ""))
            el = soup.select_one("[data-company-id]")
            if el is None:
                raise ScreenerError(f"could not find company id for {self.symbol}")
            company_id = el["data-company-id"]
            self._company_id = company_id

        soup_json = sess.get_soup(
            f"/api/company/{company_id}/chart/?period={days}&interval=1d")
        text = soup_json.get_text()
        try:
            data = _json.loads(text)
        except Exception as exc:
            raise ScreenerError(
                f"chart endpoint returned non-JSON for {self.symbol}") from exc

        datasets = data.get("datasets", []) if isinstance(data, dict) else []
        if not datasets:
            raise ScreenerError(f"empty chart payload for {self.symbol}")

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
                # volume rows carry a third element: {"delivery": %}
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
        return df

    # ---- exports ------------------------------------------------------------

    def to_json(self, path: str) -> str:
        import json
        import os
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fp:
            json.dump(self.raw, fp, indent=1, ensure_ascii=False)
        return path

    def to_csv(self, directory: str) -> list[str]:
        """Write one CSV per statement + top_ratios + peers + pros/cons."""
        import csv
        import os
        import re as _re
        os.makedirs(directory, exist_ok=True)
        safe = _re.sub(r"[^A-Za-z0-9_-]+", "_", self.symbol)
        paths: list[str] = []

        def _write(name: str, headers: list[str], rows: list[list]):
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

        tr = self._fetch()["top_ratios"]
        if tr:
            _write("top_ratios", ["Ratio", "Value"], [[k, v] for k, v in tr.items()])

        peers = self._fetch()["peers"]
        if peers["peers"]:
            cols = peers["columns"]
            _write("peers", ["Name"] + cols,
                   [[p["name"]] + [p["values"].get(c) for c in cols] for p in peers["peers"]])

        pc = self._fetch()["pros_cons"]
        if pc["pros"] or pc["cons"]:
            _write("pros_cons", ["type", "text"],
                   [["pro", x] for x in pc["pros"]] + [["con", x] for x in pc["cons"]])

        docs = self._fetch()["documents"]
        if docs:
            _write("documents", ["title", "url"], [[d["title"], d["url"]] for d in docs])
        return paths

    # ---- repr ---------------------------------------------------------------

    def __repr__(self) -> str:
        try:
            rec = self._fetch()
            return (f"<Ticker {self.symbol} ({rec['view']}) {rec['name']!r}>")
        except Exception as exc:
            return f"<Ticker {self.symbol} (unfetched: {exc})>"
