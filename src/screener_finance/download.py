"""Batch helpers: sf.download(...), sf.batch_download(...), sf.refresh(...)."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from typing import Iterable

from .ticker import Ticker

_log = logging.getLogger("screener_finance")

last_errors: dict[str, str] = {}


def _sections_fingerprint(record: dict) -> dict[str, str]:
    """Stable per-section hash of the fundamental tables.

    Deliberately excludes top_ratios (Market Cap / Current Price / High-Low
    tick daily) — two fetches of the SAME fundamentals must produce the
    same fingerprint so refresh() can skip re-storing unchanged companies.
    """
    out = {}
    for name, section in (record.get("sections") or {}).items():
        blob = json.dumps(section, sort_keys=True, ensure_ascii=False)
        out[name] = hashlib.sha256(blob.encode("utf-8")).hexdigest()
    return out



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


def refresh(symbols: Iterable[str], out_dir: str,
            keys: dict[str, str] | None = None, verbose: bool = True,
            heartbeat: int = 200) -> dict:
    """Incremental update: re-fetch every page, store only what changed.

    Screener serves no Last-Modified/ETag, so detecting change costs one
    request per company — but unchanged companies are NOT re-written:
    their files (and scraped_at provenance) stay untouched, and disk/commit
    churn is limited to companies that actually reported something new.

        summary = sf.refresh(symbol_list, "raw")
        # {"new": 3, "unchanged": 4500, "updated": 30, "failed": 7, ...}

    Change detection: sha256 over each fundamental section of the stored
    file vs the freshly fetched page (top_ratios/prices excluded — they
    tick daily by nature). For updated symbols the log names the sections
    that changed.
    """
    keys = keys or {}
    from .session import get_session
    sess = get_session()

    os.makedirs(out_dir, exist_ok=True)
    symbols = [str(s).strip().upper() for s in symbols if str(s).strip()]
    new = unchanged = updated = failed = 0
    t0 = time.time()
    errors: list[tuple[str, str]] = []
    _log.info("refresh start: %d symbols -> %s", len(symbols), out_dir)

    for i, sym in enumerate(symbols, 1):
        json_path = os.path.join(out_dir, f"{sym}.json")
        try:
            t = Ticker(sym, key=keys.get(sym))
            record = t.fetch()  # the single company-page request
            fresh = _sections_fingerprint(record)

            old_sections = None
            if os.path.exists(json_path):
                try:
                    with open(json_path, encoding="utf-8") as fp:
                        old_sections = _sections_fingerprint(json.load(fp))
                except Exception:
                    old_sections = None  # corrupt file -> rewrite

            if old_sections == fresh:
                unchanged += 1
            else:
                changed = sorted(k for k, v in fresh.items()
                                 if old_sections is None or old_sections.get(k) != v)
                with open(json_path, "w", encoding="utf-8") as fp:
                    json.dump(record, fp, indent=1, ensure_ascii=False)
                if old_sections is None:
                    new += 1
                    _log.info("refresh [%d/%d] %s NEW", i, len(symbols), sym)
                else:
                    updated += 1
                    _log.info("refresh [%d/%d] %s UPDATED sections=%s",
                              i, len(symbols), sym, ",".join(changed))
                    if verbose:
                        print(f"[{i}/{len(symbols)}] {sym} UPDATED "
                              f"({', '.join(changed)})", flush=True)
        except Exception as exc:
            failed += 1
            errors.append((sym, str(exc)))
            _log.error("refresh [%d/%d] %s FAILED: %s", i, len(symbols), sym, exc)

        if verbose and heartbeat and i % heartbeat == 0:
            rate = (time.time() - t0) / i
            eta_min = rate * (len(symbols) - i) / 60
            print(f"[{i}/{len(symbols)}] progress: {new} new, {unchanged} "
                  f"unchanged, {updated} updated, {failed} failed "
                  f"ETA {eta_min:.0f}m", flush=True)

    if errors:
        with open(os.path.join(out_dir, "errors.log"), "w", encoding="utf-8") as fp:
            for sym, err in errors:
                fp.write(f"{sym}\t{err}\n")

    st = sess.stats
    summary = {"new": new, "unchanged": unchanged, "updated": updated,
               "failed": failed, "requests": st["requests"],
               "retries": st["retries"], "blocked_403": st["blocked"],
               "rate_limited_429": st["rate_limited"],
               "elapsed_s": round(time.time() - t0, 1)}
    if st["blocked"] or st["rate_limited"]:
        _log.warning("refresh done with blocking signals: %d x 403, %d x 429 — "
                     "consider raising the delay or adding a proxy",
                     st["blocked"], st["rate_limited"])
    else:
        _log.info("refresh done clean: %s", summary)
    if verbose:
        print(json.dumps(summary, indent=1), flush=True)
    return summary


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
