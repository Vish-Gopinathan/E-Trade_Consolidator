# Improvement & Enhancement Ideas

Tracked thoughts on future work. Roughly ordered from most to least impactful.

---

## High Priority / Correctness

### ~~Sector mappings are too sparse~~ ✓ Fixed
`sector_analysis()` loads mappings from `sectors.json` in the same directory, falling back to hardcoded defaults. `sectors.json` ships with ~140 symbols across 11 sectors. Users can edit it freely.

### ~~Transaction date range is hardcoded~~ ✓ Fixed
`main.py` accepts `--start YYYY-MM-DD`, `--end YYYY-MM-DD`, and `--output-dir DIR`. Default start is Jan 1 of the current year.

### ~~`get_portfolio` uses `eval()` on API response~~ ✓ Fixed
Replaced `eval(str(...))` with direct indexing.

### ~~`percent_format` is applied but data is already multiplied by 100~~ ✓ Fixed
Changed data-column percent format from `'0.00%'` to `'0.00'`.

### ~~Deposit-adjusted returns (Modified Dietz)~~ ✓ Fixed
`performance_metrics()` includes deposit-adjusted return. Cash flows are weighted by when they occurred and factored out of the return so contributions are not mistaken for gains.

### ~~Transaction classification lumped dividends with deposits~~ ✓ Fixed
`Dividend` and `Interest` are now classified as `Income`, not `Deposit`. `Journal` entries are classified as `Internal` and excluded from cash flow reporting. Only actual external movements (ACH, Wire, EFT, Check, Contribution, Distribution) count as Deposit/Withdrawal.

### ~~90-day API limit silently truncated transaction history~~ ✓ Fixed
`get_consolidated_transactions` now chunks any date range into 89-day windows and merges results. A full year of history is fetched correctly.

---

## Medium Priority / Quality of Life

### No retry / backoff for the transaction detail API
`get_consolidated_transactions` calls `list_transaction_details` once per trade in a loop with no error handling per call and no rate-limit backoff. For accounts with many trades this can hit E-Trade's rate limits. Add simple retry with exponential backoff.

### `add_totals_row` is never called from `main.py`
The function exists in `consolidator.py` but `main.py` never calls it. The totals row only appears in the Excel summary section, not in the holdings table itself. Either wire it in or remove it.

### Analytics are not printed to the terminal
`main.py` exports analytics to Excel but prints nothing to the terminal. A condensed summary (concentration score, Sharpe ratio, win rate, top sector) would give immediate feedback.

### Debug `[TXN]` lines are always on
The transaction classifier prints a `[TXN]` line for every non-trade transaction. This is useful for diagnosing deposit classification but noisy in normal use. Add a `--verbose` flag or only print if classification is `Other`.

---

## Lower Priority / New Features

### Config file support
A `config.toml` for persistent settings (start date, output path, risk-free rate) would eliminate needing CLI flags every run.

### Benchmark comparison
Compare portfolio return against SPY (or a user-specified benchmark) over the same period. Requires `yfinance` or a stored benchmark price.

### Historical performance tracking
Currently the tool is a point-in-time snapshot. A historical mode using `yfinance` for price history would allow portfolio value over time, time-weighted return (TWR), and drawdown analysis.

### Excel chart for sector allocation
The analytics Excel file has sector data but no visualization. A pie chart on the Analytics Summary sheet would improve readability.

### Email/notification on completion
Optionally send output files as email attachments when a run completes. Useful for scheduled runs.

### Scheduled / headless runs
OAuth requires interactive browser authorization. Investigating E-Trade's session refresh to persist tokens (encrypted) would unlock non-interactive scheduled runs.
