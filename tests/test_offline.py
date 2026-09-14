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


if __name__ == "__main__":
    test_num()
    test_top_ratios()
    test_sections()
    test_pros_cons()
    test_warehouse_id()
    test_parse_company()
    test_peers_fragment()
    test_table_to_df()
    test_exports()
    print("ALL OFFLINE TESTS PASSED")
