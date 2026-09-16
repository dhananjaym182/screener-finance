"""Canonical normalization: scraped record -> clean fundamentals dataset.

This is the layer that makes the output indistinguishable from a curated
market-data feed. Every scrape artifact is removed at the boundary:

  Scrape artifact                      -> Canonical form
  ----------------------------------------------------------------------
  "Mar 2015" column headers            -> ISO period_end + fiscal_year +
                                          period_type (FY/Q/TYM)
  "Jun 2023 +", "Sales +"              -> canonical item key
                                          ("sales", "net_profit", ...)
  "1,01,460" / "₹ 995" / "6.13%"       -> float (already parsed upstream)
  "Cr." units embedded in labels       -> units declared once at table level
  High / Low combined cell             -> separate high_52w / low_52w floats
  site-specific ratio spellings        -> one canonical key namespace
  source_url / scraped_at / view       -> separate `meta` block, not mixed
                                          into the data

The raw record is never mutated. The same request feeds both.
"""
from __future__ import annotations

import re
from typing import Any

# --------------------------------------------------------------------------
# Period parsing: "Mar 2015" / "Jun 2023" -> structured period
# --------------------------------------------------------------------------

_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

_PERIOD_RE = re.compile(
    r"^([A-Za-z]{3})\s*(\d{2,4})(?:\s*(TTM|TTM\+))?$", re.I)


def parse_period(text: str) -> dict[str, Any] | None:
    """'Mar 2015' -> ISO period_end + fiscal_year.

    India's fiscal year runs Apr-Mar, so Mar 2015 == FY2015 ends
    2015-03-31; any other end month rolls into the *next* calendar
    year's fiscal year (Jun 2023 -> FY2024, ends 2024-06-30).
    """
    s = (text or "").strip()
    if s.upper().rstrip("+") == "TTM":
        # Trailing-twelve-months column: no fixed period end.
        return {"period_end": "", "fiscal_year": None, "period_type": "TTM"}
    m = _PERIOD_RE.match(s)
    if not m:
        return None
    mon, yr, tail = m.group(1).lower(), int(m.group(2)), (m.group(3) or "").upper()
    if yr < 100:
        yr += 2000
    if mon not in _MONTHS:
        return None
    month = _MONTHS[mon]

    import calendar
    last_day = calendar.monthrange(yr, month)[1]

    fiscal = yr if month == 3 else yr + 1
    return {
        "period_end": f"{yr:04d}-{month:02d}-{last_day:02d}",
        "fiscal_year": f"FY{fiscal}",
        "period_type": "TTM" if tail.startswith("TTM") else None,
    }


# --------------------------------------------------------------------------
# Statement items: label -> canonical key
# --------------------------------------------------------------------------

# Normalization helper applied before mapping: lowercase, strip punctuation,
# collapse whitespace, drop parenthetical qualifiers like "(net)".
def _norm_label(label: str) -> str:
    s = label.lower()
    s = re.sub(r"\([^)]*\)", " ", s)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


# Maps raw screener row labels (normalized) to canonical keys.
ITEM_MAP = {
    # P&L
    "sales revenue": "sales",
    "revenue": "sales",
    "sales": "sales",
    "other income": "other_income",
    "total income": "total_income",
    "expenses": "expenses",
    "raw material cost": "raw_material_cost",
    "power and fuel": "power_and_fuel",
    "purchase of stock in trade": "purchase_of_stock_in_trade",
    "employee cost": "employee_cost",
    "finance cost": "finance_cost",
    "depreciation": "depreciation",
    "profit before tax": "profit_before_tax",
    "operating profit": "operating_profit",
    "pbt excl other income": "operating_profit",
    "exceptional items loss gain": "exceptional_items",
    "profit before tax excl other income": "operating_profit",
    "tax": "tax",
    "profit after tax": "net_profit",
    "net profit": "net_profit",
    "eps in rs": "eps",
    "eps rs": "eps",
    "eps": "eps",
    "dividend payout": "dividend_payout",
    # Balance sheet
    "equity capital": "equity_capital",
    "reserves": "reserves",
    "borrowings": "borrowings",
    "other liabilities": "other_liabilities",
    "total liabilities": "total_liabilities",
    "fixed assets": "fixed_assets",
    "cwip": "cwip",
    "capital work in progress": "cwip",
    "investments": "investments",
    "other assets": "other_assets",
    "total assets": "total_assets",
    # Cash flow
    "cash from operating activity": "cash_from_operations",
    "cash from operating activity indirect method": "cash_from_operations",
    "cash from investing activity": "cash_from_investing",
    "cash from financing activity": "cash_from_financing",
    "net cash flow": "net_cash_flow",
    # Ratios
    "debtor days": "debtor_days",
    "inventory days": "inventory_days",
    "days payable": "days_payable",
    "cash conversion cycle": "cash_conversion_cycle",
    "working capital days": "working_capital_days",
    "roce": "roce",
    # Shareholding (%)
    "promoters": "promoters_pct",
    "promoter holding": "promoters_pct",
    "fiis": "fii_pct",
    "fii fpi": "fii_pct",
    "diis": "dii_pct",
    "dii": "dii_pct",
    "government": "government_pct",
    "public": "public_pct",
    "public others": "public_pct",
    "no of shareholders": "shareholders",
    "no. of shareholders": "shareholders",
    "number of shareholders": "shareholders",
}

# Units for each canonical item (declared once, applied to the whole table).
UNITS = {
    "market_cap": "INR_Cr",
    "eps": "INR",
    "face_value": "INR",
    "book_value": "INR",
    "current_price": "INR",
    "dividend_payout": "%",
    "dividend_yield": "%",
    "roe": "%",
    "roce": "%",
    "stock_pe": "x",
    "debtor_days": "days",
    "inventory_days": "days",
    "days_payable": "days",
    "cash_conversion_cycle": "days",
    "working_capital_days": "days",
    "promoters_pct": "%",
    "fii_pct": "%",
    "dii_pct": "%",
    "government_pct": "%",
    "public_pct": "%",
    "shareholders": "count",
}

# Top-ratio label -> canonical key (screener spellings).
RATIO_MAP = {
    "market cap": "market_cap",
    "current price": "current_price",
    "high low": "high_low",
    "stock p e": "stock_pe",
    "stock pe": "stock_pe",
    "book value": "book_value",
    "dividend yield": "dividend_yield",
    "roce": "roce",
    "roe": "roe",
    "face value": "face_value",
}


def map_item_label(label: str) -> str:
    """Row label -> canonical key. Falls back to a normalized form."""
    key = ITEM_MAP.get(_norm_label(label))
    if key:
        return key
    return _norm_label(label).replace(" ", "_")


def map_ratio_label(label: str) -> str:
    key = RATIO_MAP.get(_norm_label(label))
    if key:
        return key
    return _norm_label(label).replace(" ", "_")


# --------------------------------------------------------------------------
# Record-level normalization
# --------------------------------------------------------------------------

def _section_periods(headers: list[str]) -> list[dict[str, Any] | None]:
    return [parse_period(h) for h in (headers or [])]


def canonical(
    record: dict[str, Any],
    section_filter: list[str] | None = None,
) -> dict[str, Any]:
    """Raw parsed record -> canonical fundamentals dataset.

    Structure
    ---------
    {
      "meta":       {symbol, name, view, source_url, generated_at},
      "indicators": {canonical_key: {value, unit}},
      "statements": [{symbol, view, section, item, period_end,
                      fiscal_year, period_type, value}],
    }

    No site field names, no period strings like "Mar 2015", no embedded
    units, no footnote marks survive this boundary.
    """
    import time as _time

    meta = {
        "symbol": record["symbol"],
        "name": record["name"],
        "view": record.get("view"),
        "source_url": record.get("source_url"),
        "generated_at": _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime()),
    }

    # ---- indicators (snapshot ratios) ----
    # parse_top_ratios stores "High / Low" under its raw label plus the split
    # keys "high_52w" / "low_52w"; keep only the canonical split keys.
    indicators: dict[str, Any] = {}
    raw_ratios = record.get("top_ratios") or {}
    for label, value in raw_ratios.items():
        key = map_ratio_label(label)
        if key == "high_low":
            continue  # combined cell -> represented by high_52w / low_52w
        unit = UNITS.get(key, "INR" if "52w" in key else None)
        indicators[key] = {"value": value, "unit": unit}

    # ---- statements (tidy time series) ----
    # Record-wide latest quarter-end: fallback anchor for TTM columns in
    # sections that carry *only* a TTM header (rare; 3 of 4,541 companies).
    all_q_ends = [
        p["period_end"]
        for section in (record.get("sections") or {}).values()
        for p in _section_periods(section.get("headers") or [])
        if p and not p["period_type"] and p["period_end"]
    ]
    record_ttm_end = max(all_q_ends) if all_q_ends else ""

    statements: list[dict] = []
    for key, section in (record.get("sections") or {}).items():
        if section_filter and key not in section_filter:
            continue
        periods = _section_periods(section.get("headers") or [])
        if not periods:
            continue
        # TTM columns have no fixed period of their own; anchor them to the
        # section's latest quarter-end so every row stays joinable.
        q_ends = [p["period_end"] for p in periods
                  if p and not p["period_type"] and p["period_end"]]
        ttm_end = max(q_ends) if q_ends else record_ttm_end
        ttm_fy = None
        if ttm_end:
            y, m = int(ttm_end[:4]), int(ttm_end[5:7])
            ttm_fy = f"FY{y if m == 3 else y + 1}"
        for row in section.get("rows") or []:
            item = map_item_label(row["label"])
            values = row.get("values") or []
            for i, period in enumerate(periods):
                if period is None or i >= len(values):
                    continue
                if period["period_type"]:
                    ptype = "TTM"
                    period_end = period["period_end"] or ttm_end
                    fiscal_year = period["fiscal_year"] or ttm_fy
                elif period["period_end"][5:7] == "03":
                    ptype = "FY"
                    period_end = period["period_end"]
                    fiscal_year = period["fiscal_year"]
                else:
                    ptype = "Q"
                    period_end = period["period_end"]
                    fiscal_year = period["fiscal_year"]
                statements.append({
                    "symbol": record["symbol"],
                    "view": record.get("view"),
                    "section": key,
                    "item": item,
                    "period_end": period_end,
                    "fiscal_year": fiscal_year,
                    "period_type": ptype,
                    "value": values[i],
                })

    return {"meta": meta, "indicators": indicators, "statements": statements}


# --------------------------------------------------------------------------
# Export helpers (flat long table / wide pivot)
# --------------------------------------------------------------------------

TIDY_COLUMNS = [
    "symbol", "view", "section", "item", "period_end",
    "fiscal_year", "period_type", "value",
]


def statements_tidy_rows(canon: dict[str, Any]) -> list[list[Any]]:
    return [[s[c] for c in TIDY_COLUMNS] for s in canon["statements"]]


def indicator_rows(canon: dict[str, Any]) -> list[list[Any]]:
    return [[k, v.get("value"), v.get("unit")]
            for k, v in canon["indicators"].items()]
