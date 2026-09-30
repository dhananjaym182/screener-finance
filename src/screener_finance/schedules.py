"""Schedule detail ("+" rows): fetch, raw-archive, provenance.

On Screener company pages, rows whose label ends in "+" are expandable:
their detail lives behind an AJAX endpoint that takes Screener's internal
data-company-id (NOT the warehouse id used by the peers endpoint):

    GET /api/company/{company_id}/schedules/?parent=<Row>&section=<dom-id>&consolidated=

Verified live across a 13-company audit (2026-09, see COVERAGE_AUDIT.md):
- section is the DOM id ("profit-loss", "balance-sheet", "cash-flow");
  the Python section key form ("profit_loss") returns empty bodies.
- parent is the displayed label WITHOUT the trailing "+".
- annual P&L / balance-sheet / cash-flow rows return JSON:
      {"<detail label>": {"Mar 2015": "128,165", ...}, ...}
- quarterly_results "+" rows return only derived display metrics
  (Sales Growth %, Material Cost %, ...). Worth archiving, not mapping.
- shareholding "+" rows are NOT schedule-backed (all variants 4xx/empty).
- consolidated=1 selects the consolidated view ("" = standalone).

Raw payload policy: every non-empty response body is archived verbatim to
<archive_dir>/<SYMBOL>/<view>/<section>__<parent>.json|.txt with a sidecar
meta JSON (url, params, status, retrieved_at, referer, record view) BEFORE
any parsing, so future reprocessing never needs to re-scrape.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Any
from urllib.parse import quote

from .exceptions import ScreenerError
from .parse import num
from .session import BASE_URL, get_session

_log = logging.getLogger("screener_finance")

# Sections whose "+" rows carry real schedule detail. quarterly_results
# payloads are archived but flagged display_derived; shareholding rows are
# not schedule-backed at all (verified live).
SCHEDULE_SECTIONS = ("profit_loss", "balance_sheet", "cash_flow",
                     "quarterly_results")


def slug(text: str) -> str:
    """Filesystem-safe slug: 'Cash from Operating Activity' ->
    'cash_from_operating_activity'."""
    return re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_").lower()


def schedule_url(company_id: str, parent: str, section_dom_id: str,
                 consolidated: str | None = None) -> str:
    """Exact schedules endpoint for one expandable row.

    consolidated: "1" for the consolidated view, None to omit the param
    entirely (= standalone). Verified live (2026-09-30): for
    standalone-only filers, sending `consolidated=` with an EMPTY value is
    treated as a consolidated request and returns {}; only omitting the
    parameter selects the standalone dataset.
    """
    url = (f"{BASE_URL}/api/company/{company_id}/schedules/"
           f"?parent={quote(parent)}&section={quote(section_dom_id)}")
    if consolidated is not None:
        url += f"&consolidated={quote(consolidated)}"
    return url


def parse_schedule_json(text: str) -> dict[str, dict[str, Any]]:
    """Schedule body -> {detail_label: {"values": {period_str: value}, ...}}.

    The endpoint returns JSON of the shape
        {"Long term Borrowings": {"Mar 2015": "128,165", ...}, ...}
    but some label series also embed display metadata (verified across the
    13-company audit): "setAttributes": {"class": "strong"} and
    "isExpandable": true (a child row that itself expands into a nested
    schedule). Non-period keys are dropped; isExpandable is preserved as
    row metadata so nested detail can be discovered later. Values are
    parsed with the shared num() (None for "-"/blank).

    Any non-dict or malformed body raises ScreenerError (the raw body is
    archived by the caller either way).
    """
    from .normalize import parse_period

    try:
        data = json.loads(text)
    except ValueError as exc:
        raise ScreenerError(f"unparseable schedule body: {exc}") from exc
    if not isinstance(data, dict):
        raise ScreenerError(f"schedule payload is not a JSON object: {type(data).__name__}")
    out: dict[str, dict[str, Any]] = {}
    for label, series in data.items():
        if not isinstance(series, dict):
            continue
        row: dict[str, Any] = {
            "values": {str(k): num(v) for k, v in series.items()
                       if parse_period(str(k)) is not None},
        }
        if "isExpandable" in series:
            row["is_expandable"] = bool(series["isExpandable"])
        out[str(label)] = row
    return out


def _archive(path: str, body: str, meta: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fp:
        fp.write(body)
    with open(path + ".meta.json", "w", encoding="utf-8") as fp:
        json.dump(meta, fp, indent=1, ensure_ascii=False)


def fetch_schedule(record: dict[str, Any], parent_label: str, section_key: str,
                   archive_dir: str | None = None,
                   skip_existing: bool = False) -> dict[str, Any] | None:
    """Fetch one row's schedule detail for an already-fetched company record.

    record: the parsed company record (from Ticker.fetch() / parse_company);
            must contain company_id, view, symbol and source_url.
    parent_label: the displayed label WITHOUT the trailing "+" (as parsed:
            parse_financial_table strips it; pass row["label"]).
    section_key: Python section key ("profit_loss", "balance_sheet",
            "cash_flow", ...). Internally translated to the DOM id the
            endpoint requires.
    archive_dir: when set, the raw response body + a meta sidecar are
            written verbatim BEFORE parsing (raw-first provenance policy).
    skip_existing: when True and an archived payload for this
            (symbol, view, section, parent) already exists, return the
            parsed archive WITHOUT a network call — backfills never
            overwrite existing observations and re-runs cost 0 requests.

    Returns None when the endpoint yields no payload (shareholding rows,
    unknown rows). Raises ScreenerError on malformed JSON bodies.
    """
    company_id = record.get("company_id")
    if not company_id:
        return None
    section_dom = {"profit_loss": "profit-loss", "balance_sheet": "balance-sheet",
                   "cash_flow": "cash-flow", "quarterly_results": "quarters",
                   "ratios": "ratios", "shareholding": "shareholding",
                   }.get(section_key, section_key.replace("_", "-"))
    view = record.get("view") or "consolidated"
    # standalone = param OMITTED (an empty value still means "consolidated"
    # server-side and yields {} for standalone-only filers)
    consolidated = "1" if view == "consolidated" else None

    if skip_existing and archive_dir:
        existing = os.path.join(
            archive_dir, record["symbol"], view,
            f"{section_key}__{slug(parent_label)}.json")
        if os.path.exists(existing):
            with open(existing, encoding="utf-8") as fp:
                rows = parse_schedule_json(fp.read())
            return {
                "section": section_key,
                "parent_label": parent_label,
                "endpoint": None,
                "retrieved_at": None,
                "consolidated": consolidated or "0",
                "rows": rows,
                "from_archive": True,
                "is_display_derived": section_key == "quarterly_results",
            }

    sess = get_session()
    s = sess._ensure()
    url = schedule_url(company_id, parent_label, section_dom, consolidated)

    sess.throttle.wait()
    t0 = time.monotonic()
    referer = record.get("source_url") or f"{BASE_URL}/"
    resp = s.get(url, headers={
        "Referer": referer,
        "X-Requested-With": "XMLHttpRequest",
        "Accept": "application/json, text/plain, */*",
    }, timeout=sess.timeout)
    sess.stats["requests"] += 1
    elapsed = time.monotonic() - t0

    body = resp.text or ""
    _log.debug("schedule %s/%s -> %d (%.2fs, %d bytes)",
               record.get("symbol"), f"{section_key}:{parent_label}",
               resp.status_code, elapsed, len(body))

    meta = {
        "provider": "screener.in",
        "company": record.get("symbol"),
        "company_id": str(company_id),
        "view": view,
        "section": section_key,
        "section_dom_id": section_dom,
        "parent_label": parent_label,
        "url": url,
        "status_code": resp.status_code,
        "elapsed_s": round(elapsed, 3),
        "retrieved_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "referer": referer,
    }

    if resp.status_code != 200 or not body.strip():
        if archive_dir:
            meta["note"] = "empty or non-200 response"
            _archive(os.path.join(
                archive_dir, record["symbol"], view,
                f"{section_key}__{slug(parent_label)}.txt"), body, meta)
        return None

    if archive_dir:
        ext = "json" if body.lstrip().startswith("{") else "txt"
        _archive(os.path.join(
            archive_dir, record["symbol"], view,
            f"{section_key}__{slug(parent_label)}.{ext}"), body, meta)

    try:
        rows = parse_schedule_json(body)
    except (ValueError, ScreenerError) as exc:
        _log.warning("schedule %s/%s: unparseable body (%s) — raw archived",
                     record.get("symbol"), parent_label, exc)
        raise

    return {
        "section": section_key,
        "parent_label": parent_label,
        "endpoint": url.replace(BASE_URL, ""),
        "retrieved_at": meta["retrieved_at"],
        "consolidated": consolidated or "0",
        "rows": rows,
        "is_display_derived": section_key == "quarterly_results",
    }


def fetch_all_schedules(record: dict[str, Any],
                        archive_dir: str | None = None,
                        sections: tuple[str, ...] = SCHEDULE_SECTIONS,
                        parents: dict[str, list[str]] | None = None,
                        skip_existing: bool = False,
                        ) -> dict[str, dict[str, Any]]:
    """Fetch schedule detail for every expandable row of a fetched record.

    parents: optional {section_key: [parent labels]} override; by default
    every row flagged expandable=True by the parser is used.

    Returns {section_key: {parent_label: fetch_schedule() result}} —
    sections/parents with no schedule payload are omitted. Each request is
    paced by the shared session throttle; ~10 requests per company for the
    annual sections only (SCHEDULE_SECTIONS minus quarterly_results).
    """
    out: dict[str, dict[str, Any]] = {}
    for key in sections:
        section = (record.get("sections") or {}).get(key)
        if not section:
            continue
        if parents is not None:
            # explicit override: fetch exactly the listed parents and
            # nothing else (sections not listed fetch nothing) — this is
            # what makes targeted re-fetches/resumes deterministic
            labels = list(parents.get(key) or [])
        else:
            labels = [r["label"] for r in section.get("rows") or []
                      if r.get("expandable")]
        for label in labels:
            res = fetch_schedule(record, label, key, archive_dir=archive_dir,
                                 skip_existing=skip_existing)
            if res is not None:
                out.setdefault(key, {})[label] = res
    return out
