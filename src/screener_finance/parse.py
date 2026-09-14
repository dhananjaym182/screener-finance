"""HTML parsing: screener.in company pages -> dicts."""
from __future__ import annotations

import re
from typing import Any

from bs4 import BeautifulSoup

_NUM_RE = re.compile(r"-?[\d,]*\.?\d+")


def num(v: Any) -> float | None:
    """'1,01,460' / '10.9' / '1.74%' / '₹ 995.7' / '-' -> float | None."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).replace(",", "").replace("₹", "").strip().rstrip("%").strip()
    if s in ("", "-", "—", "–", "N/A", "nan"):
        return None
    try:
        return float(s)
    except ValueError:
        m = _NUM_RE.search(s)
        return float(m.group(0).replace(",", "")) if m else None


def cell_text(cell) -> str:
    return cell.get_text(" ", strip=True)


def _strip_footnote(label: str) -> str:
    return re.sub(r"\s*[*+±]\s*$", "", label).strip()


def parse_top_ratios(soup: BeautifulSoup) -> dict[str, float | None]:
    ratios: dict[str, float | None] = {}
    ul = soup.select_one("ul#top-ratios")
    if not ul:
        return ratios
    for li in ul.select("li"):
        name_el = li.select_one("span.name")
        if not name_el:
            continue
        name = cell_text(name_el).rstrip(":").strip()
        value_el = li.select_one("span.nowrap") or li.select_one("span.number")
        ratios[name] = num(cell_text(value_el)) if value_el else None
    return ratios


def parse_financial_table(section_el) -> tuple[list[str], list[dict]]:
    """One data-tables section -> (headers, rows).

    rows: [{"label": str, "values": [float|None], "raw": [str]}]
    """
    table = section_el.select_one("table.data-table") if section_el else None
    if not table:
        return [], []

    header_cells = table.select("thead th")
    headers = [_strip_footnote(cell_text(th)) for th in header_cells[1:]]

    rows: list[dict] = []
    for tr in table.select("tbody tr"):
        cells = tr.select("td")
        if not cells:
            continue
        label = _strip_footnote(cell_text(cells[0]))
        raw = [cell_text(td) for td in cells[1:]]
        vals = [num(x) for x in raw]
        if len(vals) < len(headers):
            vals += [None] * (len(headers) - len(vals))
        rows.append({"label": label, "values": vals[:len(headers)], "raw": raw})
    return headers, rows


SECTION_IDS = {
    "quarterly_results": "quarters",
    "profit_loss": "profit-loss",
    "balance_sheet": "balance-sheet",
    "cash_flow": "cash-flow",
    "ratios": "ratios",
    "shareholding": "shareholding",
}


def parse_all_sections(soup: BeautifulSoup) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for key, dom_id in SECTION_IDS.items():
        section = soup.select_one(f"section#{dom_id}")
        headers, rows = parse_financial_table(section)
        out[key] = {"headers": headers, "rows": rows}
    return out


def parse_pros_cons(soup: BeautifulSoup) -> dict[str, list[str]]:
    pros, cons = [], []
    for sel in ["div.pros ul li", "ul.pros li"]:
        pros = [cell_text(li) for li in soup.select(sel)]
        if pros:
            break
    for sel in ["div.cons ul li", "ul.cons li"]:
        cons = [cell_text(li) for li in soup.select(sel)]
        if cons:
            break
    return {"pros": pros, "cons": cons}


def parse_about(soup: BeautifulSoup) -> str:
    el = soup.select_one("div.company-profile .sub") or soup.select_one(".company-info")
    return cell_text(el) if el else ""


def parse_documents(soup: BeautifulSoup) -> list[dict]:
    docs: list[dict] = []
    for a in soup.select("ul.list-links a[href]"):
        docs.append({"title": cell_text(a), "url": a["href"]})
    return docs


def parse_company_name(soup: BeautifulSoup, fallback: str) -> str:
    el = soup.select_one("h1")
    name = cell_text(el) if el else fallback
    return re.sub(r"\s*share price$", "", name, flags=re.I).strip()


def parse_warehouse_id(soup: BeautifulSoup) -> str | None:
    """Numeric warehouse id from the company-info element (used by AJAX APIs)."""
    el = soup.select_one("#company-info[data-warehouse-id]")
    if el is None:
        el = soup.select_one("[data-warehouse-id]")
    return el["data-warehouse-id"] if el else None


def parse_peers_fragment(fragment: BeautifulSoup) -> dict:
    """Parse the peers AJAX fragment (POST /api/company/<id>/peers/).

    Fragment structure: one <table>, header cells as <th> in the first <tr>
    (no thead): 'S.No.', 'Company', 'CMP Rs.', 'P/E', ... Rows are <td> cells
    with the company link inside the second column.
    """
    table = fragment.select_one("table")
    if table is None:
        return {"columns": [], "peers": []}

    rows = table.select("tr")
    if not rows:
        return {"columns": [], "peers": []}

    header_cells = [cell_text(c) for c in rows[0].select("th")]
    if not header_cells:
        header_cells = [cell_text(c) for c in rows[0].select("td")]
    # columns exclude 'S.No.' and 'Company'
    columns = [h for h in header_cells
               if h.lower() not in ("s.no.", "s.no", "company")]

    peers: list[dict] = []
    for tr in rows[1:]:
        tds = tr.select("td")
        if len(tds) < 2:
            continue
        link = tds[1].select_one("a[href]") if len(tds) > 1 else None
        name = cell_text(link) if link else cell_text(tds[1])
        url = link["href"] if link and link.has_attr("href") else ""
        values: dict[str, float | None] = {}
        vi = 0
        for td in tds[2:]:
            if vi >= len(columns):
                break
            values[columns[vi]] = num(cell_text(td))
            vi += 1
        peers.append({"name": name, "url": url, "values": values})
    return {"columns": columns, "peers": peers}


def parse_growth_tables(soup: BeautifulSoup) -> list[dict]:
    """Compounded growth tables under the Profit & Loss section."""
    out: list[dict] = []
    for sec in soup.select("section#profit-loss ul.data-table, section#profit-loss .ranges"):
        pass
    # screener renders growth tables as simple <ul>/<table> pairs after the P&L;
    # fall back to scanning sibling tables with few rows
    pl = soup.select_one("section#profit-loss")
    if not pl:
        return out
    for table in pl.select("table.data-table:not(.data-table)"):
        pass
    return out


def parse_company(symbol: str, view: str, soup: BeautifulSoup, source_url: str) -> dict:
    name = parse_company_name(soup, symbol)
    return {
        "symbol": symbol.upper(),
        "name": name,
        "view": view,
        "warehouse_id": parse_warehouse_id(soup),
        "top_ratios": parse_top_ratios(soup),
        "sections": parse_all_sections(soup),
        "pros_cons": parse_pros_cons(soup),
        "about": parse_about(soup),
        "documents": parse_documents(soup),
        "peers": {"columns": [], "peers": []},  # filled lazily via AJAX
        "source_url": source_url,
        "scraped_at": __import__("time").strftime("%Y-%m-%dT%H:%M:%SZ", __import__("time").gmtime()),
    }
