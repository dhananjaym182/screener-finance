"""Offline tests — parsers, dataframe conversion, exports (no network)."""
import io
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from bs4 import BeautifulSoup

from screener_finance.parse import (
    num,
    parse_all_sections,
    parse_company,
    parse_peers_fragment,
    parse_pros_cons,
    parse_top_ratios,
    parse_warehouse_id,
)
from screener_finance.dataframe import table_to_df
from screener_finance.normalize import (
    canonical,
    map_item_label,
    map_ratio_label,
    parse_period,
)

HTML = """
<html><body>
<h1>State Bank of India share price</h1>
<ul id="top-ratios">
  <li><span class="name">Market Cap </span> <span class="nowrap"> ₹ 9,20,107 </span></li>
  <li><span class="name">Current Price </span> <span class="nowrap"> ₹ 996 </span></li>
  <li><span class="name">High / Low </span> <span class="nowrap"> ₹ 1,235 / 810 </span></li>
  <li><span class="name">Stock P/E </span> <span class="nowrap"> 10.9 </span></li>
  <li><span class="name">Book Value </span> <span class="nowrap"> ₹ 674 </span></li>
  <li><span class="name">Dividend Yield </span> <span class="nowrap"> 1.74% </span></li>
  <li><span class="name">ROCE </span> <span class="nowrap"> 6.13% </span></li>
  <li><span class="name">ROE </span> <span class="nowrap"> 15.4% </span></li>
  <li><span class="name">Face Value </span> <span class="nowrap"> ₹ 1 </span></li>
</ul>
<section id="quarters">
  <table class="data-table">
    <thead><tr><th></th><th>Jun 2023 +</th><th>Sep 2023 +</th><th>Dec 2023 +</th></tr></thead>
    <tbody>
      <tr><td class="text">Revenue +</td><td class="number">1,01,460</td><td class="number">1,07,391</td><td class="number">1,12,868</td></tr>
      <tr><td class="text">Net Profit +</td><td class="number">17,925</td><td class="number">18,079</td><td class="number">18,312</td></tr>
    </tbody>
  </table>
</section>
<section id="profit-loss">
  <table class="data-table">
    <thead><tr><th></th><th>Mar 2015</th><th>Mar 2016</th></tr></thead>
    <tbody>
      <tr><td class="text">Sales +</td><td class="number">2,07,974</td><td class="number">2,20,633</td></tr>
    </tbody>
  </table>
</section>
<section id="balance-sheet"></section>
<section id="cash-flow">
  <table class="data-table">
    <thead><tr><th></th><th>Mar 2015</th><th>Mar 2016</th></tr></thead>
    <tbody>
      <tr><td class="text">Cash from Operating Activity +</td><td class="number">31,646</td><td class="number">37,545</td></tr>
      <tr><td class="text">Raw PDF</td><td class="">x</td><td class="">x</td></tr>
    </tbody>
  </table>
</section>
<div class="pros"><p class="title">Pros</p><ul><li>Healthy dividend payout</li></ul></div>
<div class="cons"><p class="title">Cons</p><ul><li>Low interest coverage ratio</li></ul></div>
<ul class="list-links">
  <li><a href="/uploads/annual_report.pdf">Annual Report 2025</a></li>
</ul>
<div id="company-info" data-warehouse-id="6598877"></div>
</body></html>
"""

PEERS_FRAGMENT = """
<div data-messages></div>
<table class="data-table text-nowrap striped">
  <tbody>
    <tr>
      <th class="text" scope="colgroup">S.No.</th>
      <th class="text" scope="colgroup">Company</th>
      <th class="text" scope="colgroup">CMP Rs.</th>
      <th class="text" scope="colgroup">P/E</th>
    </tr>
    <tr><td>1.</td><td><a href="/company/SBIN/">SBI</a></td><td>995.70</td><td>10.94</td></tr>
    <tr><td>2.</td><td><a href="/company/HDFCBANK/">HDFC Bank</a></td><td>1710.50</td><td>19.20</td></tr>
  </tbody>
</table>
"""


def test_num():
    assert num("1,01,460") == 101460
    assert num("1.74%") == 1.74
    assert num("₹ 995.7") == 995.7
    assert num("-") is None
    assert num("") is None
    assert num(None) is None


def test_top_ratios():
    soup = BeautifulSoup(HTML, "html.parser")
    r = parse_top_ratios(soup)
    assert r["Market Cap"] == 920107
    assert r["Stock P/E"] == 10.9
    assert r["Dividend Yield"] == 1.74
    assert r["ROE"] == 15.4


def test_sections():
    soup = BeautifulSoup(HTML, "html.parser")
    secs = parse_all_sections(soup)
    qr = secs["quarterly_results"]
    assert qr["headers"] == ["Jun 2023", "Sep 2023", "Dec 2023"]
    assert qr["rows"][0]["label"] == "Revenue"
    assert qr["rows"][0]["values"] == [101460, 107391, 112868]
    assert secs["profit_loss"]["headers"] == ["Mar 2015", "Mar 2016"]
    assert secs["balance_sheet"]["rows"] == []


def test_raw_pdf_row_stripped():
    """Screener's link-only 'Raw PDF' row must never leak into parsed data."""
    soup = BeautifulSoup(HTML, "html.parser")
    secs = parse_all_sections(soup)
    labels = [r["label"] for r in secs["cash_flow"]["rows"]]
    assert labels == ["Cash from Operating Activity"], labels
    assert all(r["label"].lower() != "raw pdf" for sec in secs.values()
               for r in sec["rows"])


def test_pros_cons():
    soup = BeautifulSoup(HTML, "html.parser")
    pc = parse_pros_cons(soup)
    assert pc["pros"] == ["Healthy dividend payout"]
    assert pc["cons"] == ["Low interest coverage ratio"]


def test_warehouse_id():
    soup = BeautifulSoup(HTML, "html.parser")
    assert parse_warehouse_id(soup) == "6598877"


def test_parse_company():
    soup = BeautifulSoup(HTML, "html.parser")
    rec = parse_company("SBIN", "consolidated", soup,
                        "https://www.screener.in/company/SBIN/consolidated/")
    assert rec["name"] == "State Bank of India"
    assert rec["top_ratios"]["Market Cap"] == 920107
    assert rec["warehouse_id"] == "6598877"
    assert rec["documents"][0]["url"] == "/uploads/annual_report.pdf"


def test_peers_fragment():
    frag = BeautifulSoup(PEERS_FRAGMENT, "html.parser")
    p = parse_peers_fragment(frag)
    assert p["columns"] == ["CMP Rs.", "P/E"], p["columns"]
    assert p["peers"][0]["name"] == "SBI"
    assert p["peers"][1]["name"] == "HDFC Bank"
    assert p["peers"][1]["values"]["CMP Rs."] == 1710.5
    assert p["peers"][1]["url"] == "/company/HDFCBANK/"


def test_table_to_df():
    soup = BeautifulSoup(HTML, "html.parser")
    secs = parse_all_sections(soup)
    df = table_to_df(secs["quarterly_results"])
    assert list(df.columns) == ["Jun 2023", "Sep 2023", "Dec 2023"]
    assert df.loc["Revenue", "Sep 2023"] == 107391
    assert df.loc["Net Profit", "Dec 2023"] == 18312


def test_exports():
    soup = BeautifulSoup(HTML, "html.parser")
    rec = parse_company("SBIN", "consolidated", soup, "https://x/")
    with tempfile.TemporaryDirectory() as tmp:
        jp = os.path.join(tmp, "SBIN.json")
        json.dump(rec, open(jp, "w"))
        assert json.load(open(jp))["symbol"] == "SBIN"


# ---- one-request guarantee -------------------------------------------------

def test_one_request_per_ticker():
    """All same-page accessors together cost exactly ONE company-page request."""
    import screener_finance.session as sess_mod
    from screener_finance.ticker import Ticker

    calls = {"page": 0, "text": 0}

    class FakeSession:
        def get_soup(self, path, use_cache=True):
            calls["page"] += 1
            return BeautifulSoup(HTML, "html.parser")

        def get_text(self, path, use_cache=True):
            calls["text"] += 1
            return "{}"

    orig = sess_mod._session
    sess_mod._session = FakeSession()
    try:
        t = Ticker("SBIN")
        t.fetch()
        _ = t.info
        _ = t.quarterly_results, t.profit_loss, t.balance_sheet
        _ = t.cash_flow, t.ratios, t.shareholding
        _ = t.pros, t.cons, t.about, t.documents
        # repeat accessors — still no extra calls
        _ = t.info, t.quarterly_results
        assert calls["page"] == 1, f"expected 1 page request, got {calls['page']}"
        assert calls["text"] == 0, calls["text"]
    finally:
        sess_mod._session = orig


def test_session_cache_dedupes_repeats():
    """Session TTL cache: same URL twice = one network call (across Tickers too)."""
    import screener_finance.session as sess_mod

    class FakeResp:
        status_code = 200
        text = HTML

    s = sess_mod.Session()
    calls = {"n": 0}

    def fake_request(path):
        calls["n"] += 1
        return FakeResp()

    s._request = fake_request
    s.get_soup("/company/SBIN/consolidated/")
    s.get_soup("/company/SBIN/consolidated/")  # served from TTL cache
    assert calls["n"] == 1, calls["n"]


def test_peers_fetched_once_and_cached():
    """Peers: one AJAX call, then cached in the record."""
    import screener_finance.session as sess_mod
    from screener_finance.ticker import Ticker

    class FakeSession:
        def __init__(self):
            self.page_calls = 0
            self.peer_calls = 0
            self.timeout = 5

        def _ensure(self):
            import types
            s = types.SimpleNamespace()
            s.get = lambda url, headers=None, timeout=None: self._peer_resp()
            return s

        def _peer_resp(self):
            self.peer_calls += 1
            import types
            r = types.SimpleNamespace()
            r.status_code = 200
            r.text = PEERS_FRAGMENT
            r.raise_for_status = lambda: None
            return r

        def get_soup(self, path, use_cache=True):
            self.page_calls += 1
            return BeautifulSoup(HTML, "html.parser")

        def get_text(self, path, use_cache=True):
            return "{}"

        def throttle_wait(self):
            pass

    fake = FakeSession()
    fake.throttle = types.SimpleNamespace(wait=lambda: None)
    orig = sess_mod._session
    sess_mod._session = fake
    try:
        t = Ticker("SBIN")
        _ = t.peers
        _ = t.peers
        _ = t.peers
        assert fake.page_calls == 1
        assert fake.peer_calls == 1, f"peers AJAX called {fake.peer_calls}x"
    finally:
        sess_mod._session = orig


import types  # noqa: E402  (used by peers test)


# ---- canonical normalization -------------------------------------------------

def test_parse_period():
    # Fiscal year ends Mar: Mar 2015 == FY2015, ends 2015-03-31
    p = parse_period("Mar 2015")
    assert p == {"period_end": "2015-03-31", "fiscal_year": "FY2015",
                 "period_type": None}, p
    # Non-Mar quarters roll into the next fiscal year: Jun 2023 -> FY2024
    p = parse_period("Jun 2023")
    assert p == {"period_end": "2023-06-30", "fiscal_year": "FY2024",
                 "period_type": None}, p
    # TTM column
    p = parse_period("TTM")
    assert p is not None and p["period_type"] == "TTM", p
    # two-digit years
    p = parse_period("Mar 99")
    assert p["period_end"] == "2099-03-31", p
    # non-periods
    assert parse_period("Revenue") is None
    assert parse_period("") is None


def test_map_labels():
    assert map_item_label("Sales") == "sales"
    assert map_item_label("Sales +") == "sales"
    assert map_item_label("Net Profit +") == "net_profit"
    assert map_item_label("Profit after tax") == "net_profit"
    assert map_item_label("EPS (Rs)") == "eps"
    assert map_item_label("Borrowings +") == "borrowings"
    assert map_item_label("Cash from Operating Activity") == "cash_from_operations"
    assert map_item_label("Promoters") == "promoters_pct"
    assert map_item_label("FII/FPI") in ("fii_pct", "fii_fpi")
    assert map_ratio_label("Market Cap") == "market_cap"
    assert map_ratio_label("Stock P/E") == "stock_pe"
    assert map_ratio_label("High / Low") == "high_low"


def test_canonical_structure():
    soup = BeautifulSoup(HTML, "html.parser")
    rec = parse_company("SBIN", "consolidated", soup, "https://x/")
    canon = canonical(rec)

    assert set(canon.keys()) == {"meta", "indicators", "statements"}
    assert canon["meta"]["symbol"] == "SBIN"
    # site plumbing lives only in meta
    assert "source_url" in canon["meta"]
    assert "scraped_at" not in json.dumps(canon["statements"])

    # indicators: canonical keys with units, combined High/Low split
    keys = set(canon["indicators"].keys())
    assert "market_cap" in keys and "stock_pe" in keys
    assert "high_52w" in keys and "low_52w" in keys
    assert "high_low" not in keys
    assert canon["indicators"]["market_cap"] == {"value": 920107, "unit": "INR_Cr"}
    assert canon["indicators"]["stock_pe"] == {"value": 10.9, "unit": "x"}
    assert canon["indicators"]["roe"] == {"value": 15.4, "unit": "%"}

    # statements: tidy rows with ISO periods and canonical items
    s = canon["statements"]
    assert s, "expected statement rows"
    row = next(r for r in s if r["section"] == "quarterly_results"
               and r["item"] == "sales")
    assert row["period_end"] in ("2023-06-30", "2023-09-30", "2023-12-31")
    assert row["period_type"] == "Q"
    assert row["value"] in (101460, 107391, 112868)
    fy = next(r for r in s if r["section"] == "profit_loss"
              and r["item"] == "sales")
    assert fy["period_end"] in ("2015-03-31", "2016-03-31")
    assert fy["period_type"] == "FY"
    assert fy["fiscal_year"] in ("FY2015", "FY2016")

    # no raw labels or period strings leak through
    blob = json.dumps(canon["statements"])
    assert "Revenue" not in blob and "Net Profit" not in blob
    assert "Mar 2015" not in blob and "Jun 2023" not in blob
    # footnote artifact "+" never appears
    assert "label" not in blob


def test_canonical_ticker_method_and_exports():
    import screener_finance.session as sess_mod
    from screener_finance.ticker import Ticker

    class FakeSession:
        def get_soup(self, path, use_cache=True):
            return BeautifulSoup(HTML, "html.parser")

        def get_text(self, path, use_cache=True):
            return "{}"

    orig = sess_mod._session
    sess_mod._session = FakeSession()
    try:
        t = Ticker("SBIN")
        t.fetch()                       # the one request
        canon = t.canonical()           # zero extra requests
        assert canon["meta"]["symbol"] == "SBIN"
        assert canon["indicators"]["high_52w"]["value"] == 1235
        assert canon["indicators"]["low_52w"]["value"] == 810

        with tempfile.TemporaryDirectory() as tmp:
            jp = t.to_canonical_json(os.path.join(tmp, "c.json"))
            loaded = json.load(open(jp))
            assert loaded["meta"]["symbol"] == "SBIN"

        tidy = t.canonical_tidy_csv()
        assert tidy.splitlines()[0].startswith("symbol,view,section,item")
        assert "2023-09-30" in tidy

        ind = t.canonical_indicators_csv()
        assert ind.splitlines()[0] == "key,value,unit"
        hl_lines = [ln for ln in ind.splitlines() if ln.startswith("high_52w,")]
        assert hl_lines and hl_lines[0].endswith(",INR"), hl_lines
        assert "low_52w," in ind
    finally:
        sess_mod._session = orig


if __name__ == "__main__":
    test_num()
    test_sections()
    test_raw_pdf_row_stripped()
    test_top_ratios()
    test_sections()
    test_pros_cons()
    test_warehouse_id()
    test_parse_company()
    test_peers_fragment()
    test_table_to_df()
    test_exports()
    test_one_request_per_ticker()
    test_session_cache_dedupes_repeats()
    test_peers_fetched_once_and_cached()
    test_parse_period()
    test_map_labels()
    test_canonical_structure()
    test_canonical_ticker_method_and_exports()
    print("ALL OFFLINE TESTS PASSED")
