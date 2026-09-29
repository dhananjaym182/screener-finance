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

### Bottom line

Ratios and Piotroski are fully computable after the fixes (S6 CA/CL and S8
need the proxies noted above). Altman Z is computable except for
`current_assets` / `current_liabilities` (X1) and true `retained_earnings`
(X2) — both blocked on Screener's detailed balance-sheet view (Task 2).
