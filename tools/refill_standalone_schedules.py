#!/usr/bin/env python3
"""Refill empty schedule payloads (standalone-only companies).

Diagnosis (2026-09-30): ~1,468 companies are standalone-only filers.
Screener serves their standalone data at the /consolidated/ URL — so the
statement scrape is fine — but the schedules API with `consolidated=1`
returns {} because no consolidated dataset exists for them. The same
endpoint WITHOUT the consolidated param returns the full schedule
(verified live on 1STCUS company_id 1024: consolidated={} for every
parent, standalone returns complete series).

For every archived payload that is exactly {} across ALL annual sections
of a symbol, this runner re-fetches those rows with standalone params,
archiving raw-first to <archive>/<SYM>/standalone/ (never overwriting the
consolidated empties — they stay as provenance of the failed attempt).
A _backfill_done.json marker in the standalone dir makes re-runs cost
zero requests, exactly like the main backfill.

Usage:
  python3 tools/refill_standalone_schedules.py --pilot      # 50 symbols
  python3 tools/refill_standalone_schedules.py              # full sweep
  python3 tools/refill_standalone_schedules.py --minutes 240  # budgeted
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from contextlib import suppress

LOCK_FILE = os.path.expanduser("~/.schedules_refill.lock")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from screener_finance.schedules import fetch_all_schedules  # noqa: E402

DEFAULT_DB = os.path.expanduser("~/india-fundamentals-db/raw")
DEFAULT_ARCHIVE = os.path.expanduser("~/india-fundamentals-db/raw_schedules")
ANNUAL_SECTIONS = ("profit_loss", "balance_sheet", "cash_flow")
MARKER = "_backfill_done.json"


def _payload_files(vdir: str) -> list[str]:
    return [f for f in os.listdir(vdir)
            if f.endswith(".json") and not f.startswith("_")
            and not f.endswith(".meta.json")]


def needs_refill(sym: str, archive: str) -> bool:
    """True when consolidated payloads exist but are ALL empty {}."""
    vdir = os.path.join(archive, sym, "consolidated")
    if not os.path.isdir(vdir):
        return False
    if os.path.exists(os.path.join(archive, sym, "standalone", MARKER)):
        return False
    files = _payload_files(vdir)
    if not files:
        return False
    for fn in files:
        with open(os.path.join(vdir, fn), encoding="utf-8") as fp:
            if json.load(fp):
                return False  # consolidated worked for this symbol
    return True


def refill_symbol(sym: str, db: str, archive: str,
                  skip_existing: bool = True) -> dict:
    with open(os.path.join(db, f"{sym}.json"), encoding="utf-8") as fp:
        rec = json.load(fp)
    if not rec.get("company_id"):
        return {"symbol": sym, "status": "no_company_id"}

    standalone = dict(rec)
    standalone["view"] = "standalone"

    parents = {sec: list(labels)
               for sec, labels in (rec.get("expandable_rows") or {}).items()
               if sec in ANNUAL_SECTIONS}
    if not any(parents.values()):
        return {"symbol": sym, "status": "no_expandable_rows"}

    res = fetch_all_schedules(standalone, archive_dir=archive,
                              sections=ANNUAL_SECTIONS, parents=parents,
                              skip_existing=skip_existing)
    payloads = sum(len(v) for v in res.values())
    nonempty = sum(1 for sec in res.values() for r in sec.values()
                   if any(rr.get("values") for rr in
                          (r.get("rows") or {}).values()
                          if isinstance(r.get("rows"), dict))
                   or any(vals for vals in (r.get("rows") or {}).values()
                          if isinstance(vals, dict)))

    marker_dir = os.path.join(archive, sym, "standalone")
    os.makedirs(marker_dir, exist_ok=True)
    with open(os.path.join(marker_dir, MARKER), "w", encoding="utf-8") as fp:
        json.dump({
            "symbol": sym,
            "company_id": rec.get("company_id"),
            "view": "standalone",
            "reason": "consolidated schedules empty; standalone-only filer",
            "sections": {k: len(v) for k, v in res.items()},
            "payloads": payloads,
            "completed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                          time.gmtime()),
        }, fp, indent=1)
    status = "ok" if nonempty else "still_empty"
    return {"symbol": sym, "status": status, "payloads": payloads,
            "nonempty": nonempty}


def acquire_lock() -> bool:
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
    ap.add_argument("--delay", type=float, default=1.2,
                    help="min seconds between requests (default 1.2)")
    ap.add_argument("--no-skip-existing", action="store_true")
    ap.add_argument("--pilot", action="store_true",
                    help="first 50 refill candidates, verify then stop")
    ap.add_argument("--minutes", type=float, default=0,
                    help="stop cleanly after N minutes (0 = until done); "
                         "re-run resumes at zero requests")
    args = ap.parse_args()
    if not acquire_lock():
        print("another refill instance is already running (lock: "
              f"{LOCK_FILE}) — exiting", flush=True)
        return

    import screener_finance as sf
    os.makedirs(args.archive, exist_ok=True)
    sf.configure(
        delay=args.delay,
        log_file=os.path.join(args.archive, "standalone_refill.log"),
        log_level="INFO",
    )

    if args.symbols:
        symbols = args.symbols
    else:
        symbols = sorted(f[:-5] for f in os.listdir(args.db)
                         if f.endswith(".json"))
        if args.pilot:
            candidates = [s for s in symbols if needs_refill(s, args.archive)]
            symbols = candidates[:50]

    queue = [s for s in symbols if needs_refill(s, args.archive)]
    total = len(queue)
    print(f"standalone refill: {total} symbols need it "
          f"(of {len(symbols)} considered, delay={args.delay}s)",
          flush=True)

    ok = still_empty = skipped = failed = payloads = 0
    errors: list[tuple[str, str]] = []
    t0 = time.time()
    time_stop = False

    for i, sym in enumerate(queue, 1):
        if args.minutes and (time.time() - t0) > args.minutes * 60:
            print(f"time budget of {args.minutes}min reached — stopping "
                  "cleanly; re-run the same command to resume", flush=True)
            time_stop = True
            break
        try:
            r = refill_symbol(sym, args.db, args.archive,
                              skip_existing=not args.no_skip_existing)
            if r["status"] == "ok":
                ok += 1
            elif r["status"] == "still_empty":
                still_empty += 1
            else:
                skipped += 1
            payloads += r.get("payloads", 0)
            print(f"[{i}/{total}] {sym} {r['status']} "
                  f"({r.get('payloads', 0)} payloads)", flush=True)
        except Exception as exc:  # noqa: BLE001 — log & continue, resumable
            failed += 1
            errors.append((sym, str(exc)[:200]))
            print(f"[{i}/{total}] {sym} FAILED: {exc}", flush=True)

    summary = {
        "symbols_needed": total,
        "processed": ok + still_empty + skipped + failed,
        "ok": ok,
        "still_empty": still_empty,
        "skipped": skipped,
        "failed": failed,
        "payloads": payloads,
        "requests": sf.session_stats().get("requests", 0)
        if hasattr(sf, "session_stats") else None,
        "elapsed_s": round(time.time() - t0, 1),
        "stopped_on_time_budget": time_stop,
    }
    with open(os.path.join(args.archive, "last_refill_summary.json"),
              "w", encoding="utf-8") as fp:
        json.dump(summary, fp, indent=1)
    if errors:
        with open(os.path.join(args.archive, "refill_errors.log"), "a",
                  encoding="utf-8") as fp:
            for sym, err in errors:
                fp.write(f"{sym}\t{err}\n")
    release_lock()
    print(json.dumps(summary, indent=1), flush=True)


if __name__ == "__main__":
    main()
