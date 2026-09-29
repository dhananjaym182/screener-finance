"""PHASE 4/5 — live verification of the schedules fix on the audit sample.

Checks, per company:
  1. summary unchanged: sections parsed from the PRE-FIX archived page are
     identical to sections from a FRESH live fetch (the fix must not touch
     summary parsing).
  2. detail recovered: fetch_schedules() returns non-empty detail for the
     annual "+" rows and the archive lands raw-first.
  3. idempotent: two consecutive schedule runs produce identical parsed
     detail (raw-first overwrite is byte-stable).
  4. raw archive present with .meta.json provenance sidecars.
  5. field availability (PHASE 5): which Piotroski / Altman-critical fields
     the schedules actually provide for that company.

Writes audit_out/phase4_verification.json + phase4_summary.md. No canonical
data is touched anywhere.
"""
from __future__ import annotations

import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from bs4 import BeautifulSoup  # noqa: E402

from screener_finance.parse import parse_company  # noqa: E402
from screener_finance.schedules import (  # noqa: E402
    fetch_all_schedules,
    slug,
)
from screener_finance.session import get_session  # noqa: E402

AUDIT_DIR = os.path.join(os.path.dirname(__file__), "..", "audit_out")
ARCHIVE = os.path.join(AUDIT_DIR, "phase4_after")

SYMBOLS = json.load(open(os.path.join(AUDIT_DIR, "audit_raw.json"),
                         encoding="utf-8"))
SYMBOLS = [r["symbol"] for r in SYMBOLS if not r.get("error")]

# detail-label -> classification against our calculation needs (PHASE 5).
# Exact-label matching only — no label-similarity guessing (PHASE 8 rule).
FIELD_CLASSIFICATION = {
    "inventory": "inventories",
    "trade receivables": "trade_receivables",
    "receivables": "receivables_wc_change",
    "cash equivalents": "cash_equivalents",
    "fixed assets purchased": "capex_fixed_assets_purchased",
    "capital wip": "capex_cwip_movement",
    "long term borrowings": "debt_long_term",
    "short term borrowings": "debt_short_term",
    "lease liabilities": "debt_lease",
    "other borrowings": "debt_other",
    "trade payables": "trade_payables",
    "loans n advances": "loans_and_advances",
    "advance from customers": "advance_from_customers",
    "operating deposits": "operating_deposits",
    "interest paid": "interest_paid_cf",
    "direct taxes": "direct_taxes_cf",
}


def _sections_snapshot(record: dict) -> str:
    blob = json.dumps(record["sections"], sort_keys=True, ensure_ascii=False)
    import hashlib
    return hashlib.sha256(blob.encode()).hexdigest()


def verify_symbol(sess, sym: str) -> dict:
    out: dict = {"symbol": sym}
    pre_page_path = os.path.join(AUDIT_DIR, "payload_archive",
                                 f"{sym}__page.html")
    pre_html = open(pre_page_path, encoding="utf-8").read()
    pre = parse_company(sym, "consolidated",
                        BeautifulSoup(pre_html, "lxml"),
                        f"https://www.screener.in/company/{sym}/consolidated/")

    # 1. summary unchanged: pre-fix archived page vs FRESH live page
    path = f"/company/{sym}/consolidated/"
    soup = sess.get_soup(path, use_cache=False)
    post = parse_company(sym, "consolidated", soup, f"https://www.screener.in{path}")
    out["summary_unchanged"] = (_sections_snapshot(pre) == _sections_snapshot(post))
    out["summary_unchanged_note"] = (
        "sections hash equal (top_ratios/prices excluded — they tick daily)"
        if out["summary_unchanged"] else
        "sections DIFFER — fundamentals changed between audit and now")

    # 2. detail recovered via the fixed scraper, raw-first archive
    res = fetch_all_schedules(post, archive_dir=ARCHIVE,
                              sections=("profit_loss", "balance_sheet",
                                        "cash_flow"))
    annual_parents = sum(1 for sec in ("profit_loss", "balance_sheet", "cash_flow")
                         for r in pre["sections"][sec]["rows"]
                         if r.get("expandable"))
    detail_parents = sum(len(v) for v in res.values())
    out["annual_plus_rows"] = annual_parents
    out["detail_parents_recovered"] = detail_parents
    out["detail_recovered"] = detail_parents == annual_parents

    # detail rows now available that the summary scrape never had
    new_labels = set()
    for sec, parents in res.items():
        prow_labels = {r["label"] for r in post["sections"][sec]["rows"]}
        for parent, payload in parents.items():
            new_labels.update(l for l in payload["rows"] if l not in prow_labels)
    out["new_detail_labels"] = sorted(new_labels)

    # 3. idempotency: run again, parsed detail must be identical
    # (retrieved_at is retrieval provenance, not data — excluded)
    def _strip_ts(r):
        import copy
        r2 = copy.deepcopy(r)
        for parents in r2.values():
            for payload in parents.values():
                payload.pop("retrieved_at", None)
        return r2

    res2 = fetch_all_schedules(post, archive_dir=ARCHIVE,
                               sections=("profit_loss", "balance_sheet",
                                         "cash_flow"))
    blob1 = json.dumps(_strip_ts(res), sort_keys=True, ensure_ascii=False)
    blob2 = json.dumps(_strip_ts(res2), sort_keys=True, ensure_ascii=False)
    out["idempotent"] = blob1 == blob2

    # 4. raw archive present with provenance sidecars
    sym_dir = os.path.join(ARCHIVE, sym, "consolidated")
    n_json = len([f for f in os.listdir(sym_dir) if f.endswith(".json")
                  and not f.endswith(".meta.json")])
    n_meta = len([f for f in os.listdir(sym_dir) if f.endswith(".meta.json")])
    out["archive_files"] = n_json
    out["archive_meta_files"] = n_meta
    out["raw_archive_ok"] = n_json >= annual_parents and n_meta == n_json

    # 5. field availability vs classification (exact labels only)
    have = {}
    for sec, parents in res.items():
        for parent, payload in parents.items():
            for label in payload["rows"]:
                key = FIELD_CLASSIFICATION.get(label.strip().lower())
                if key:
                    have.setdefault(key, {"from_parent": parent,
                                          "section": sec,
                                          "periods": len(
                                              payload["rows"][label]["values"])})
    out["fields_available"] = have
    out["fields_missing"] = sorted(set(FIELD_CLASSIFICATION.values())
                                   - set(have))
    return out


def main() -> None:
    sess = get_session()
    sess.configure(delay=1.2, log_level="WARNING")
    os.makedirs(AUDIT_DIR, exist_ok=True)

    results = []
    for i, sym in enumerate(SYMBOLS, 1):
        print(f"[{i}/{len(SYMBOLS)}] {sym} ...", flush=True)
        try:
            results.append(verify_symbol(sess, sym))
        except Exception as exc:  # noqa: BLE001
            import traceback
            traceback.print_exc()
            results.append({"symbol": sym, "error": str(exc)})

    with open(os.path.join(AUDIT_DIR, "phase4_verification.json"), "w",
              encoding="utf-8") as fp:
        json.dump(results, fp, indent=1, ensure_ascii=False)

    lines = [
        "| Symbol | summary unchanged | detail recovered | idempotent "
        "| raw archive | inventory | receivables | capex | cash | debt split |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        f = r.get("fields_available", {})
        def y(v): return "yes" if v else "-"
        lines.append(
            f"| {r['symbol']} | {y(r.get('summary_unchanged'))} "
            f"| {r.get('detail_parents_recovered', '-')}/{r.get('annual_plus_rows', '-')} "
            f"| {y(r.get('idempotent'))} | {y(r.get('raw_archive_ok'))} "
            f"| {y('inventories' in f)} | {y('trade_receivables' in f or 'receivables_wc_change' in f)} "
            f"| {y('capex_fixed_assets_purchased' in f or 'capex_cwip_movement' in f)} "
            f"| {y('cash_equivalents' in f)} "
            f"| {y('debt_long_term' in f or 'debt_short_term' in f)} |")
    with open(os.path.join(AUDIT_DIR, "phase4_summary.md"), "w",
              encoding="utf-8") as fp:
        fp.write("\n".join(lines) + "\n")
    print("\n".join(lines))
    print(f"\nstats: {sess.stats}")
    print(f"artifacts: {AUDIT_DIR}")


if __name__ == "__main__":
    main()
