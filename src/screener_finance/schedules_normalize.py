"""Canonical ingestion of schedule ("+")-row detail — Phase 8.

Turns the raw schedules archive (raw-first JSON payloads + provenance
sidecars, one file per expandable parent row) into tidy canonical rows,
fully offline.

Policy (COVERAGE_AUDIT.md "Phase 8 — canonical ingestion policy"):

- Provider labels are stored verbatim in the ``detail_label`` column; the
  canonical ``item`` key comes only from an exact-match taxonomy
  (``SCHEDULE_ITEM_MAP``). No string similarity, no normalization of the
  label itself: anything unmapped is passed through verbatim as the item.
- ``Working capital changes`` is an overlapping aggregate of its sibling
  working-capital rows (Receivables, Inventory, Payables, ...). It is
  never merged, summed, or dropped — both levels are kept and flagged
  via ``SCHEDULE_AGGREGATES`` so consumers know not to sum them.
- Display-derived rows (growth %, cost %, EPS/PE denominators) carry
  ``metric: False`` — they describe the presentation, not the company.
- Child rows of a parent are additive observations, keyed by
  ``(symbol, view, section, item, period_end, period_type)``; the parent
  summary rows live in the main statements panel.
"""

from __future__ import annotations

import json
import os
from typing import Any, Iterable

from .normalize import parse_period, resolve_stub_periods
from .parse import num

# --------------------------------------------------------------------------
# Exact-label taxonomy (verbatim provider label -> canonical item key)
# --------------------------------------------------------------------------

# (section, parent_item) -> {verbatim detail label: canonical item}
# Every label here was observed in the 4,540-symbol archive sweep
# (2026-09-30); 95 distinct (section, parent, label) triples. New labels
# appearing on the site pass through verbatim (never guessed).
SCHEDULE_ITEM_MAP: dict[tuple[str, str], dict[str, str]] = {
    ("balance_sheet", "borrowings"): {
        "Short term Borrowings": "borrowings_short_term",
        "Long term Borrowings": "borrowings_long_term",
        "Other Borrowings": "borrowings_other",
        "Lease Liabilities": "lease_liabilities",
        "Preference Capital": "preference_capital",
    },
    ("balance_sheet", "fixed_assets"): {
        "Gross Block": "gross_block",
        "Accumulated Depreciation": "accumulated_depreciation",
        # net-structured schedule: gross_block - accumulated_depreciation
        "Land": "fixed_assets_land",
        "Building": "fixed_assets_building",
        "Plant Machinery": "fixed_assets_plant_machinery",
        "Computers": "fixed_assets_computers",
        "Equipments": "fixed_assets_equipments",
        "Furniture n fittings": "fixed_assets_furniture_fittings",
        "Vehicles": "fixed_assets_vehicles",
        "Intangible Assets": "fixed_assets_intangibles",
        "Other fixed assets": "fixed_assets_other",
        "Railway sidings": "fixed_assets_railway_sidings",
        "Wind turbines": "fixed_assets_wind_turbines",
        "Ships Vessels": "fixed_assets_ships_vessels",
        "Estates": "fixed_assets_estates",
    },
    ("balance_sheet", "other_assets"): {
        "Cash Equivalents": "cash_equivalents",
        "Loans n Advances": "loans_and_advances",
        "Trade receivables": "trade_receivables",
        "Inventories": "inventories",
        "Receivables over 6m": "receivables_over_6m",
        "Other asset items": "other_assets_unclassified",
    },
    ("balance_sheet", "other_liabilities"): {
        "Trade Payables": "trade_payables",
        "Advance from Customers": "advances_from_customers",
        "Non controlling int": "non_controlling_interests",
        "Other liability items": "other_liabilities_unclassified",
    },
    ("cash_flow", "cash_from_operating_activity"): {
        "Profit from operations": "cfo_profit_from_operations",
        "Working capital changes": "cfo_working_capital_changes",
        "Receivables": "cfo_receivables",
        "Inventory": "cfo_inventory",
        "Payables": "cfo_payables",
        "Loans Advances": "cfo_loans_advances",
        "Operating Deposits": "cfo_operating_deposits",
        "Operating borrowings": "cfo_operating_borrowings",
        "Operating investments": "cfo_operating_investments",
        "Other WC items": "cfo_other_wc_items",
        "Direct taxes": "cfo_direct_taxes",
        "Advance tax": "cfo_advance_tax",
        "Interest paid": "cfo_interest_paid",
        "Exceptional CF items": "cfo_exceptional_items",
        "Leased assets": "cfo_leased_assets",
        "Bills purchased": "cfo_bills_purchased",
        "Stock on hire": "cfo_stock_on_hire",
        "Other operating items": "cfo_other_operating_items",
    },
    ("cash_flow", "cash_from_investing_activity"): {
        "Fixed assets purchased": "capex",  # authoritative capex (CFI outflow)
        "Fixed assets sold": "fixed_assets_sold",
        "Capital WIP": "cwip_acquired",
        "Investments purchased": "investments_purchased",
        "Investments sold": "investments_sold",
        "Interest received": "interest_received",
        "Dividends received": "dividends_received",
        "Investment income": "investment_income",
        "Acquisition of companies": "acquisition_of_companies",
        "Investment in group cos": "investment_in_group_companies",
        "Invest in subsidiaries": "investment_in_subsidiaries",
        "Loans to subsidiaries": "loans_to_subsidiaries",
        "Inter corporate deposits": "inter_corporate_deposits",
        "Redemp n Canc of Shares": "redemption_cancellation_of_shares",
        "Issue of shares on acq": "shares_issued_on_acquisition",
        "Subsidy received": "subsidy_received",
        "Other investing items": "cfi_other_items",
    },
    ("cash_flow", "cash_from_financing_activity"): {
        "Proceeds from borrowings": "borrowings_proceeds",
        "Repayment of borrowings": "borrowings_repayment",
        "Proceeds from debentures": "debentures_proceeds",
        "Redemption of debentures": "debentures_redemption",
        "Proceeds from deposits": "deposits_proceeds",
        "Proceeds from shares": "shares_proceeds",
        "Share application money": "share_application_money",
        "Application money refund": "application_money_refund",
        "Financial liabilities": "cff_financial_liabilities",
        "Interest paid fin": "cff_interest_paid",
        "Dividends paid": "dividends_paid",
        "Corporate loans": "corporate_loans",
        "Investment subsidy": "investment_subsidy",
        "Shelter assistance reserve": "shelter_assistance_reserve",
        "Other financing items": "cff_other_items",
    },
    # Annual P&L "+" schedules are display ratios, not rupee lines.
    ("profit_loss", "sales"): {
        "Sales Growth %": "sales_growth_pct",
    },
    ("profit_loss", "revenue"): {
        "Sales Growth %": "sales_growth_pct",
    },
    ("profit_loss", "expenses"): {
        "Material Cost %": "material_cost_pct",
        "Manufacturing Cost %": "manufacturing_cost_pct",
        "Employee Cost %": "employee_cost_pct",
        "Other Cost %": "other_cost_pct",
    },
    ("profit_loss", "net_profit"): {
        "Profit excl Excep": "profit_excl_exceptional",
        "Exceptional items AT": "exceptional_items_after_tax",
        "Minority share": "minority_share",
        "Profit from Associates": "profit_from_associates",
        "Profit Growth %": "profit_growth_pct",
        "Profit for EPS": "profit_for_eps",
        "Profit for PE": "profit_for_pe",
    },
    ("profit_loss", "other_income"): {
        "Other income normal": "other_income_normal",
        "Exceptional items": "exceptional_items",
    },
}

# Overlapping aggregates: never sum with their siblings. The parent summary
# row (in the main panel) already nets these components.
SCHEDULE_AGGREGATE_ITEMS: dict[str, str] = {
    "cfo_working_capital_changes":
        "Overlapping aggregate of the working-capital component rows "
        "(cfo_receivables, cfo_inventory, cfo_payables, ...); do NOT sum "
        "with siblings.",
}

# Display-derived rows: presentation artifacts, not company fundamentals.
SCHEDULE_NON_METRIC_ITEMS: frozenset[str] = frozenset({
    "sales_growth_pct", "profit_growth_pct",
    "material_cost_pct", "manufacturing_cost_pct",
    "employee_cost_pct", "other_cost_pct",
    "profit_for_eps", "profit_for_pe",
})

SCHEDULE_SECTIONS_CANONICAL = {
    "profit-loss": "profit_loss", "profit_loss": "profit_loss",
    "balance-sheet": "balance_sheet", "balance_sheet": "balance_sheet",
    "cash-flow": "cash_flow", "cash_flow": "cash_flow",
}


def map_schedule_item(section: str, parent_item: str, label: str) -> str:
    """Exact-match taxonomy lookup; unmapped labels pass through verbatim."""
    return SCHEDULE_ITEM_MAP.get((section, parent_item), {}).get(label, label)


# --------------------------------------------------------------------------
# Payload -> canonical rows
# --------------------------------------------------------------------------

SCHEDULE_COLUMNS = [
    "symbol", "view", "section", "item", "parent_item", "detail_label",
    "period_end", "fiscal_year", "period_type", "value",
    "metric", "aggregate", "stub", "stub_months",
]


def schedule_rows(
    symbol: str,
    view: str,
    section: str,
    parent_item: str,
    payload: dict[str, Any],
    stub_by_ym: dict[tuple[int, int], dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """One schedules payload -> tidy canonical rows.

    ``payload`` is the raw endpoint JSON: {verbatim detail label:
    {"Mar 2015": "1,23,456", ...}}. Metadata keys produced by the endpoint
    (setAttributes / isExpandable entries) are skipped here.

    ``stub_by_ym``: optional record-wide stub resolution (see
    ``normalize.resolve_stub_periods``). The schedules endpoint strips the
    "NNm" duration suffix from its column headers, so stub periods arrive
    here as plain "Mar 2023"; passing the symbol's resolved stub map flags
    them consistently with the statements panel.
    """
    key = (section, parent_item)
    item_map = SCHEDULE_ITEM_MAP.get(key, {})
    rows: list[dict[str, Any]] = []
    for label, series in payload.items():
        if not isinstance(series, dict):
            continue  # endpoint metadata, not a label series
        item = item_map.get(label, label)
        for header, raw_value in series.items():
            period = parse_period(header)
            if period is None:
                continue
            stub = bool(period.get("stub"))
            months = period.get("stub_months")
            if stub_by_ym and period["period_end"]:
                res = stub_by_ym.get((int(period["period_end"][:4]),
                                      int(period["period_end"][5:7])))
                if res is not None:
                    stub, months = res["stub"], res["months"]
            if period["period_type"]:
                ptype = "TTM"
            elif stub:
                ptype = "FY" if months == 12 else "STUB"
            else:
                ptype = "FY"
            rows.append({
                "symbol": symbol,
                "view": view,
                "section": section,
                "item": item,
                "parent_item": parent_item,
                "detail_label": label,
                "period_end": period["period_end"],
                "fiscal_year": period["fiscal_year"],
                "period_type": ptype,
                # canonical boundary: "1,23,456" / "6.13%" -> float
                "value": num(raw_value),
                "metric": item not in SCHEDULE_NON_METRIC_ITEMS,
                "aggregate": SCHEDULE_AGGREGATE_ITEMS.get(item),
                "stub": stub,
                "stub_months": months,
            })
    return rows


def ingest_archive(
    archive_dir: str,
    symbols: Iterable[str] | None = None,
    require_done_marker: bool = True,
    headers_by_view: dict[str, list[str]] | None = None,
) -> list[dict[str, Any]]:
    """Walk a raw-first schedules archive and return all canonical rows.

    Only symbols with a ``_backfill_done.json`` marker are ingested by
    default — partial archives must never leak into the canonical dataset.

    ``headers_by_view``: optional {view: [all company-page headers]} from
    the raw record, used to resolve stub periods symbol-wide (the schedule
    payloads themselves lack the "NNm" duration suffix).
    """
    marker = "_backfill_done.json"
    root = os.fspath(archive_dir)
    if symbols is None:
        symbols = sorted(d for d in os.listdir(root)
                         if os.path.isdir(os.path.join(root, d)))
    rows: list[dict[str, Any]] = []
    for sym in symbols:
        for view in sorted(os.listdir(os.path.join(root, sym))):
            vdir = os.path.join(root, sym, view)
            if not os.path.isdir(vdir):
                continue
            if require_done_marker and not os.path.exists(
                    os.path.join(vdir, marker)):
                continue
            stub_by_ym = None
            if headers_by_view and headers_by_view.get(view):
                stub_by_ym = resolve_stub_periods(headers_by_view[view])
            for fn in sorted(os.listdir(vdir)):
                if (not fn.endswith(".json") or fn.startswith("_")
                        or fn.endswith(".meta.json")):
                    continue
                stem = fn[:-5]
                if "__" not in stem:
                    continue
                dom_id, parent_label = stem.split("__", 1)
                section = SCHEDULE_SECTIONS_CANONICAL.get(dom_id)
                if section is None:
                    continue  # e.g. quarterly sections; annual-only policy
                with open(os.path.join(vdir, fn), encoding="utf-8") as fp:
                    payload = json.load(fp)
                rows.extend(schedule_rows(sym, view, section, parent_label,
                                          payload, stub_by_ym=stub_by_ym))
    return rows
