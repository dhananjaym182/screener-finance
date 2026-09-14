# screener-finance

**One-request Python API for Indian stock fundamentals**, built on
[Screener.in](https://www.screener.in) — a standalone, pip-installable library
unifying the proven scraping patterns of
[sahiljani/screener-india](https://github.com/sahiljani/screener-india) and
[mayur1064/screenercli](https://github.com/mayur1064/screenercli) into one
clean service.

```python
import screener_finance as sf

t = sf.Ticker("RELIANCE")
t.fetch()               # THE one request — full parsed record, cached
t.data                  # free view of that record
t.info                  # free  → market_cap, stock_pe, roe, roce, dividend_yield…
t.quarterly_results     # free  → DataFrame, 13 quarters
t.profit_loss           # free  → DataFrame, annual back to Mar 2015 + TTM
t.balance_sheet / t.cash_flow / t.ratios / t.shareholding   # free
t.peers                 # lazy  → one small AJAX call, then cached
t.history("5y")         # lazy  → one chart call per period, then cached

sf.download(["SBIN", "TCS"])          # batch
sf.batch_download(syms, "out/")       # bulk to disk, resume support
sf.compare(["TCS", "INFY"])           # side-by-side metrics
sf.search("tata")
sf.configure(delay=2.0, proxy="socks5://…")
```

**One request per company.** Every statement, ratio, and document view reads
from the single parsed company page — you never re-scrape screener.in for the
same data. Only `peers` and `history()` touch separate (tiny) endpoints, and
each is fetched lazily and cached.

## Install

```bash
git clone https://github.com/dhananjaym182/screener-finance.git
pip install -e screener-finance
```

Python 3.9+. Deps: `requests`, `beautifulsoup4`, `lxml`, `pandas`, `click`.

## Request model (how the API stays cheap)

| Call | Network cost |
|---|---|
| `t.fetch()` / `t.data` | **1 request** — the company page; parsed into a record |
| `t.info`, `t.quarterly_results`, `t.profit_loss`, `t.balance_sheet`, `t.cash_flow`, `t.ratios`, `t.shareholding`, `t.pros`, `t.cons`, `t.about`, `t.documents`, `t.to_json()`, `t.to_csv()` | **0** — views over the fetched record |
| `t.peers` | 1 small AJAX fragment, lazily, cached in the record |
| `t.history(period)` | 1 chart-API call per period, cached per Ticker |
| `t.refresh()` | forces a fresh company-page request |

On top of that, the shared session adds a TTL cache (5 min) and enforced
pacing, so even `sf.download(["SBIN", "SBIN"])` hits the site once.

## API

### `sf.Ticker(symbol, view="consolidated")`

- `symbol` — NSE/BSE ticker (upper-cased). `view` — `"consolidated"` (default)
  or `"standalone"`; auto-falls back when one is unavailable.

**Data accessors** — `.info`, `.quarterly_results`, `.profit_loss`,
`.balance_sheet`, `.cash_flow`, `.ratios`, `.shareholding`, `.peers`,
`.pros`, `.cons`, `.about`, `.documents`, `.history(period)`, `.data`.

**Exports** — `.to_json(path)`, `.to_csv(dir)` (one CSV per statement +
ratios + peers + pros/cons + documents).

**Lifecycle** — `.fetch()`, `.refresh()`.

### Module functions

| Function | Purpose |
|---|---|
| `sf.download(symbols, view)` | dict of ready Tickers, paced by the shared session; failures land in `sf.last_errors` |
| `sf.batch_download(symbols, out_dir, fmt="json"\|"csv"\|"both", skip_existing=True)` | bulk to disk with resume; failures → `errors.log` |
| `sf.compare(symbols)` | key-metrics DataFrame, one row per symbol |
| `sf.search(query, limit)` | name/symbol search |
| `sf.configure(delay, jitter, timeout, max_retries, proxy, headers, cache_ttl, user_agent)` | global session tuning |

## CLI (`sfin`)

```bash
sfin info SBIN                       # key metrics (1 request)
sfin quarterly RELIANCE              # CSV -> stdout (same 1 request)
sfin profit-loss SBIN
sfin balance / cashflow / ratios / shareholding <SYMBOL>
sfin all TCS --json tcs.json --csv-dir out/csv    # everything, 1 request
sfin history RELIANCE --period 5y --csv rel_px.csv
sfin compare SBIN HDFCBANK ICICIBANK
sfin search "hdfc"
sfin batch --symbols-file nse.txt --out-dir out/bulk --fmt both
sfin --delay 2.0 --proxy socks5://127.0.0.1:9050 info SBIN
```

## Anti-blocking (built in)

- Full browser-like header set + shared `requests.Session`
- Enforced min-interval throttling (default **1.5s**) + random jitter
- TTL response cache (5 min) — repeated lookups never re-hit the site
- HTTP 429 → honors `Retry-After`, then exponential backoff (3 tries)
- Optional proxy: `sf.configure(proxy="socks5://…")`
- Batch mode with resume: skips symbols already downloaded

**Timing:** measured ~1.0s/req → ~2s/stock including pacing. All ~2,700 NSE
active stocks ≈ **1.5–2 h**; NSE+BSE ≈ 3–4 h. Keep delays ≥1s; run bulk jobs
overnight (IST).

## Why screener.in

In a three-way verification (see
[tapetide-scraper](https://github.com/dhananjaym182/tapetide-scraper)),
Screener.in's statements matched Tapetide's paid MCP API **117/117 annual
values** — it is the ground-truth source those services repackage.

## Project layout

```
screener-finance/
├── pyproject.toml              # pip-installable, `sfin` entry point
├── src/screener_finance/
│   ├── __init__.py             # public API
│   ├── ticker.py               # Ticker: one request -> many views
│   ├── session.py              # shared session: throttle, cache, retries, proxy
│   ├── parse.py                # HTML -> records
│   ├── dataframe.py            # records -> pandas
│   ├── download.py             # download() / batch_download()
│   ├── search.py               # search() / compare()
│   ├── cli.py                  # sfin command line
│   └── exceptions.py
├── tests/test_offline.py       # offline tests (CI, no network)
├── examples/basic_usage.py
└── .github/workflows/ci.yml
```

## Responsible use

Screener.in is a free service — keep the default pacing, don't strip the
throttle, cache what you can. For personal research/education; don't
redistribute their data commercially without permission. Not investment advice.

## License

MIT
