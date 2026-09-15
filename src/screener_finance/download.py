"""Batch helpers: sf.download(...) and bulk-to-disk sf.batch_download(...)."""
from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Iterable

from .ticker import Ticker

_log = logging.getLogger("screener_finance")

last_errors: dict[str, str] = {}


def download(symbols: Iterable[str], view: str = "consolidated") -> dict[str, Ticker]:
    """Download several tickers (polite pacing via the shared session).

        tickers = sf.download(["SBIN", "TCS"])
        tickers["SBIN"].info
        tickers["SBIN"].profit_loss

    Failed symbols are reported and skipped (check sf.last_errors).
    """
    global last_errors
    last_errors = {}
    out: dict[str, Ticker] = {}
    for sym in symbols:
        sym = str(sym).strip().upper()
        if not sym:
            continue
        try:
            out[sym] = Ticker(sym, view)
            _ = out[sym].fetch()  # force fetch now (respect throttle)
        except Exception as exc:
            last_errors[sym] = str(exc)
            _log.error("download: %s failed: %s", sym, exc)
    return out


def batch_download(symbols: Iterable[str], out_dir: str,
                   fmt: str = "both", csv_subdir: str = "csv",
                   skip_existing: bool = True, verbose: bool = True,
                   keys: dict[str, str] | None = None) -> dict:
    """Bulk download to disk with resume support.

        sf.batch_download(symbol_list, "out/screener_finance", fmt="both")

    keys: optional {symbol: screener_url_key} map — BSE-only companies are
    addressed by their numeric BSE code (from sf.universe.all_unique()).

    Writes <out_dir>/<SYMBOL>.json (+ <out_dir>/csv/<SYMBOL>_*.csv when
    fmt includes csv). Returns a summary dict.
    """
    keys = keys or {}
    from .session import get_session
    sess = get_session()

    os.makedirs(out_dir, exist_ok=True)
    csv_dir = os.path.join(out_dir, csv_subdir)
    symbols = [str(s).strip().upper() for s in symbols if str(s).strip()]
    ok = skipped = failed = 0
    t0 = time.time()
    errors: list[tuple[str, str]] = []
    _log.info("batch start: %d symbols -> %s (fmt=%s, resume=%s)",
              len(symbols), out_dir, fmt, skip_existing)

    for i, sym in enumerate(symbols, 1):
        json_path = os.path.join(out_dir, f"{sym}.json")
        if skip_existing and os.path.exists(json_path):
            skipped += 1
            continue
        try:
            t = Ticker(sym, key=keys.get(sym))
            record = t.fetch()  # the single company-page request
            with open(json_path, "w", encoding="utf-8") as fp:
                json.dump(record, fp, indent=1, ensure_ascii=False)
            if fmt in ("csv", "both"):
                t.to_csv(csv_dir)
            ok += 1
            _log.info("batch [%d/%d] %s OK (%d ok, %d skipped, %d failed)",
                      i, len(symbols), sym, ok, skipped, failed)
            if verbose:
                rate = (time.time() - t0) / max(ok + skipped, 1)
                eta_min = rate * (len(symbols) - i) / 60
                print(f"[{i}/{len(symbols)}] {sym} OK "
                      f"({ok} ok, {skipped} skipped, {failed} failed) "
                      f"ETA {eta_min:.0f}m", flush=True)
        except Exception as exc:
            failed += 1
            errors.append((sym, str(exc)))
            _log.error("batch [%d/%d] %s FAILED: %s", i, len(symbols), sym, exc)
            if verbose:
                print(f"[{i}/{len(symbols)}] {sym} FAILED ({exc})", flush=True)

    if errors:
        with open(os.path.join(out_dir, "errors.log"), "w", encoding="utf-8") as fp:
            for sym, err in errors:
                fp.write(f"{sym}\t{err}\n")

    st = sess.stats
    if st["blocked"] or st["rate_limited"]:
        _log.warning("batch done with blocking signals: %d x 403, %d x 429, "
                     "%d retries — consider raising --delay or adding a proxy",
                     st["blocked"], st["rate_limited"], st["retries"])
    else:
        _log.info("batch done clean: %d requests, %d retries, "
                  "no 403/429 observed", st["requests"], st["retries"])

    return {"ok": ok, "skipped": skipped, "failed": failed,
            "requests": st["requests"], "retries": st["retries"],
            "blocked_403": st["blocked"], "rate_limited_429": st["rate_limited"],
            "elapsed_s": round(time.time() - t0, 1)}
