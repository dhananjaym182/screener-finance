"""Offline tests for the Phase 8 canonical schedules layer.

Run: python3 tests/test_schedules_normalize.py
Uses only committed fixtures — no network.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from screener_finance.normalize import resolve_stub_periods
from screener_finance.schedules_normalize import (  # noqa: E402
    SCHEDULE_AGGREGATE_ITEMS,
    SCHEDULE_COLUMNS,
    SCHEDULE_ITEM_MAP,
    SCHEDULE_NON_METRIC_ITEMS,
    ingest_archive,
    map_schedule_item,
    schedule_rows,
)

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "schedules")


def load_fixture(sym: str, dom_section: str, parent: str) -> dict:
    path = os.path.join(FIXTURES, f"{sym}__{dom_section}__{parent}.json")
    with open(path, encoding="utf-8") as fp:
        return json.load(fp)


def rel_2026() -> list[dict]:
    """RELIANCE borrowings schedule rows (real archived payload)."""
    payload = load_fixture("RELIANCE", "balance-sheet", "borrowings")
    return schedule_rows("RELIANCE", "consolidated", "balance_sheet",
                         "borrowings", payload)


def test_exact_label_taxonomy_matches_archive_sweep():
    # Locked against the 4,540-symbol archive sweep (2026-09-30):
    # 95 distinct (section, parent, label) triples across 12 parents.
    n_pairs = sum(len(v) for v in SCHEDULE_ITEM_MAP.values())
    assert n_pairs == 95, n_pairs
    n_parents = len(SCHEDULE_ITEM_MAP)
    assert n_parents == 12, n_parents


def test_rows_are_tidy_and_typed():
    rows = rel_2026()
    assert rows, "fixture produced no rows"
    for r in rows:
        assert set(r) == set(SCHEDULE_COLUMNS)
        assert r["period_type"] == "FY"
        assert r["period_end"].endswith("-03-31")
        assert r["fiscal_year"] == (
            f"FY{r['period_end'][:4]}")  # Mar end -> same FY
        assert r["parent_item"] == "borrowings"
        assert r["detail_label"]  # verbatim provider label kept
        assert isinstance(r["metric"], bool)
        assert isinstance(r["value"], float)


def test_mar_2026_borrowings_children_sum_to_parent():
    # Screener parent (Mar 2026): 402,962. Children must sum EXACTLY.
    rows = rel_2026()
    total = sum(r["value"] for r in rows
                if r["period_end"] == "2026-03-31")
    assert abs(total - 402_962.0) < 1.0, total


def test_debt_split_items_present():
    rows = rel_2026()
    items = {r["item"] for r in rows}
    assert {"borrowings_short_term", "borrowings_long_term",
            "borrowings_other", "lease_liabilities"} <= items


def test_display_pct_rows_are_non_metric():
    payload = load_fixture("RELIANCE", "profit-loss", "sales")
    rows = schedule_rows("RELIANCE", "consolidated", "profit_loss",
                         "sales", payload)
    assert rows
    for r in rows:
        assert r["item"] == "sales_growth_pct"
        assert r["metric"] is False
    # and metric rows elsewhere are True
    for r in rel_2026():
        assert r["metric"] is True


def test_working_capital_changes_flagged_as_aggregate():
    payload = load_fixture("RELIANCE", "cash-flow",
                           "cash_from_operating_activity")
    rows = schedule_rows("RELIANCE", "consolidated", "cash_flow",
                         "cash_from_operating_activity", payload)
    agg = [r for r in rows if r["item"] == "cfo_working_capital_changes"]
    comp = [r for r in rows if r["item"] == "cfo_receivables"]
    assert agg and comp
    assert all(r["aggregate"] == SCHEDULE_AGGREGATE_ITEMS[
        "cfo_working_capital_changes"] for r in agg)
    assert all(r["aggregate"] is None for r in comp)


def test_cfo_children_additivity_and_aggregate_flag():
    # Real RELIANCE fixture (FY2026): CFO parent = 192,113 (main panel).
    # Children WITHOUT the overlapping aggregate sum exactly to it; the
    # aggregate (Working capital changes = 12,497 = Rec + Inv + Pay) is a
    # sub-total that must never be added on top.
    payload = load_fixture("RELIANCE", "cash-flow",
                           "cash_from_operating_activity")
    rows = schedule_rows("RELIANCE", "consolidated", "cash_flow",
                         "cash_from_operating_activity", payload)
    r26 = [r for r in rows if r["period_end"] == "2026-03-31"]
    comps = sum(r["value"] for r in r26
                if r["item"] != "cfo_working_capital_changes")
    agg = [r["value"] for r in r26
           if r["item"] == "cfo_working_capital_changes"][0]
    assert agg == 12_497.0, agg
    assert abs(comps - 192_113.0) < 1.0, comps


def test_unmapped_labels_pass_through_verbatim():
    assert map_schedule_item("balance_sheet", "borrowings",
                             "Never Seen Before") == "Never Seen Before"
    # exact match required: no similarity fallback
    assert map_schedule_item("balance_sheet", "borrowings",
                             "Short term Borrowing") == "Short term Borrowing"


def test_capex_is_metric_and_authoritative():
    payload = load_fixture("RELIANCE", "cash-flow",
                           "cash_from_investing_activity")
    rows = schedule_rows("RELIANCE", "consolidated", "cash_flow",
                         "cash_from_investing_activity", payload)
    capex = [r for r in rows if r["item"] == "capex"]
    assert capex, "capex (Fixed assets purchased) missing"
    assert all(r["metric"] is True for r in capex)
    assert all(r["aggregate"] is None for r in capex)


def test_endpoint_metadata_rows_are_skipped():
    rows = schedule_rows("X", "consolidated", "balance_sheet", "borrowings",
                         {"setAttributes": {"foo": 1}, "isExpandable": True,
                          "Short term Borrowings": {"Mar 2026": "1,000"}})
    assert len(rows) == 1
    assert rows[0]["value"] == 1000.0


def test_stub_period_propagates_from_company_headers():
    """The schedules endpoint strips the 'NNm' suffix (ACC: P&L header
    'Mar 2023 15m' arrives as plain 'Mar 2023'). Passing the record-wide
    stub resolution must flag the same periods as the statements panel
    (Defect B: 2,169,413 rows all stub=False)."""
    payload = {"Short term Borrowings": {"Mar 2022": "500",
                                         "Mar 2023": "600",
                                         "Mar 2024": "700"}}
    headers = ["Mar 2022", "Mar 2023 15m", "Mar 2024"]
    stub_by_ym = resolve_stub_periods(headers)

    # without stub map: legacy behaviour, nothing flagged
    plain = schedule_rows("ACC", "consolidated", "balance_sheet",
                          "borrowings", payload)
    assert all(r["stub"] is False and r["period_type"] == "FY"
               for r in plain)

    # with stub map: the 15-month period is flagged everywhere
    rows = schedule_rows("ACC", "consolidated", "balance_sheet",
                         "borrowings", payload, stub_by_ym=stub_by_ym)
    by_end = {r["period_end"]: r for r in rows}
    assert by_end["2023-03-31"]["stub"] is True
    assert by_end["2023-03-31"]["stub_months"] == 15
    assert by_end["2023-03-31"]["period_type"] == "STUB"
    assert by_end["2022-03-31"]["stub"] is False
    assert by_end["2022-03-31"]["period_type"] == "FY"
    assert by_end["2024-03-31"]["stub"] is False


def test_ingest_archive_resolves_stub_via_headers():
    with tempfile.TemporaryDirectory() as tmp:
        vdir = os.path.join(tmp, "ACC", "consolidated")
        os.makedirs(vdir)
        with open(os.path.join(vdir, "balance-sheet__borrowings.json"),
                  "w", encoding="utf-8") as fp:
            json.dump({"Short term Borrowings": {"Mar 2023": "600"}}, fp)
        with open(os.path.join(vdir, "_backfill_done.json"),
                  "w", encoding="utf-8") as fp:
            json.dump({"symbol": "ACC"}, fp)
        rows = ingest_archive(tmp, headers_by_view={
            "consolidated": ["Mar 2022", "Mar 2023 15m", "Mar 2024"]})
        assert len(rows) == 1
        assert rows[0]["stub"] is True and rows[0]["stub_months"] == 15
        assert rows[0]["period_type"] == "STUB"


def test_schedule_jan_feb_period_is_stub_not_quarter():
    """ONEINDIG transition period: schedule payload header 'Jan 2026'
    without the stub map parses to period_type FY (legacy); with the
    record-wide map it must be stub/STUB and fiscal_year FY2026."""
    payload = {"Direct taxes": {"Jan 2026": "42"}}
    rows = schedule_rows("ONEINDIG", "consolidated", "cash_flow",
                         "cash_from_operating_activity", payload,
                         stub_by_ym=resolve_stub_periods(
                             ["Mar 2025", "Jan 2026 10m", "Mar 2026"]))
    assert rows[0]["stub"] is True
    assert rows[0]["period_type"] == "STUB"
    assert rows[0]["fiscal_year"] == "FY2026"


def test_ingest_archive_requires_done_marker():
    with tempfile.TemporaryDirectory() as tmp:
        vdir = os.path.join(tmp, "RELIANCE", "consolidated")
        os.makedirs(vdir)
        with open(os.path.join(vdir, "balance-sheet__borrowings.json"),
                  "w", encoding="utf-8") as fp:
            json.dump({"Long term Borrowings": {"Mar 2026": "1,000"}}, fp)
        # no marker -> nothing ingested
        assert ingest_archive(tmp) == []
        # with marker -> rows appear
        with open(os.path.join(vdir, "_backfill_done.json"),
                  "w", encoding="utf-8") as fp:
            json.dump({"symbol": "RELIANCE"}, fp)
        rows = ingest_archive(tmp)
        assert len(rows) == 1
        assert rows[0]["symbol"] == "RELIANCE"
        assert rows[0]["view"] == "consolidated"
        assert rows[0]["section"] == "balance_sheet"
        assert rows[0]["item"] == "borrowings_long_term"
        assert rows[0]["value"] == 1000.0


def test_ingest_archive_skips_meta_and_markers():
    with tempfile.TemporaryDirectory() as tmp:
        vdir = os.path.join(tmp, "X", "consolidated")
        os.makedirs(vdir)
        open(os.path.join(vdir, "_backfill_done.json"), "w").close()
        for fn in ("balance-sheet__borrowings.meta.json",
                   "balance-sheet__borrowings.json"):
            with open(os.path.join(vdir, fn), "w", encoding="utf-8") as fp:
                fp.write('{"Lease Liabilities": {"Mar 2026": "500"}}')
        rows = ingest_archive(tmp)
        assert len(rows) == 1  # meta sidecar not double-counted


def test_non_metric_inventory():
    for item in ("sales_growth_pct", "material_cost_pct",
                 "profit_for_pe"):
        assert item in SCHEDULE_NON_METRIC_ITEMS
    for item in ("capex", "trade_payables", "cash_equivalents"):
        assert item not in SCHEDULE_NON_METRIC_ITEMS


def main() -> None:
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"  ok {fn.__name__}")
    print(f"{len(fns)} tests passed")


if __name__ == "__main__":
    main()
