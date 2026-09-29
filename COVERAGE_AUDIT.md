# Coverage audit — scraped data vs. ratios / Altman Z / Piotroski F

Goal: verify whether the one-request scrape captures every field needed to
compute (a) the 7 fundamental ratios, (b) Altman Z-score, (c) Piotroski
F-score — and fix what can be fixed in the normalization layer.

## Task 1 — normalization loss (raw vs canonical vs CSV)

Checked via the offline regression suite (`tests/test_offline.py`):

- Row labels survive `raw -> canonical -> tidy CSV` 1:1; no label is dropped
  or renamed silently (locked by `test_canonical_structure`,
  `test_canonical_ticker_method_and_exports`).
- Values pass through unparsed (`num()` only strips separators/currency),
  so raw and canonical values are identical; "High / Low" is split, not
  altered.
- **FIXED — audited stub columns were dropped.** Headers like
  `Mar 2015 15m` (audited/extended-period columns on some P&L sections)
  returned `None` from `parse_period()` and the whole column vanished from
  canonical output. They are now real, joinable FY rows flagged
  `stub: True` + `stub_months`, and excluded from TTM anchoring
  (`test_canonical_keeps_audited_stub_columns`).
- **FIXED — unmapped labels leaked raw.** Annual P&L spells the financing
  item `Interest` (quarterly spells it `Finance Cost`), and `OPM %`
  normalized to a bare `opm`; both fell through `ITEM_MAP`'s fallback and
  appeared as site spellings in canonical output. `OPM %` -> `opm_pct`,
  `Interest` -> `finance_cost`. Shareholding's `No. of Shares` ->
  `shares_outstanding` (Piotroski S7) was also leaking as `no_of_shares`.
- **FIXED — Altman X4 denominator was missing.** Screener's `Total
  Liabilities` is the grand total (equals `Total Assets`), so the
  ex-equity figure needed by Altman Z did not exist. `canonical()` now
  emits a derived `liabilities_ex_equity` row per balance-sheet period
  (`borrowings + other_liabilities`), flagged `derived: True` with
  `source_items`, never overwriting scraped rows
  (`test_canonical_derived_liabilities_ex_equity`).

## Task 2 — Screener's *detailed* balance-sheet view

Not verifiable offline — requires live fetches (RELIANCE, TCS, HDFCBANK)
against the expanded/detailed tables. **Open item.** The derived fields
below do not depend on it.

## Task 3 — field-by-field presence (canonical keys)

Sign conventions: statement values are stored exactly as Screener reports
them — depreciation and interest are positive expense lines; cash-flow
outflows (capex-heavy CFI, buyback-heavy CFF) are negative; there is no
separate capex row (only `free_cash_flow`), so asset-level capex cannot be
recovered from the summary view.

### Altman Z

| Field | Status | Source |
|---|---|---|
| `total_assets` | present | balance_sheet `Total Assets` (Cr) |
| `total_liabilities_ex_equity` | **now derived** | `liabilities_ex_equity` = borrowings + other_liabilities |
| `ebit` | present (proxy) | profit_loss `operating_profit` (`PBT excl other income`); exact EBIT = that + `finance_cost` |
| `sales` | present | profit_loss `Sales` (Cr) |
| `market_value_of_equity` | present | indicator `market_cap` (INR Cr) |
| `retained_earnings` | **absent** | only `reserves` exists; reserves ≠ retained earnings (includes share premium) — flag |
| `current_assets` / `current_liabilities` | **absent** | summary BS does not split CA/CL; only `working_capital_days` ratio — needs detailed view |

### Piotroski F

| Signal | Status | Source |
|---|---|---|
| S1/S2 `net_profit`, `total_assets` | present | profit_loss / balance_sheet |
| S3/S4 `operating_cash_flow` | present | cash_flow `cash_from_operations` |
| S5 `long_term_debt` | present | balance_sheet `borrowings` |
| S6 CA + CL | **absent** | same gap as Altman X1 |
| S7 `shares_outstanding` | **now mapped** | shareholding `No. of Shares`; fallback `equity_capital / face_value` |
| S8 `gross_profit` | **absent** | computable proxy `sales - expenses` (Screener's Expenses excludes finance cost + depreciation) |
| S9 `sales`, `total_assets` | present | asset turnover computable |

### Bottom line (summary-only scrape)

Ratios and Piotroski are computable after the fixes (S6 CA/CL and S8
need the proxies noted above). Altman Z is computable except for
`current_assets` / `current_liabilities` (X1) and true `retained_earnings`
(X2) — see the schedules audit below for what the `+`-row detail adds.

---

# Schedules ("+" rows) audit — 2026-09 (Phases 1–9)

## Endpoint & ID model (verified live)

    GET /api/company/{company_id}/schedules/?parent=<Row>&section=<dom-id>&consolidated=

- `company_id` is Screener's `data-company-id`, NOT the warehouse id
  (RELIANCE: 2726 vs 6598251). Both are now parsed into the record.
- `section` must be the DOM id (`profit-loss`, `balance-sheet`,
  `cash-flow`) — the Python key form returns empty bodies.
- `parent` is the displayed label without the trailing `+`.
- Response: JSON `{detail_label: {"Mar 2015": "128,165", ...}}`, sometimes
  with embedded display metadata (`setAttributes`, `isExpandable`).
- quarterly_results `+` rows return only display-derived metrics
  (growth %, cost ratios); shareholding `+` rows are NOT schedule-backed.

## Phase 1/2 — sample & matrix

12 companies (RELIANCE, TCS, HDFCBANK, BAJFINANCE, HDFCLIFE, MARUTI
[large-cap mfg], CUMMINSIND [mid], AARTIIND [small], ITC, SBIN, TATASTEEL,
INFY), 237 `+` rows, 177 schedule payloads captured (TATAMOTORS excluded:
its consolidated URL 404s post-demerger). Full payloads + provenance in
`audit_out/payload_archive/` (gitignored evidence, 204 files).

Fields the old scraper lost (parent-only, detail never fetched):
`Inventories`, `Trade receivables`, `Cash Equivalents`, `Fixed assets
purchased` (capex), `Gross Block`/`Accumulated Depreciation`, `Long/Short
term Borrowings`, `Lease Liabilities`, `Trade Payables`, `Loans n Advances`,
`Working capital changes`, `Direct taxes`, `Proceeds/Repayment of
borrowings`, and the rest of the 74 distinct detail labels.

## Semantics verified (do NOT violate in canonical mapping)

- Balance-sheet children sum EXACTLY to the parent (RELIANCE Mar 2026:
  Borrowings 402,962 / Other Liabilities 870,554 / Other Assets 566,733).
- CFO children are additive EXCEPT `Working capital changes` — an AGGREGATE
  overlapping the Receivables+Inventory+Payables rows (difference is the
  aggregate itself). Never sum both levels.
- `Fixed Assets` schedule is net-structured (Gross Block − Accumulated
  Depreciation); children are not additive to the parent.
- Annual `Sales +` / `Expenses +` schedules are display-derived (growth %,
  cost ratios); `Net Profit +` children are EPS/PE variants.
- Schedules are per-view (consolidated=1 vs "") and carry their own period
  set (13 headers on the page vs 12 in the payload for RELIANCE).
- Nested schedules exist (`isExpandable` children, e.g. Material Cost %).

## Phase 5/9 — recovered calculation inputs (n=12)

| input | companies | note |
|---|---|---|
| capex (`Fixed assets purchased`) | 12/12 | direct Piotroski S4 input |
| cash equivalents | 12/12 | |
| trade payables | 12/12 | |
| trade receivables / receivables Δ | 10/12 | absent only for banks |
| inventories | 7/12 | absent for financiers/banks/IT — structural |
| debt split (LT/ST/lease/other) | 6/12 | manufacturers/industrials |
| loans n advances, operating deposits | 12/12 | |

Piotroski impact: S4 becomes directly computable (capex) instead of
CFI-derived; S3/S4 WC-delta terms gain real inputs. Altman X1 (CA−CL) is
STILL NOT fully computable: schedules provide components (inventories,
receivables, cash, payables) but not an explicit CA/CL classification —
X1 remains a flagged approximation.

**Screener does NOT provide**: COGS/cost of materials (only cost ratios %
of sales), gross profit, retained earnings (only `reserves`), or an explicit
CA/CL split. These stay absent — no label-similarity substitutions.

## Parser/normalizer issues found & fixed

1. Scraper dropped `+` semantics entirely (`_strip_footnote`) and never
   fetched detail → fixed: `expandable` flag, `company_id` parsed,
   `schedules.py` fetch+archive layer, `Ticker.fetch_schedules()`.
2. `data-company-id` was never captured (history() even re-fetched the
   page to find it) → fixed; that extra request is gone.
3. Schedule payloads embed `setAttributes`/`isExpandable` inside label
   series → parser filters non-period keys, preserves expandability.
4. `parse_period` stub columns (`Mar 2015 15m`) are kept as joinable
   periods AND now carry `stub`/`stub_months` on every canonical row and
   tidy-CSV column — never silently treated as 12-month FYs.
5. Schedules carry their own period set that may differ from the page →
   payload periods are self-describing; consumers must join on period.

## Tests

`tests/test_schedules.py` (13 tests, real captured payloads, offline):
payload parsing incl. metadata filtering, expandable/company-id parsing,
additivity locks (BS sums, CFO-excluding-aggregate), raw-first archive +
provenance sidecar, skip_existing (no overwrite, zero requests),
Ticker-level caching, malformed-body handling. Both suites pass
(`ALL OFFLINE TESTS PASSED`, `ALL SCHEDULES TESTS PASSED`).

## Phase 7 — backfill design (NOT executed)

- Measured: ~10 annual `+` rows/company (14–15 with quarterly) → ~1.3M
  requests for 4,541 companies (460K annual-only).
- Runtime @ 2.2s/req: ~11 days full, ~4 days annual-only, ~13h for Nifty
  500 (annual-only); halve via 2 proxies.
- Storage: ~1.4KB/payload → ~70MB full universe + sidecars.
- Rate limits: none observed (430+ requests this audit, 0×403/429) at
  1.2–1.5s pacing; keep delay ≥1.5s, pause on 429 (Retry-After), abort on
  repeated 403.
- Order: 50 representative → Nifty 500 → rest. `skip_existing=True` makes
  every re-run resume with zero requests and zero overwrites; failures
  logged to errors.log and retried next run.

## Phase 8 — canonical ingestion policy (before any metric exposure)

Keep provider labels verbatim in observations; map to canonical keys only
via an explicit exact-label taxonomy (no string similarity); never merge
`Working capital changes` with its components; treat display-derived %
rows as non-metric; nested children get their own nodes; authority policy:
schedules are the only source for capex/cash/debt detail → authoritative,
not a reconciliation candidate.

## Status: STOPPED before full backfill

Scraper fix verified on the 12-company sample; no canonical DB changed;
no full-universe scraping performed. Awaiting review.
