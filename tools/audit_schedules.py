"""PHASE 1 — live audit of Screener's schedule ("+" row) endpoint.

Read-only with respect to any canonical/production data. Outputs:
  tests/fixtures/schedules/<SYM>__<section>__<parent>.html|json  (raw payloads)
  tests/fixtures/schedules/<SYM>__page.html                      (source pages)
  audit_out/audit_raw.json                                       (machine audit)
  audit_out/audit_matrix.md                                      (human matrix)

Usage:
  python3 tools/audit_schedules.py --smoke                 # RELIANCE, 2 parents
  python3 tools/audit_schedules.py                         # full 12-company run
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from urllib.parse import quote

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from bs4 import BeautifulSoup  # noqa: E402

from screener_finance.normalize import canonical, map_item_label  # noqa: E402
from screener_finance.parse import (  # noqa: E402
    SECTION_IDS,
    cell_text,
    num,
    parse_warehouse_id,
)
from screener_finance.session import BASE_URL, get_session  # noqa: E402

FIXTURE_DIR = os.path.join(os.path.dirname(__file__), "..", "tests",
                           "fixtures", "schedules")
AUDIT_DIR = os.path.join(os.path.dirname(__file__), "..", "audit_out")

DEFAULT_SYMBOLS = [
    "RELIANCE", "TCS", "HDFCBANK", "BAJFINANCE", "HDFCLIFE",
    "TATAMOTORS",      # large-cap manufacturing
    "CUMMINSIND",      # mid-cap
    "AARTIIND",        # small/mid-cap chemicals
    "ITC",             # expected: Other Assets +
    "SBIN",            # bank BS shape
    "TATASTEEL",       # expected: Fixed Assets +
    "INFY",
]


def slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_").lower()


def plus_rows(soup: BeautifulSoup) -> list[dict]:
    """Every tbody row whose label cell ends with '+', per section."""
    out: list[dict] = []
    for key, dom_id in SECTION_IDS.items():
        section = soup.select_one(f"section#{dom_id}")
        if not section:
            continue
        table = section.select_one("table.data-table")
        if not table:
            continue
        headers = [cell_text(th) for th in table.select("thead th")[1:]]
        for tr in table.select("tbody tr"):
            cells = tr.select("td")
            if not cells:
                continue
            label = cell_text(cells[0])
            if not label.rstrip().endswith("+"):
                continue
            vals = [cell_text(td) for td in cells[1:]]
            latest = next((v for v in reversed(vals) if num(v) is not None), None)
            out.append({
                "section_key": key,
                "section_dom_id": dom_id,
                "parent_label": label.strip(),
                "parent_value_latest": latest,
                "headers": headers,
            })
    return out


def company_ids(soup: BeautifulSoup) -> list[str]:
    seen: list[str] = []
    for el in soup.select("[data-company-id]"):
        cid = el["data-company-id"]
        if cid and cid not in seen:
            seen.append(cid)
    return seen


def call_schedule(sess, cid: str, parent: str, section: str,
                  consolidated: str, referer: str):
    """One schedule attempt. Returns (status, text)."""
    url = (f"{BASE_URL}/api/company/{cid}/schedules/"
           f"?parent={quote(parent)}&section={quote(section)}"
           f"&consolidated={consolidated}")
    s = sess._ensure()
    sess.throttle.wait()
    t0 = time.monotonic()
    try:
        resp = s.get(url, headers={
            "Referer": referer,
            "X-Requested-With": "XMLHttpRequest",
            "Accept": "text/html, */*; q=0.01",
        }, timeout=sess.timeout)
    except Exception as exc:  # noqa: BLE001 — audit must survive failures
        return 0, f"__EXC__ {type(exc).__name__}: {exc} ({time.monotonic()-t0:.1f}s)"
    sess.stats["requests"] += 1
    return resp.status_code, resp.text


def parse_schedule_payload(text: str) -> dict:
    """Schedule responses are JSON: {detail_label: {"Mar 2015": "128,165", ...}}.
    HTML fragment fallback kept for safety."""
    s = text.lstrip()
    if s.startswith("{"):
        try:
            data = json.loads(text)
        except ValueError:
            return {"payload_kind": "json-invalid", "bytes": len(text)}
        if isinstance(data, dict):
            rows: list[dict] = []
            periods: set[str] = set()
            for label, series in data.items():
                if isinstance(series, dict):
                    periods.update(series.keys())
                    rows.append({"label": label,
                                 "values": {k: num(v) for k, v in series.items()}})
            return {"payload_kind": "json", "bytes": len(text),
                    "detail_labels": [r["label"] for r in rows],
                    "periods": sorted(periods), "rows": rows,
                    "row_count": len(rows)}
        return {"payload_kind": "json-other", "bytes": len(text),
                "preview": str(data)[:200]}
    kind = "html" if s.startswith(("<", "<!doctype")) else "other"
    info: dict = {"payload_kind": kind, "bytes": len(text)}
    if kind == "html":
        frag = BeautifulSoup(text, "lxml")
        table = frag.select_one("table")
        heads = [cell_text(th) for th in table.select("tr th")] if table else []
        rows = []
        if table:
            body_rows = table.select("tbody tr") or table.select("tr")[1:]
            for tr in body_rows:
                cells = [cell_text(td) for td in tr.select("td")]
                if len(cells) < 2:
                    continue
                rows.append({"label": cells[0],
                             "values": [num(v) for v in cells[1:]],
                             "raw_values": cells[1:]})
        info["headers"] = heads
        info["rows"] = rows
        info["row_count"] = len(rows)
    else:
        info["preview"] = text[:200]
    return info


def audit_symbol(sess, symbol: str) -> dict:
    print(f"\n=== {symbol} ===", flush=True)
    rec_out: dict = {"symbol": symbol}
    try:
        soup = sess.get_soup(f"/company/{symbol}/consolidated/")
    except Exception as exc:  # noqa: BLE001
        rec_out["error"] = f"page fetch failed: {exc}"
        print(f"  page fetch FAILED: {exc}")
        return rec_out

    cids = company_ids(soup)
    rec_out["data_company_ids"] = cids
    rec_out["warehouse_id"] = parse_warehouse_id(soup)
    rec_out["source_url"] = f"{BASE_URL}/company/{symbol}/consolidated/"
    rec_out["retrieved_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    rec_out["schedule_cid"] = cids[0] if cids else None

    # archive the source page (provenance + future parser fixtures)
    os.makedirs(FIXTURE_DIR, exist_ok=True)
    with open(os.path.join(FIXTURE_DIR, f"{symbol}__page.html"), "w",
              encoding="utf-8") as fp:
        fp.write(str(soup))

    parents = plus_rows(soup)
    rec_out["plus_rows"] = parents
    print(f"  data-company-id={rec_out['schedule_cid']} "
          f"(warehouse={rec_out['warehouse_id']}), + rows: {len(parents)}")

    # current scraper output for the same page
    from screener_finance.parse import parse_company
    record = parse_company(symbol, "consolidated", soup, rec_out["source_url"])
    canon = canonical(record)
    rec_out["canonical_items"] = sorted({s["item"] for s in canon["statements"]})

    schedules: list[dict] = []
    cid = rec_out["schedule_cid"]
    for pr in parents:
        bare = pr["parent_label"].rstrip("+").strip()
        attempt: dict = {"section": pr["section_key"],
                         "section_dom_id": pr["section_dom_id"],
                         "parent_label": pr["parent_label"],
                         "parent_value_latest": pr["parent_value_latest"]}
        if pr["section_key"] == "shareholding":
            # Verified live on RELIANCE: 4 endpoint variants all fail for
            # shareholding rows — they are not schedule-backed.
            attempt["schedule_endpoint_works"] = False
            attempt["missing_because"] = (
                "not schedule-backed (verified on RELIANCE: all variants fail)")
            schedules.append(attempt)
            continue
        # try the documented shape first: bare label + dom-id section
        variants = [
            (bare, pr["section_dom_id"], ""),
            (bare, pr["section_key"], ""),
            (pr["parent_label"], pr["section_dom_id"], ""),
            (bare, pr["section_dom_id"], "1"),
        ]
        got = None
        for parent_p, section_p, cons_p in variants:
            status, text = call_schedule(sess, cid, parent_p, section_p,
                                         cons_p, rec_out["source_url"])
            attempt.setdefault("attempts", []).append({
                "parent": parent_p, "section": section_p,
                "consolidated": cons_p, "status": status})
            if status == 200 and text.strip() and not text.startswith("__EXC__"):
                got = (parent_p, section_p, cons_p, text)
                break
        if not got:
            attempt["schedule_endpoint_works"] = False
            attempt["missing_because"] = "endpoint returned no 200 body"
            print(f"  + {pr['parent_label']!r}: NO schedule response")
        else:
            parent_p, section_p, cons_p, text = got
            info = parse_schedule_payload(text)
            attempt["schedule_endpoint_works"] = True
            attempt["endpoint"] = (f"/api/company/{cid}/schedules/"
                                   f"?parent={parent_p}&section={section_p}"
                                   f"&consolidated={cons_p}")
            attempt["schedule"] = info
            fn = f"{symbol}__{pr['section_dom_id']}__{slug(bare)}"
            ext = "json" if info["payload_kind"] == "json" else "html"
            with open(os.path.join(FIXTURE_DIR, f"{fn}.{ext}"), "w",
                      encoding="utf-8") as fp:
                fp.write(text)
            sched_items = [r["label"] for r in info.get("rows", [])]
            scraped_labels = [r["label"] for
                              r in record["sections"][pr["section_key"]]["rows"]]
            attempt["currently_scraped"] = {
                "parent": bare in [l.rstrip("+").strip() for l in scraped_labels],
                "detail_rows": [l for l in sched_items
                                if l not in scraped_labels],
            }
            print(f"  + {pr['parent_label']!r}: OK "
                  f"({info.get('row_count', '?')} schedule rows)")
        schedules.append(attempt)
    rec_out["schedules"] = schedules
    return rec_out


def write_matrix(results: list[dict]) -> None:
    lines = [
        "| Symbol | Parent + row | Section | Schedule endpoint works? "
        "| Schedule rows | Currently scraped? | Missing detail rows |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in results:
        for sch in r.get("schedules", []):
            works = sch.get("schedule_endpoint_works")
            n_rows = sch.get("schedule", {}).get("row_count", "-") \
                if works else "-"
            scraped = sch.get("currently_scraped", {})
            missing = ", ".join(scraped.get("detail_rows", [])[:6]) or "-"
            lines.append(
                f"| {r['symbol']} | {sch['parent_label']} | {sch['section']} "
                f"| {'yes' if works else 'NO'} | {n_rows} "
                f"| {scraped.get('parent', '-')} | {missing} |")
    os.makedirs(AUDIT_DIR, exist_ok=True)
    with open(os.path.join(AUDIT_DIR, "audit_matrix.md"), "w",
              encoding="utf-8") as fp:
        fp.write("\n".join(lines) + "\n")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", nargs="*", default=DEFAULT_SYMBOLS)
    ap.add_argument("--smoke", action="store_true",
                    help="RELIANCE only, max 2 parents")
    ap.add_argument("--force", action="store_true",
                    help="re-audit symbols already present in audit_raw.json")
    ap.add_argument("--delay", type=float, default=1.5)
    args = ap.parse_args()

    symbols = args.symbols
    if args.smoke:
        symbols = ["RELIANCE"]

    sess = get_session()
    sess.configure(delay=args.delay, log_level="INFO")
    os.makedirs(AUDIT_DIR, exist_ok=True)

    # resumable: skip symbols already audited; --force re-audits them but
    # still builds on the existing results (never wipes other symbols)
    raw_path = os.path.join(AUDIT_DIR, "audit_raw.json")
    results: list[dict] = []
    if os.path.exists(raw_path):
        with open(raw_path, encoding="utf-8") as fp:
            results = json.load(fp)
    done = {r["symbol"] for r in results if not args.force}

    for sym in symbols:
        if sym in done:
            print(f"=== {sym} === (already audited, skipping)")
            continue
        r = audit_symbol(sess, sym)
        if args.smoke and r.get("schedules"):
            r["schedules"] = r["schedules"][:2]
            results = [x for x in results if x["symbol"] != sym] + [r]
        else:
            results = [x for x in results if x["symbol"] != sym] + [r]
        # incremental save: the run can be interrupted/resumed safely
        with open(raw_path, "w", encoding="utf-8") as fp:
            json.dump(results, fp, indent=1, ensure_ascii=False)

    write_matrix(results)
    print(f"\nstats: {sess.stats}")
    print(f"artifacts: {FIXTURE_DIR}\n           {AUDIT_DIR}")


if __name__ == "__main__":
    main()
