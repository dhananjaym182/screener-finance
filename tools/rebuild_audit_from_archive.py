"""Rebuild audit_raw.json + audit_matrix.md from the archived raw payloads.

Offline (zero network): the per-symbol audit record is reconstructed from
audit_out/payload_archive/ — <SYM>__page.html for the page-level facts and
<SYM>__<dom>__<parent>.json for the schedule payloads. The archive is the
primary evidence; this makes it the single source of truth.
"""
from __future__ import annotations

import glob
import json
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from bs4 import BeautifulSoup  # noqa: E402

from screener_finance.parse import (  # noqa: E402
    SECTION_IDS,
    cell_text,
    num,
    parse_company,
    parse_warehouse_id,
)
from screener_finance.schedules import parse_schedule_json  # noqa: E402
from screener_finance.session import BASE_URL  # noqa: E402

AUDIT_DIR = os.path.join(os.path.dirname(__file__), "..", "audit_out")
ARCHIVE = os.path.join(AUDIT_DIR, "payload_archive")


def plus_rows(soup):
    out = []
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
            out.append({"section_key": key, "section_dom_id": dom_id,
                        "parent_label": label.strip(),
                        "parent_value_latest": latest, "headers": headers})
    return out


def main() -> None:
    results = []
    pages = sorted(glob.glob(os.path.join(ARCHIVE, "*__page.html")))
    for pagef in pages:
        sym = os.path.basename(pagef).replace("__page.html", "")
        soup = BeautifulSoup(open(pagef, encoding="utf-8").read(), "lxml")
        cids = []
        for el in soup.select("[data-company-id]"):
            if el["data-company-id"] not in cids:
                cids.append(el["data-company-id"])
        rec_out = {
            "symbol": sym,
            "data_company_ids": cids,
            "warehouse_id": parse_warehouse_id(soup),
            "source_url": f"{BASE_URL}/company/{sym}/consolidated/",
            "schedule_cid": cids[0] if cids else None,
            "plus_rows": plus_rows(soup),
            "rebuilt_from_archive": True,
        }
        record = parse_company(sym, "consolidated", soup, rec_out["source_url"])
        rec_out["canonical_items"] = sorted(
            {s["item"] for s in __import__("screener_finance.normalize",
                                          fromlist=["canonical"])
             .canonical(record)["statements"]})

        schedules = []
        for pr in rec_out["plus_rows"]:
            bare = pr["parent_label"].rstrip("+").strip()
            attempt = {"section": pr["section_key"],
                       "section_dom_id": pr["section_dom_id"],
                       "parent_label": pr["parent_label"],
                       "parent_value_latest": pr["parent_value_latest"]}
            if pr["section_key"] == "shareholding":
                attempt["schedule_endpoint_works"] = False
                attempt["missing_because"] = "not schedule-backed (verified live)"
                schedules.append(attempt)
                continue
            pat = os.path.join(
                ARCHIVE, f"{sym}__{pr['section_dom_id']}__"
                         f"{re.sub(r'[^A-Za-z0-9]+', '_', bare).strip('_').lower()}.json")
            if os.path.exists(pat):
                rows = parse_schedule_json(open(pat, encoding="utf-8").read())
                attempt["schedule_endpoint_works"] = True
                attempt["endpoint"] = (
                    f"/api/company/{rec_out['schedule_cid']}/schedules/"
                    f"?parent={bare}&section={pr['section_dom_id']}&consolidated=")
                attempt["schedule"] = {
                    "payload_kind": "json",
                    "detail_labels": list(rows.keys()),
                    "row_count": len(rows),
                }
                scraped = [r["label"] for r in
                           record["sections"][pr["section_key"]]["rows"]]
                attempt["currently_scraped"] = {
                    "parent": True,
                    "detail_rows": [l for l in rows if l not in scraped],
                }
            else:
                attempt["schedule_endpoint_works"] = False
                attempt["missing_because"] = "no archived payload"
            schedules.append(attempt)
        rec_out["schedules"] = schedules
        results.append(rec_out)

    with open(os.path.join(AUDIT_DIR, "audit_raw.json"), "w",
              encoding="utf-8") as fp:
        json.dump(results, fp, indent=1, ensure_ascii=False)

    lines = ["| Symbol | Parent + row | Section | Schedule works? | Detail rows |",
             "|---|---|---|---|---|"]
    n_works = 0
    for r in results:
        for s in r["schedules"]:
            works = s.get("schedule_endpoint_works")
            n_works += bool(works)
            lines.append(
                f"| {r['symbol']} | {s['parent_label']} | {s['section']} "
                f"| {'yes' if works else 'no'} "
                f"| {s.get('schedule', {}).get('row_count', '-')} |")
    with open(os.path.join(AUDIT_DIR, "audit_matrix.md"), "w",
              encoding="utf-8") as fp:
        fp.write("\n".join(lines) + "\n")
    print(f"rebuilt {len(results)} company records, "
          f"{n_works} working schedule payloads, "
          f"{sum(len(r['plus_rows']) for r in results)} + rows")


if __name__ == "__main__":
    main()
