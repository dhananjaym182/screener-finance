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
- **FIXED — NBFC templates had no ex-equity figure (2026-10-01 defect
  sweep).** 334 lender/NBFC sheets spell the row `Borrowing` (singular)
  and 24 deposit-takers add `Deposits`; both fell through `ITEM_MAP`, so
  the derived row never fired for them (219 symbols had zero coverage).
  `Borrowing` -> `borrowings`, `Deposits` -> `deposits`; the derived
  composition now sums every provided liability line (borrowings +
  deposits + other_liabilities)
  (`test_canonical_nbfc_borrowing_and_deposits_templates`).
- **FIXED — `total_liabilities` was a naming trap.** The item held the
  balance-sheet GRAND TOTAL (== `total_assets`); mapping it onto a
  liabilities key silently publishes the sheet total as debt. Renamed to
  `total_equity_and_liabilities` (matching the provider label's meaning);
  `liabilities_ex_equity` remains the liabilities-only figure.
- **FIXED — stub flags were applied to profit_loss only (2026-10-01
  defect sweep).** The site prints the `NNm` duration suffix only on the
  P&L header (ACC: `Mar 2023 15m` in P&L vs plain `Mar 2023` in
  balance_sheet/cash_flow/ratios), so 103 stub periods carried mixed
  flags and the schedules panel (2.17M rows) had none at all. Stub-ness
  is now resolved per (symbol, view, period_end) via
  `resolve_stub_periods()` across ALL headers and applied to every annual
  section and to schedule detail rows; quarterly_results/shareholding
  columns are never flagged (quarters/snapshots can share a period_end
  with an annual stub). Stub rows get `period_type` `STUB` (never `Q`),
  and Jan/Feb transition ends map to the fiscal year ending that March
  (`Jan 2026` -> FY2026, previously mislabelled FY2027).
  (`test_canonical_stub_flags_apply_to_every_section`,
  `test_canonical_transition_stub_period_type_and_fy`,
  `test_stub_period_propagates_from_company_headers`,
  `test_schedule_jan_feb_period_is_stub_not_quarter`)

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
| `total_liabilities_ex_equity` | **now derived** | `liabilities_ex_equity` = borrowings + deposits + other_liabilities (deposits on NBFC/deposit-taker sheets) |
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

## Phase 7 — backfill (EXECUTED 2026-09-28 → 2026-09-30)

- Full run: 4,541 symbols, 54,029 requests, 49,558 payloads, 0×403,
  0×429, ~18.8h at delay=1.0–1.5s. Archive: `raw_schedules/<SYM>/<view>/`
  with provenance sidecars; `_backfill_done.json` markers make every
  re-run resume at zero requests.
- Outcome: 4,540/4,541 done. SHINDL/SHINEFASH/SHIPROCKET succeeded on
  retry (transient 503s); HEG succeeded under its renamed slug `HEGAM`
  (universe_keys.tsv row added); SANGINITA now 404s on Screener
  (delisted after the 2026-09-15 scrape) — structural gap, raw record
  retained.
- Volume: ~1.43M schedule rows promoted to canonical (Phase 8),
  ~320 pts/symbol where schedules exist; 3,072 of 4,540 symbols have
  expandable rows (the rest have none — structural, not a gap).

## Phase 8 — canonical ingestion (IMPLEMENTED: `schedules_normalize.py`)

Keep provider labels verbatim in observations (`detail_label` column);
map to canonical keys only via an explicit exact-label taxonomy
(`SCHEDULE_ITEM_MAP`, 95 triples — no string similarity); never merge
`Working capital changes` with its components (flagged via
`SCHEDULE_AGGREGATE_ITEMS`); display-derived % rows are `metric: False`
(`SCHEDULE_NON_METRIC_ITEMS`); nested children get their own nodes;
authority policy: schedules are the only source for capex/cash/debt
detail → authoritative, not a reconciliation candidate. Ingestion is
offline and marker-gated: only symbols with `_backfill_done.json` are
promoted (`ingest_archive`, `require_done_marker=True`).

## Status: Phases 1–8 COMPLETE (2026-09-30)

Scraper fix verified (12-company sample); full-universe schedule
backfill executed (4,540/4,541; SANGINITA delisted upstream); canonical
schedule layer implemented + 13 offline tests; dataset rebuilt with the
schedules panel (`canonical/schedules.csv.gz`, 1.43M rows over 3,072
symbols with expandable rows).
