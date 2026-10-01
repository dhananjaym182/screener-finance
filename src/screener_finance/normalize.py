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

# Audited/extended-period stub column: "Mar 2015 15m" (the trailing NNm is
# how many months of figures the column contains). Kept as a real, joinable
# period at the stated end month; the stub suffix is exposed via `stub`.
# The affected P&L section always carries real "Mar YYYY" annual columns too,
# so stub columns are excluded from TTM anchoring (they are never the
# latest annual figure).
_STUB_PERIOD_RE = re.compile(
    r"^([A-Za-z]{3})\s+(\d{4})\s+(\d{1,2})m$", re.I)

# Trailing "NNm" duration suffix on an otherwise parseable header
# ("Mar 2023 15m" -> months=15). Used to recover stub info from headers.
_STUB_NOTE_RE = re.compile(r"\s+(\d{1,2})m$", re.I)


def parse_period(text: str) -> dict[str, Any] | None:
    """'Mar 2015' -> ISO period_end + fiscal_year.

    India's fiscal year runs Apr-Mar, so Mar 2015 == FY2015 ends
    2015-03-31; any other end month rolls into the *next* calendar
    year's fiscal year (Jun 2023 -> FY2024, ends 2024-06-30).
    Exception: Jan/Feb end months sit INSIDE FY<year> (Apr<year-1> -
    Mar<year>), so they map to the CURRENT calendar year's FY — a
    transition stub ending Jan 2026 belongs to FY2026, never FY2027.
    """
    s = (text or "").strip()
    if s.upper().rstrip("+") == "TTM":
        # Trailing-twelve-months column: no fixed period end.
        return {"period_end": "", "fiscal_year": None, "period_type": "TTM"}
    sm = _STUB_PERIOD_RE.match(s)
    if sm:
        mon, yr, months_n = sm.group(1).lower(), int(sm.group(2)), int(sm.group(3))
        if yr < 100:
            yr += 2000
        if mon not in _MONTHS:
            return None
        month = _MONTHS[mon]
        import calendar
        last_day = calendar.monthrange(yr, month)[1]
        # Jan/Feb end months fall inside the fiscal year that ends in Mar
        # of the SAME calendar year (see docstring).
        fiscal = yr if month <= 3 else yr + 1
        return {
            "period_end": f"{yr:04d}-{month:02d}-{last_day:02d}",
            "fiscal_year": f"FY{fiscal}",
            "period_type": None,
            "stub": True,
            "stub_months": months_n,
        }
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

    # Consistent fiscal-year rule for ALL non-March ends: Apr-Dec roll into
    # the NEXT fiscal year (Jun 2023 -> FY2024); Jan-Mar belong to the
    # fiscal year ending in Mar of the same calendar year (Jan 2026 ->
    # FY2026, Dec 2014 -> FY2015). The old blanket yr+1 mislabelled
    # transition stubs ("Jan 2026" -> FY2027, a year after FY2026).
    fiscal = yr if month <= 3 else yr + 1
    return {
        "period_end": f"{yr:04d}-{month:02d}-{last_day:02d}",
        "fiscal_year": f"FY{fiscal}",
        "period_type": "TTM" if tail.startswith("TTM") else None,
        "stub": False,
    }


def resolve_stub_periods(
    headers: list[str],
) -> dict[tuple[int, int], dict[str, Any]]:
    """Resolve stub-ness per (year, month) across a whole header list.

    The site only prints the "NNm" duration suffix on the P&L header of a
    stub period; the SAME period appears in balance_sheet / cash_flow /
    ratios with a plain "Mar 2023" header. Stub-ness is a property of the
    PERIOD, not of one section, so it must be resolved symbol-wide and
    applied to every ANNUAL section (quarterly_results / shareholding
    columns are quarters and snapshots by nature and are never flagged).

    Resolution per (year, month):
      - any header with an explicit "NNm" suffix -> stub, months = N;
      - else a Jan/Feb end month (a transition period inside the fiscal
        year ending that Mar) is conservatively flagged stub with an
        UNKNOWN duration (stub_months=None, period_type "STUB");
      - else (Mar/Jun/Sep/Dec ends, no suffix) -> normal period. Jun/Sep
        fiscal-year-end annual columns (KENNAMET, SIEMENS) are genuine
        12-month periods and must stay unflagged.

    Returns {(year, month): {"stub": bool, "months": int | None}}.
    """
    out: dict[tuple[int, int], dict[str, Any]] = {}
    for h in (headers or []):
        s = (h or "").strip()
        if s.upper().rstrip("+") == "TTM":
            continue
        base = _STUB_NOTE_RE.sub("", s)
        m = _PERIOD_RE.match(base)
        if not m:
            continue
        mon = m.group(1).lower()
        if mon not in _MONTHS:
            continue
        yr = int(m.group(2))
        if yr < 100:
            yr += 2000
        month = _MONTHS[mon]
        key = (yr, month)
        note = _STUB_NOTE_RE.search(s)
        if note:
            months = int(note.group(1))
            if not out.get(key, {}).get("stub") or out[key].get("months") is None:
                out[key] = {"stub": True, "months": months}
        elif key not in out:
            # plain header: only Jan/Feb ends are transition periods by
            # month alone; Mar/Jun/Sep/Dec ends stay unflagged
            out[key] = {"stub": month in (1, 2), "months": None}
    return out


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
    # "OPM %" normalizes to "opm": it is the margin ratio row, kept under
    # its own key instead of leaking through the fallback mapping.
    "opm": "opm_pct",
    # Annual P&L spells the item "Interest", quarterly spells it
    # "Finance Cost" — same accounting item, one canonical key.
    "interest": "finance_cost",
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
    # NBFC / lender templates spell the row "Borrowing" (singular)
    "borrowing": "borrowings",
    # NBFC / deposit-taking templates: customer deposits are a form of
    # borrowing (kept as its own item; included in liabilities_ex_equity)
    "deposits": "deposits",
    "other liabilities": "other_liabilities",
    # Screener's "Total Liabilities" row is the balance-sheet GRAND TOTAL
    # (== Total Assets: equity + every liability). Named after the provider
    # label so nobody mistakes it for a liabilities-only figure.
    "total liabilities": "total_equity_and_liabilities",
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
    "no of shares": "shares_outstanding",
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
    "shares_outstanding": "count",
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

    Derived rows (marked `derived: True` with `source_items`, never
    overwriting a scraped row):
      liabilities_ex_equity = the sum of the liability line items the
      template provides (borrowings + other_liabilities, plus deposits
      for NBFC/deposit-taking sheets). Screener's "Total Liabilities" is
      the grand total (equals Total Assets); Altman Z's X4 needs the
      ex-equity figure.

    Every statement row carries `stub` / `stub_months`. Stub-ness is a
    property of the (symbol, view, period_end) PERIOD and is resolved
    symbol-wide from the raw headers (the "NNm" suffix appears on the
    P&L header alone), then applied to EVERY section, so a 15-month
    period is flagged in balance_sheet / cash_flow / ratios too.
    Non-March, non-December period ends (transition periods between
    fiscal-year conventions) are conservatively flagged stub with an
    unknown duration and period_type "STUB".
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
    # Stub-ness is a property of the PERIOD, not of one section: the site
    # prints the "NNm" duration suffix only on the P&L header. Resolve it
    # once across ALL sections and apply it uniformly below.
    all_headers = [
        h
        for section in (record.get("sections") or {}).values()
        for h in (section.get("headers") or [])
    ]
    stub_by_ym = resolve_stub_periods(all_headers)

    def _period_stub(section_key: str,
                     period: dict[str, Any]) -> tuple[bool, int | None]:
        """Record-wide stub flags for one parsed period.

        quarterly_results / shareholding columns are quarters and
        ownership snapshots by nature — never stubs, even when their
        period_end collides with an annual stub period (Jun-FY companies:
        annual 'Jun 2022' + quarterly 'Jun 2022'). TTM columns have no
        duration of their own and never inherit a period's stub flag.
        """
        if (section_key in ("quarterly_results", "shareholding")
                or period.get("period_type")):  # TTM column
            return False, None
        if not period.get("period_end"):
            return bool(period.get("stub")), period.get("stub_months")
        res = stub_by_ym.get((int(period["period_end"][:4]),
                              int(period["period_end"][5:7])))
        if res is None:
            return bool(period.get("stub")), period.get("stub_months")
        return res["stub"], res["months"]

    # Record-wide latest quarter-end: fallback anchor for TTM columns in
    # sections that carry *only* a TTM header (rare; 3 of 4,541 companies).
    all_q_ends = [
        p["period_end"]
        for section in (record.get("sections") or {}).values()
        for p in _section_periods(section.get("headers") or [])
        if p and not p["period_type"] and p["period_end"] and not p.get("stub")
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
        # Stub (audited "NNm") columns are excluded: they are never the
        # latest annual figure of a section.
        q_ends = [p["period_end"] for p in periods
                  if p and not p["period_type"] and p["period_end"]
                  and not p.get("stub")]
        ttm_end = max(q_ends) if q_ends else record_ttm_end
        ttm_fy = None
        if ttm_end:
            y, m = int(ttm_end[:4]), int(ttm_end[5:7])
            ttm_fy = f"FY{y if m == 3 else y + 1}"
        rows_out: list[dict] = []
        for row in section.get("rows") or []:
            item = map_item_label(row["label"])
            values = row.get("values") or []
            for i, period in enumerate(periods):
                if period is None or i >= len(values):
                    continue
                # Unified period_type rule:
                #   TTM column            -> "TTM"
                #   stub with known months -> months == 12 ? "FY" : "STUB"
                #     (a "12m" suffix is just a labelled full year)
                #   stub without months    -> "STUB" (unknown duration)
                #   Mar / Dec year-end     -> "FY"
                #   anything else          -> "Q" (genuine quarter)
                stub, stub_months = _period_stub(key, period)
                if period["period_type"]:
                    ptype = "TTM"
                    period_end = period["period_end"] or ttm_end
                    fiscal_year = period["fiscal_year"] or ttm_fy
                elif stub:
                    ptype = "FY" if stub_months == 12 else "STUB"
                    period_end = period["period_end"]
                    fiscal_year = period["fiscal_year"]
                elif period["period_end"][5:7] in ("03", "12"):
                    ptype = "FY"
                    period_end = period["period_end"]
                    fiscal_year = period["fiscal_year"]
                else:
                    ptype = "Q"
                    period_end = period["period_end"]
                    fiscal_year = period["fiscal_year"]
                rows_out.append({
                    "symbol": record["symbol"],
                    "view": record.get("view"),
                    "section": key,
                    "item": item,
                    "period_end": period_end,
                    "fiscal_year": fiscal_year,
                    "period_type": ptype,
                    "value": values[i],
                    # audited/extended-period columns ("Mar 2015 15m") keep
                    # their non-12-month duration semantics: never silently
                    # comparable to a normal fiscal year. Flagging is
                    # record-wide, so every section of the period agrees.
                    "stub": stub,
                    "stub_months": stub_months,
                })

        # Derived: liabilities_ex_equity, per period (see docstring).
        # Composition adapts to the template: NBFC/lender sheets spell the
        # row "Borrowing" (-> borrowings) and deposit-takers add "Deposits"
        # (-> deposits); every provided liability line is summed.
        if key == "balance_sheet":
            liability_items = ("borrowings", "deposits", "other_liabilities")
            comps: dict[tuple, dict[str, Any]] = {}
            for r in rows_out:
                if r["item"] in liability_items and not r.get("derived"):
                    k = (r["period_type"], r["period_end"], r["fiscal_year"])
                    entry = comps.setdefault(k, {})
                    entry[r["item"]] = r["value"]
                    entry["_stub"] = (r["stub"], r["stub_months"])
            for k in sorted(comps, key=lambda k: k[1] or ""):
                pair = comps[k]
                b = pair.get("borrowings")
                d = pair.get("deposits")
                o = pair.get("other_liabilities")
                if b is None or o is None:
                    continue
                stub, stub_months = pair["_stub"]
                rows_out.append({
                    "symbol": record["symbol"],
                    "view": record.get("view"),
                    "section": key,
                    "item": "liabilities_ex_equity",
                    "period_end": k[1],
                    "fiscal_year": k[2],
                    "period_type": k[0],
                    "value": b + (d or 0.0) + o,
                    "derived": True,
                    "source_items": [i for i in liability_items
                                     if pair.get(i) is not None],
                    # keep the full TIDY_COLUMNS shape so the tidy CSV
                    # projection never KeyErrors on derived rows; the
                    # period's stub flags propagate to the derived row
                    "stub": stub,
                    "stub_months": stub_months,
                })
        statements.extend(rows_out)

    return {"meta": meta, "indicators": indicators, "statements": statements}


# --------------------------------------------------------------------------
# Export helpers (flat long table / wide pivot)
# --------------------------------------------------------------------------

TIDY_COLUMNS = [
    "symbol", "view", "section", "item", "period_end",
    "fiscal_year", "period_type", "value", "stub", "stub_months",
]


def statements_tidy_rows(canon: dict[str, Any]) -> list[list[Any]]:
    return [[s[c] for c in TIDY_COLUMNS] for s in canon["statements"]]


def indicator_rows(canon: dict[str, Any]) -> list[list[Any]]:
    return [[k, v.get("value"), v.get("unit")]
            for k, v in canon["indicators"].items()]
