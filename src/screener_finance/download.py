"""Batch helpers: sf.download(...) like yfinance.download, plus bulk export."""
from __future__ import annotations

import json
import os
import re
from typing import Iterable

from .ticker import Ticker

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
            _ = out[sym]._fetch()  # force fetch now (respect throttle)
        except Exception as exc:
            last_errors[sym] = str(exc)
    return out


def batch_download(symbols: Iterable[str], out_dir: str,
                   fmt: str = "both", csv_subdir: str = "csv",
                   skip_existing: bool = True, verbose: bool = True) -> dict:
    """Bulk download to disk with resume support.

        sf.batch_download(symbol_list, "out/screener_finance", fmt="both")

    Writes <out_dir>/<SYMBOL>.json (+ <out_dir>/csv/<SYMBOL>_*.csv when
    fmt includes csv). Returns a summary dict.
    """
    os.makedirs(out_dir, exist_ok=True)
    csv_dir = os.path.join(out_dir, csv_subdir)
    symbols = [str(s).strip().upper() for s in symbols if str(s).strip()]
    ok = skipped = failed = 0
    t0 = __import__("time").time()
    errors: list[tuple[str, str]] = []

    for i, sym in enumerate(symbols, 1):
        json_path = os.path.join(out_dir, f"{sym}.json")
        if skip_existing and os.path.exists(json_path):
            skipped += 1
            continue
        try:
            t = Ticker(sym)
            record = t.raw
            with open(json_path, "w", encoding="utf-8") as fp:
                json.dump(record, fp, indent=1, ensure_ascii=False)
            if fmt in ("csv", "both"):
                t.to_csv(csv_dir)
            ok += 1
            if verbose:
                rate = (__import__("time").time() - t0) / max(ok + skipped, 1)
                eta_min = rate * (len(symbols) - i) / 60
                print(f"[{i}/{len(symbols)}] {sym} OK "
                      f"({ok} ok, {skipped} skipped, {failed} failed) "
                      f"ETA {eta_min:.0f}m")
        except Exception as exc:
            failed += 1
            errors.append((sym, str(exc)))
            if verbose:
                print(f"[{i}/{len(symbols)}] {sym} FAILED ({exc})")

    if errors:
        with open(os.path.join(out_dir, "errors.log"), "w", encoding="utf-8") as fp:
            for sym, err in errors:
                fp.write(f"{sym}\t{err}\n")

    return {"ok": ok, "skipped": skipped, "failed": failed,
            "elapsed_s": round(__import__("time").time() - t0, 1)}
