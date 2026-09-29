"""Backfill schedule ("+" row) detail into an existing raw DB.

For every raw/<SYM>.json (pre-fix records lack company_id and expandable
flags):
  1. fetch the company page ONCE (view from the record, BSE-only key slugs
     from the DB universe map) -> data-company-id + per-section expandable
     row labels; patch the record ADDITIVELY (company_id, expandable_rows)
     without touching sections/top_ratios/scraped_at;
  2. fetch every annual "+"-row schedule via the schedules endpoint,
     archiving raw-first to <archive-dir>/<SYM>/<view>/ with provenance
     sidecars; skip_existing keeps re-runs at zero requests and never
     overwrites existing payloads;
  3. write a _backfill_done.json marker so interrupted runs resume exactly
     where they stopped.

Usage:
  python3 tools/backfill_schedules.py --pilot            # 5 symbols, verify
  python3 tools/backfill_schedules.py                    # full universe
  python3 tools/backfill_schedules.py --symbols X Y --force
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import sys
import time
from contextlib import suppress

LOCK_FILE = os.path.expanduser("~/.schedules_backfill.lock")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import screener_finance as sf  # noqa: E402
from screener_finance.schedules import fetch_all_schedules  # noqa: E402
from screener_finance.session import get_session  # noqa: E402
from screener_finance.ticker import Ticker  # noqa: E402

DEFAULT_DB = os.path.expanduser("~/india-fundamentals-db/raw")
DEFAULT_ARCHIVE = os.path.expanduser("~/india-fundamentals-db/raw_schedules")
ANNUAL_SECTIONS = ("profit_loss", "balance_sheet", "cash_flow")
MARKER = "_backfill_done.json"


def load_keys(db: str) -> dict[str, str]:
    """{symbol: screener_url_key} for BSE-only companies (numeric BSE code)."""
    for path in (os.path.join(os.path.dirname(db), "universe",
                              "universe_keys.tsv"),
                 os.path.expanduser("~/universe_keys.tsv")):
        if os.path.exists(path):
            keys = {}
            with open(path, encoding="utf-8") as fp:
                for row in csv.reader(fp, delimiter="\t"):
                    if len(row) >= 2 and row[0].strip():
                        keys[row[0].strip().upper()] = row[1].strip()
            return keys
    return {}


def backfill_symbol(sym: str, db: str, archive: str, keys: dict[str, str],
                    skip_existing: bool = True) -> dict:
    raw_path = os.path.join(db, f"{sym}.json")
    if not os.path.exists(raw_path):
        return {"symbol": sym, "status": "no_raw"}
    with open(raw_path, encoding="utf-8") as fp:
        rec = json.load(fp)

    marker_path = os.path.join(archive, sym, rec.get("view") or "consolidated",
                               MARKER)
    if os.path.exists(marker_path):
        with open(marker_path, encoding="utf-8") as fp:
            prev = json.load(fp)
        return {"symbol": sym, "status": "already_done",
                "payloads": prev.get("payloads", 0)}

    view = rec.get("view") or "consolidated"
    t = Ticker(sym, view=view, key=keys.get(sym))
    fresh = t.fetch()  # one page request: company_id + expandable flags

    # additive patch only — never touch existing observations
    patched = False
    if fresh.get("company_id") and rec.get("company_id") != fresh["company_id"]:
        rec["company_id"] = fresh["company_id"]
        patched = True
    exp_rows = {sec: [r["label"] for r in fresh["sections"][sec]["rows"]
                      if r.get("expandable")]
                for sec in ANNUAL_SECTIONS}
    if rec.get("expandable_rows") != exp_rows:
        rec["expandable_rows"] = exp_rows
        patched = True
    if patched:
        tmp = raw_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fp:
            json.dump(rec, fp, indent=1, ensure_ascii=False)
        os.replace(tmp, raw_path)

    # schedules: raw-first archive, skip_existing => resumable, no overwrites
    res = fetch_all_schedules(fresh, archive_dir=archive,
                              sections=ANNUAL_SECTIONS,
                              skip_existing=skip_existing)
    payloads = sum(len(v) for v in res.values())

    os.makedirs(os.path.dirname(marker_path), exist_ok=True)
    with open(marker_path, "w", encoding="utf-8") as fp:
        json.dump({
            "symbol": sym,
            "company_id": fresh.get("company_id"),
            "view": view,
            "sections": {k: len(v) for k, v in res.items()},
            "payloads": payloads,
            "completed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }, fp, indent=1)
    return {"symbol": sym, "status": "ok", "payloads": payloads,
            "company_id": fresh.get("company_id")}


def acquire_lock() -> bool:
    """Single-instance guard: two concurrent runners would double-hit the
    site and race on the same archive files."""
    try:
        if os.path.exists(LOCK_FILE):
            with open(LOCK_FILE, encoding="utf-8") as fp:
                pid = int(fp.read().strip() or 0)
            if pid and os.path.exists(f"/proc/{pid}"):
                return False
        with open(LOCK_FILE, "w", encoding="utf-8") as fp:
            fp.write(str(os.getpid()))
        return True
    except OSError:
        return False


def release_lock() -> None:
    with suppress(OSError):
        if os.path.exists(LOCK_FILE):
            with open(LOCK_FILE, encoding="utf-8") as fp:
                if fp.read().strip() == str(os.getpid()):
                    os.remove(LOCK_FILE)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--archive", default=DEFAULT_ARCHIVE)
    ap.add_argument("--symbols", nargs="*", help="subset to process")
    ap.add_argument("--limit", type=int, help="process only the first N")
    ap.add_argument("--delay", type=float, default=1.0,
                    help="min seconds between requests (default 1.0)")
    ap.add_argument("--no-skip-existing", action="store_true",
                    help="re-fetch payloads that already exist on disk")
    ap.add_argument("--force", action="store_true",
                    help="ignore completion markers and re-run symbols")
    ap.add_argument("--pilot", action="store_true",
                    help="5-symbol verification run")
    ap.add_argument("--minutes", type=float, default=0,
                    help="stop cleanly after N minutes (0 = until done); "
                         "re-run the same command to resume — completed "
                         "symbols are skipped with zero requests")
    args = ap.parse_args()
    if not acquire_lock():
        print("another backfill instance is already running (lock: "
              f"{LOCK_FILE}) — exiting", flush=True)
        return

    os.makedirs(args.archive, exist_ok=True)
    sf.configure(
        delay=args.delay,
        log_file=os.path.join(args.archive, "backfill.log"),
        log_level="INFO",
    )
    sess = get_session()

    symbols = args.symbols or [f[:-5] for f in sorted(os.listdir(args.db))
                               if f.endswith(".json")]
    if args.pilot:
        symbols = ["RELIANCE", "TCS", "HDFCBANK", "BAJFINANCE", "ITC"]
    if args.limit:
        symbols = symbols[:args.limit]

    keys = load_keys(args.db)
    total = len(symbols)
    print(f"backfill: {total} symbols -> {args.archive} "
          f"(delay={args.delay}s, skip_existing={not args.no_skip_existing})",
          flush=True)

    ok = done_before = no_raw = failed = payloads = 0
    errors: list[tuple[str, str]] = []
    t0 = time.time()
    blocked_stop = time_stop = False
    i = 0

    for i, sym in enumerate(symbols, 1):
        if args.minutes and (time.time() - t0) > args.minutes * 60:
            print(f"time budget of {args.minutes}min reached — stopping "
                  "cleanly; re-run the same command to resume", flush=True)
            time_stop = True
            break
        try:
            r = backfill_symbol(sym, args.db, args.archive, keys,
                                skip_existing=not args.no_skip_existing)
            status = r["status"]
            payloads += r.get("payloads", 0)
            if status == "ok":
                ok += 1
            elif status == "already_done":
                done_before += 1
            else:
                no_raw += 1
            line = (f"[{i}/{total}] {sym} {status}"
                    f" ({r.get('payloads', 0)} payloads)")
        except Exception as exc:  # noqa: BLE001 — one bad company must not stop the run
            failed += 1
            errors.append((sym, str(exc)))
            line = f"[{i}/{total}] {sym} FAILED: {exc}"
        if i % 25 == 0 or i == total:
            rate = (time.time() - t0) / max(i, 1)
            eta_h = rate * (total - i) / 3600
            print(f"{line} | {ok} ok, {done_before} done, {no_raw} no_raw, "
                  f"{failed} failed, {payloads} payloads, ETA {eta_h:.1f}h",
                  flush=True)
        else:
            print(line, flush=True)
        if sess.stats["blocked"] >= 3:
            print("ABORTING: 3+ 403 blocks — resume later with the same "
                  "command (skip_existing resumes with zero re-requests)",
                  flush=True)
            blocked_stop = True
            break

    release_lock()

    if errors:
        with open(os.path.join(args.archive, "errors.log"), "a",
                  encoding="utf-8") as fp:
            for sym, err in errors:
                fp.write(f"{sym}\t{err}\n")

    summary = {"symbols_attempted": ok + done_before + no_raw + failed,
               "ok": ok, "already_done": done_before, "no_raw": no_raw,
               "failed": failed, "payloads": payloads,
               "requests": sess.stats["requests"],
               "blocked_403": sess.stats["blocked"],
               "rate_limited_429": sess.stats["rate_limited"],
               "elapsed_s": round(time.time() - t0, 1),
               "aborted_on_blocks": blocked_stop,
               "stopped_on_time_budget": time_stop}
    with open(os.path.join(args.archive, "last_run_summary.json"), "w",
              encoding="utf-8") as fp:
        json.dump(summary, fp, indent=1)
    print(json.dumps(summary, indent=1), flush=True)


if __name__ == "__main__":
    main()
