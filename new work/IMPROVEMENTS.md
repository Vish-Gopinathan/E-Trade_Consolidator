# Improvement & Enhancement Ideas

Tracked thoughts on future work. Roughly ordered from most to least impactful.

---

## High Priority / Correctness

### ~~Sector mappings are too sparse~~ ✓ Fixed
`sector_analysis()` now loads mappings from `sectors.json` in the same directory, falling back to hardcoded defaults if the file is absent or malformed. `sectors.json` ships with ~140 symbols across 11 sectors. Users can edit it freely without touching Python code.

### ~~Transaction date range is hardcoded~~ ✓ Fixed
`main.py` now accepts `--start YYYY-MM-DD`, `--end YYYY-MM-DD`, and `--output-dir DIR` CLI arguments. Default start is Jan 1 of the current year; default end is today. The hardcoded `2025-01-01` literal is gone.

### ~~`get_portfolio` uses `eval()` on API response~~ ✓ Fixed
Replaced `eval(str(...))` with direct indexing of the list returned by `pyetrade`. The response is already a Python structure so no parsing is needed.

### ~~`percent_format` is applied but data is already multiplied by 100~~ ✓ Fixed
Changed the data-column percent format from `'0.00%'` to `'0.00'`. Excel's `0.00%` multiplies the cell value by 100 before displaying, which would turn `21.47` into `2147%`. The summary section continues to use `'0.00%'` correctly because it divides by 100 before writing those cells.

---

## Medium Priority / Quality of Life

### No CLI interface
Both `main.py` and `consolidator.py` require editing source to change any parameters. An `argparse`-based CLI would allow:
```
python main.py --start 2025-01-01 --end 2025-12-31 --output ~/reports/
```

### Output directory is not configurable
Output files land in whatever directory the script is run from. This is fine interactively but makes automation harder. Add an `--output-dir` argument or config option.

### No retry / backoff for the transaction detail API
`get_consolidated_transactions` calls `list_transaction_details` once per transaction in a loop with no error handling per call and no rate-limit backoff. For accounts with many transactions this will likely hit E-Trade's rate limits and fail silently (the transaction is just skipped). Add a simple retry with exponential backoff.

### `portfolio_summary` "Largest Holdings" key prints awkwardly in terminal
Fixed in `main.py`, but the `portfolio_summary` return value itself contains a list-of-dicts under `'Largest Holdings'`. Any caller that just iterates `summary.items()` and prints will get a raw Python object. Consider returning formatted strings there, or splitting the key into three separate top-holding keys.

### Realized gain calculation is wrong
`performance_metrics()` calculates "realized gain" as the total dollar value of all sell transactions, not as profit from those sells. The actual realized gain is `(sell price - cost basis) * quantity`. Without the cost basis for each sold lot, the number is meaningless and misleading.

### `add_totals_row` is never called from `main.py`
The function exists in `consolidator.py` and is exported but `main.py` never calls it. The totals row only appears in the Excel summary section, not in the holdings table itself. Either remove the function or wire it in.

### Analytics are not printed to the terminal
`main.py` runs the full analytics and exports to Excel but prints nothing to the terminal. At minimum, a condensed summary (concentration score, Sharpe ratio, win rate, top sector) would give immediate feedback without needing to open the Excel file.

---

## Lower Priority / New Features

### Historical performance tracking
Currently the tool is a point-in-time snapshot. Adding a historical mode (using `yfinance` for price history) would allow:
- Plotting portfolio value over time
- Calculating proper time-weighted return (TWR)
- Drawdown analysis

### Deposit-adjusted returns (Modified Dietz)
If the user has added or withdrawn cash during the period, simple gain/loss % is misleading. The Modified Dietz method accounts for cash flows to separate investment returns from contribution effects. Needs transaction data with deposit/withdrawal types (currently filtered out).

### Config file support
A `config.toml` or `config.json` for persistent settings (start date, output path, risk-free rate, custom sector mappings) would eliminate most hardcoded values without requiring CLI flags every time.

### Benchmark comparison
Compare portfolio return against SPY (or another user-specified benchmark) over the same period. Requires either `yfinance` or a stored benchmark price.

### Email/notification on completion
Optionally send the output files as email attachments when a run completes. Useful for scheduled runs.

### Scheduled / headless runs
OAuth currently requires interactive browser authorization, making automation difficult. E-Trade offers a session refresh mechanism. Investigating whether tokens can be persisted (encrypted) to enable non-interactive scheduled runs would unlock automation.

### Excel chart for sector allocation
The analytics Excel file has the sector data but no visualization. A pie chart on the Analytics Summary sheet would make the output more readable for non-technical users.
