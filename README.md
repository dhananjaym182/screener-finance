# screener-finance

**yfinance-style API for Indian stock fundamentals** — powered by
[Screener.in](https://www.screener.in). Written as a standalone server-side
library (like `yfinance`), installable with `pip`.

```python
import screener_finance as sf

t = sf.Ticker("RELIANCE")

t.info                 # {'name': ..., 'market_cap': ..., 'stock_pe': ..., 'roe': ...}
t.quarterly_results    # DataFrame — 13 quarters
t.profit_loss          # DataFrame — annual P&L back to Mar 2015
t.balance_sheet
t.cash_flow
t.ratios
t.shareholding
t.peers                # DataFrame — peer comparison
t.pros / t.cons        # Screener's analysis bullets
t.history("5y")        # daily close price series (experimental)

sf.download(["SBIN", "TCS", "INFY"])   # batch -> dict of Tickers
sf.compare(["TCS", "INFY", "WIPRO"])   # side-by-side metrics DataFrame
sf.search("tata", 5)                   # name/symbol search
```

## Install

```bash
pip install -e .                      # from a clone of this repo
# or once published:
pip install screener-finance
```

Python 3.9+. Deps: `requests`, `beautifulsoup4`, `lxml`, `pandas`, `click`.

## CLI (`sfin`)

```bash
sfin info SBIN                        # key metrics JSON
sfin quarterly RELIANCE               # quarterly results CSV -> stdout
sfin profit-loss SBIN                 # annual P&L CSV
sfin all TCS --json tcs.json --csv-dir out/csv
sfin compare SBIN HDFCBANK ICICIBANK
sfin history RELIANCE --period 5y --csv rel_px.csv
sfin search "hdfc"
sfin batch --symbols-file nse.txt --out-dir out/bulk --fmt both
```

## API surface (yfinance mapping)

| yfinance | screener-finance |
|---|---|
| `yf.Ticker("X")` | `sf.Ticker("X")` (`.upper()`d NSE/BSE symbol) |
| `t.info` | `t.info` — market_cap, stock_pe, book_value, roe, roce, dividend_yield … |
| `t.financials` | `t.financials` / `t.profit_loss` (annual P&L) |
| `t.quarterly_financials` | `t.quarterly_results` (13 quarters) |
| `t.balance_sheet` | `t.balance_sheet` |
| `t.cashflow` | `t.cash_flow` |
| `t.history(period)` | `t.history(period)` — daily close (screener chart API) |
| `yf.download([...])` | `sf.download([...])` / `sf.batch_download(list, out_dir)` |
| — | `t.shareholding`, `t.peers`, `t.pros`, `t.cons`, `t.documents` |
| — | `t.to_json(path)`, `t.to_csv(dir)` |
| — | `sf.search(q)`, `sf.compare([...])` |
| — | `sf.configure(delay=…, proxy=…, user_agent=…)` |

`view=` — Screener serves **consolidated** (default) and **standalone**
views: `sf.Ticker("SBIN", "standalone")`.

## Anti-blocking (built in)

- Full browser-like header set + shared `requests.Session`
- Enforced min-interval throttling (default **1.5s**) + random jitter
- TTL response cache (5 min) — repeated lookups don't re-hit the site
- HTTP 429 → honors `Retry-After`, then exponential backoff (3 tries)
- Optional proxy: `sf.configure(proxy="socks5://…")`
- Batch mode with resume: skips symbols already downloaded; failures → `errors.log`

**Timing:** measured ~1.0s/req latency → ~2s/stock including pacing.
All ~2,700 NSE active stocks ≈ **1.5–2 h**; NSE+BSE ≈ 3–4 h. Keep delays ≥1s
and run bulk jobs overnight (IST) — that's all screener.in needs.

## Data quality

Screener.in is the verified ground truth used by Tapetide's MCP API
(117/117 annual P&L values identical in a 3-stock cross-check — see the
[tapetide-scraper repo](https://github.com/dhananjaym182/tapetide-scraper)).

## Files layout

```
screener-finance/
├── pyproject.toml              # pip-installable, `sfin` entry point
├── src/screener_finance/
│   ├── __init__.py             # public API
│   ├── ticker.py               # Ticker class (yfinance-style)
│   ├── session.py              # HTTP session + throttle + cache + retries
│   ├── parse.py                # HTML -> dicts
│   ├── dataframe.py            # dict -> pandas
│   ├── download.py             # download() / batch_download()
│   ├── search.py               # search() / compare()
│   ├── cli.py                  # sfin command line
│   └── exceptions.py
├── tests/test_offline.py       # offline parser tests (CI)
├── examples/basic_usage.py
└── .github/workflows/ci.yml
```

## Responsible use

Screener.in is a free service — respect it: keep the default pacing, don't
remove the throttle, cache what you can. This library is for personal
research/education; don't redistribute their data commercially without
permission. Not investment advice.

## License

MIT
