"""Offline tests for the schedules ("+" row) layer — real captured payloads.

Fixtures under tests/fixtures/schedules/ are raw bodies archived verbatim
from Screener's schedules endpoint during the 13-company live audit
(2026-09): RELIANCE (data-company-id=2726) plus SBIN / TCS / BAJFINANCE
samples. No network in this file.
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from bs4 import BeautifulSoup

from screener_finance.exceptions import ScreenerError
from screener_finance.normalize import parse_period
from screener_finance.parse import parse_company
from screener_finance.schedules import (
    fetch_all_schedules,
    fetch_schedule,
    parse_schedule_json,
    schedule_url,
)

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "schedules")


def _fixture(name: str) -> str:
    with open(os.path.join(FIX, name), encoding="utf-8") as fp:
        return fp.read()


def _reliance_record() -> dict:
    return parse_company("RELIANCE", "consolidated",
                         BeautifulSoup(_fixture("RELIANCE__page.html"), "lxml"),
                         "https://www.screener.in/company/RELIANCE/consolidated/")


def _latest_fy(series: dict) -> tuple:
    fy = [(k, v) for k, v in series.items()
          if parse_period(k) and not parse_period(k)["period_type"]
          and not parse_period(k).get("stub")]
    fy.sort(key=lambda kv: parse_period(kv[0])["period_end"])
    return fy[-1] if fy else (None, None)


# ---- payload parsing --------------------------------------------------------

def test_parse_schedule_json_borrowings():
    rows = parse_schedule_json(_fixture("RELIANCE__balance-sheet__borrowings.json"))
    assert set(rows.keys()) == {"Long term Borrowings", "Short term Borrowings",
                                "Lease Liabilities", "Other Borrowings"}
    lt = rows["Long term Borrowings"]["values"]
    assert lt["Mar 2015"] == 128165.0
    assert lt["Mar 2026"] == 270751.0
    assert rows["Lease Liabilities"]["values"]["Mar 2015"] == 0.0
    # display metadata must never leak into values
    assert "setAttributes" not in lt and "isExpandable" not in lt


def test_parse_schedule_json_display_derived_and_expandable():
    # annual Expenses schedule: cost-ratio display rows, one child expandable
    rows = parse_schedule_json(_fixture("RELIANCE__profit-loss__expenses.json"))
    assert set(rows.keys()) == {"Material Cost %", "Manufacturing Cost %",
                                "Employee Cost %", "Other Cost %"}
    assert any(r.get("is_expandable") for r in rows.values())
    # quarterly variant carries only the two ratios Screener displays there
    rows = parse_schedule_json(_fixture("RELIANCE__quarters__expenses.json"))
    assert set(rows.keys()) == {"Material Cost %", "Employee Cost %"}
    # TCS Other Assets schedule also carries isExpandable
    rows = parse_schedule_json(_fixture("TCS__balance-sheet__other_assets.json"))
    assert any(r.get("is_expandable") for r in rows.values())


def test_parse_schedule_json_rejects_malformed():
    for bad in ("[]", "not json", '"str"'):
        try:
            parse_schedule_json(bad)
        except ScreenerError:
            pass
        else:
            raise AssertionError(f"expected ScreenerError for {bad!r}")


# ---- page-level capture ------------------------------------------------------

def test_expandable_flag_and_company_id_parsed():
    rec = _reliance_record()
    assert rec["company_id"] == "2726"
    assert rec["warehouse_id"] == "6598251"  # different id spaces
    bs = rec["sections"]["balance_sheet"]["rows"]
    by_label = {r["label"]: r for r in bs}
    assert by_label["Borrowings"]["expandable"] is True
    assert by_label["Equity Capital"]["expandable"] is False
    expandable = [r["label"] for sec in rec["sections"].values()
                  for r in sec["rows"] if r.get("expandable")]
    # 20 rows flagged exactly as the page shows them: 4 quarterly +
    # 4 P&L + 4 BS + 3 CF + 5 shareholding. fetch_all_schedules() only
    # calls the endpoint for the annual sections (11 by default).
    assert len(expandable) == 20, expandable
    sh = [r["label"] for r in rec["sections"]["shareholding"]["rows"]
          if r.get("expandable")]
    assert sh == ["Promoters", "FIIs", "DIIs", "Government", "Public"]


def test_schedule_url_shape():
    # standalone (default) OMITS the param: an empty consolidated= value is
    # treated as consolidated server-side and yields {} for standalone-only
    # filers (verified live, 2026-09-30)
    url = schedule_url("2726", "Borrowings", "balance-sheet")
    assert url == ("https://www.screener.in/api/company/2726/schedules/"
                   "?parent=Borrowings&section=balance-sheet")
    url = schedule_url("2726", "Borrowings", "balance-sheet", "1")
    assert url.endswith("consolidated=1")
    url = schedule_url("2726", "Borrowings", "balance-sheet", None)
    assert "consolidated" not in url


# ---- semantics: parent == sum(children) where structurally expected ----------

def test_balance_sheet_children_sum_to_parent():
    """Verified live: BS schedules are additive. Locked on RELIANCE Mar 2026."""
    rec = _reliance_record()
    for parent, fixture in [
        ("Borrowings", "RELIANCE__balance-sheet__borrowings.json"),
        ("Other Liabilities", "RELIANCE__balance-sheet__other_liabilities.json"),
        ("Other Assets", "RELIANCE__balance-sheet__other_assets.json"),
    ]:
        rows = parse_schedule_json(_fixture(fixture))
        prow = next(r for r in rec["sections"]["balance_sheet"]["rows"]
                    if r["label"] == parent)
        pmap = dict(zip(rec["sections"]["balance_sheet"]["headers"],
                        prow["values"]))
        pk, pv = _latest_fy(pmap)
        periods = set().union(*[r["values"].keys() for r in rows.values()])
        ck, cv = _latest_fy({p: sum((r["values"].get(p) or 0)
                                    for r in rows.values()) for p in periods})
        assert pk == ck
        assert abs(pv - cv) < 0.5, (parent, pv, cv)


def test_cash_flow_children_sum_excluding_aggregate():
    """CFO schedule: Profit from operations + WC rows + Direct taxes == CFO,
    but 'Working capital changes' is an AGGREGATE overlapping the WC rows —
    it must be excluded from sums (semantic note for canonical mapping)."""
    rec = _reliance_record()
    rows = parse_schedule_json(
        _fixture("RELIANCE__cash-flow__cash_from_operating_activity.json"))
    agg = rows.pop("Working capital changes")["values"]
    periods = set().union(*[r["values"].keys() for r in rows.values()])
    ck, cv = _latest_fy({p: sum((r["values"].get(p) or 0)
                                for r in rows.values()) for p in periods})
    prow = next(r for r in rec["sections"]["cash_flow"]["rows"]
                if r["label"] == "Cash from Operating Activity")
    pmap = dict(zip(rec["sections"]["cash_flow"]["headers"], prow["values"]))
    pk, pv = _latest_fy(pmap)
    assert pk == ck and abs(pv - cv) < 0.5, (pv, cv)
    # the aggregate itself equals the WC rows' sum (overlap, not extra flow)
    wc_rows = {p: (rows["Receivables"]["values"].get(p) or 0)
                  + (rows["Inventory"]["values"].get(p) or 0)
                  + (rows["Payables"]["values"].get(p) or 0)
               for p in periods}
    ak, av = _latest_fy(wc_rows)
    assert ak == _latest_fy(agg)[0] and abs(av - agg[ak]) < 0.5


def test_quarterly_schedules_are_display_derived():
    rows = parse_schedule_json(_fixture("RELIANCE__quarters__sales.json"))
    assert list(rows.keys()) == ["YOY Sales Growth %"]


# ---- fetch + raw archive + provenance ----------------------------------------

class FakeSess:
    """Session double: records schedule GETs, serves a canned body,
    and a get_soup() backed by the archived page for Ticker tests."""

    def __init__(self, body: str, page_html: str | None = None):
        self.body = body
        self.page_html = page_html
        self.calls: list[str] = []
        self.timeout = 5
        from types import SimpleNamespace
        self.throttle = SimpleNamespace(wait=lambda: None)
        self.stats = {"requests": 0}

    def get_soup(self, path, use_cache=True):
        return BeautifulSoup(self.page_html or "<html></html>", "html.parser")

    def get_text(self, path, use_cache=True):
        return "{}"

    def _ensure(self):
        import types
        sess = self

        class R:
            status_code = 200
            text = sess.body

            def raise_for_status(self):
                pass

        s = types.SimpleNamespace()
        s.get = lambda url, headers=None, timeout=None: (
            sess.calls.append(url), R())[1]
        return s


def test_fetch_schedule_archives_raw_and_meta():
    body = _fixture("RELIANCE__balance-sheet__borrowings.json")
    rec = _reliance_record()
    sess = FakeSess(body)
    import screener_finance.session as sess_mod
    orig = sess_mod._session
    sess_mod._session = sess
    try:
        with tempfile.TemporaryDirectory() as tmp:
            res = fetch_schedule(rec, "Borrowings", "balance_sheet",
                                 archive_dir=tmp)
            assert res is not None and "Long term Borrowings" in res["rows"]
            assert res["section"] == "balance_sheet"
            assert res["endpoint"].startswith("/api/company/2726/schedules/")
            # raw body archived VERBATIM before parsing
            raw = os.path.join(tmp, "RELIANCE", "consolidated",
                               "balance_sheet__borrowings.json")
            with open(raw, encoding="utf-8") as fp:
                assert fp.read() == body
            meta = json.load(open(raw + ".meta.json", encoding="utf-8"))
            for key in ("provider", "company", "company_id", "view", "section",
                        "parent_label", "url", "status_code", "retrieved_at",
                        "referer"):
                assert key in meta, key
            assert meta["provider"] == "screener.in"
            assert meta["company_id"] == "2726"
            assert meta["parent_label"] == "Borrowings"
            assert sess.stats["requests"] == 1
    finally:
        sess_mod._session = orig


def test_fetch_schedule_without_company_id_returns_none():
    rec = {"symbol": "X", "view": "consolidated", "company_id": None,
           "sections": {}}
    assert fetch_schedule(rec, "Borrowings", "balance_sheet") is None


def test_skip_existing_never_refetches_or_overwrites():
    """Backfill rule: with skip_existing=True, an archived payload is served
    from disk — zero network calls, existing observations never overwritten."""
    body = _fixture("RELIANCE__balance-sheet__borrowings.json")
    rec = _reliance_record()
    sess = FakeSess("{\"SHOULD NOT BE USED\": {\"Mar 2026\": \"1\"}}")
    import screener_finance.session as sess_mod
    orig = sess_mod._session
    sess_mod._session = sess
    try:
        with tempfile.TemporaryDirectory() as tmp:
            # pre-seed the archive (as a previous backfill run would have)
            raw = os.path.join(tmp, "RELIANCE", "consolidated",
                               "balance_sheet__borrowings.json")
            os.makedirs(os.path.dirname(raw), exist_ok=True)
            with open(raw, "w", encoding="utf-8") as fp:
                fp.write(body)
            # skip_existing: served from archive — the fake's poisoned body
            # must never be fetched, parsed, or stored
            r2 = fetch_schedule(rec, "Borrowings", "balance_sheet",
                                archive_dir=tmp, skip_existing=True)
            assert r2["from_archive"] is True
            assert "Long term Borrowings" in r2["rows"]
            assert open(raw, encoding="utf-8").read() == body
            assert len(sess.calls) == 0  # zero network calls
    finally:
        sess_mod._session = orig


def test_fetch_all_schedules_uses_expandable_rows_and_cache():
    body = _fixture("RELIANCE__balance-sheet__borrowings.json")
    rec = _reliance_record()
    sess = FakeSess(body)
    import screener_finance.session as sess_mod
    orig = sess_mod._session
    sess_mod._session = sess
    try:
        out = fetch_all_schedules(rec)
        # module default covers quarterly_results too: 4 Q + 4 P&L + 4 BS + 3 CF
        n_calls = len(sess.calls)
        assert n_calls == 15, n_calls
        assert set(out.keys()) == {"profit_loss", "balance_sheet", "cash_flow",
                                   "quarterly_results"}
        # Ticker.fetch_schedules() defaults to the annual sections only
        import inspect
        from screener_finance.schedules import SCHEDULE_SECTIONS
        assert "quarterly_results" in SCHEDULE_SECTIONS
        # parents override -> fewer calls
        sess.calls.clear()
        out = fetch_all_schedules(rec, parents={"balance_sheet": ["Borrowings"]})
        assert len(sess.calls) == 1
        assert list(out["balance_sheet"].keys()) == ["Borrowings"]
    finally:
        sess_mod._session = orig


def test_ticker_fetch_schedules_cached():
    import screener_finance.session as sess_mod
    from screener_finance.ticker import Ticker

    sess = FakeSess(_fixture("RELIANCE__balance-sheet__borrowings.json"))
    orig = sess_mod._session
    sess_mod._session = sess
    try:
        t = Ticker("RELIANCE")
        sess.page_html = _fixture("RELIANCE__page.html")
        t.fetch()
        page_calls = len(sess.calls)
        out = t.fetch_schedules()
        first = len(sess.calls)
        assert first > page_calls  # one AJAX call per expandable row
        _ = t.schedules            # cached — no new calls
        assert len(sess.calls) == first
        assert "balance_sheet" in out
    finally:
        sess_mod._session = orig


if __name__ == "__main__":
    test_parse_schedule_json_borrowings()
    test_parse_schedule_json_display_derived_and_expandable()
    test_parse_schedule_json_rejects_malformed()
    test_expandable_flag_and_company_id_parsed()
    test_schedule_url_shape()
    test_balance_sheet_children_sum_to_parent()
    test_cash_flow_children_sum_excluding_aggregate()
    test_quarterly_schedules_are_display_derived()
    test_fetch_schedule_archives_raw_and_meta()
    test_fetch_schedule_without_company_id_returns_none()
    test_skip_existing_never_refetches_or_overwrites()
    test_fetch_all_schedules_uses_expandable_rows_and_cache()
    test_ticker_fetch_schedules_cached()
    print("ALL SCHEDULES TESTS PASSED")
