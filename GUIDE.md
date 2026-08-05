# E-Trade Portfolio Consolidator — Developer & User Guide

## Table of Contents

- [Overview](#overview)
- [Project Structure](#project-structure)
- [Prerequisites](#prerequisites)
- [Setup](#setup)
- [Usage](#usage)
- [Module Reference](#module-reference)
- [Data Flow](#data-flow)
- [Excel Output Format](#excel-output-format)
- [Security](#security)
- [Troubleshooting](#troubleshooting)

---

## Overview

A Python application that consolidates investment holdings across multiple E-Trade brokerage accounts into a single unified view. It connects to the E-Trade API via OAuth, aggregates positions by symbol, classifies transactions (trades, deposits, withdrawals, income), calculates deposit-adjusted performance using the Modified Dietz method, runs portfolio analytics, and exports professionally formatted Excel reports.

### What It Does

- Authenticates with E-Trade via OAuth 2.0 (browser-based)
- Pulls holdings and cash balances from **all active accounts**
- Merges duplicate positions across accounts (same symbol held in multiple accounts)
- Calculates weighted average cost basis, gain/loss, and portfolio allocation
- Retrieves full transaction history across **all accounts** in arbitrary date ranges (automatically chunks into 89-day windows to comply with the API limit)
- Classifies transactions: Trades, Deposits, Withdrawals, Income (dividends/interest), Internal (excluded)
- Calculates deposit-adjusted return (Modified Dietz) separating investment gains from cash contributions
- Generates risk and concentration analytics
- Exports everything to formatted `.xlsx` files

---

## Project Structure

```
E-Trade consolidator/
├── GUIDE.md                        # This file
├── .env                            # API credentials (gitignored)
├── .gitignore
│
├── new work/                       # Active modular codebase
│   ├── main.py                     # Orchestrator entry point (run this)
│   ├── consolidator.py             # Core data retrieval, consolidation, and transaction classification
│   ├── analytics.py                # Portfolio analytics engine
│   └── sectors.json                # Editable sector-to-symbol mappings (~140 symbols, 11 sectors)
│
├── outputs/                        # Historical Excel exports (gitignored)
└── archive/                        # Deprecated code kept for reference (gitignored)
```

Output files (`.xlsx`) are written to the directory specified by `--output-dir` (default: current directory).

---

## Prerequisites

- **Python 3.10+** (developed on 3.12)
- **E-Trade developer account** with API access enabled
- **Consumer key and secret** from E-Trade's developer portal

### Dependencies

```bash
pip install pyetrade pandas numpy xlsxwriter python-dotenv
```

| Package | Purpose |
|---------|---------|
| `pyetrade` | E-Trade API wrapper |
| `pandas` | Data manipulation |
| `numpy` | Numerical operations |
| `xlsxwriter` | Excel file generation |
| `python-dotenv` | `.env` file loading |

---

## Setup

### 1. Enter the project directory

```bash
cd "E-Trade consolidator"
```

### 2. Create a virtual environment

```bash
python -m venv .venv
source .venv/bin/activate   # macOS/Linux
```

### 3. Install dependencies

```bash
pip install pyetrade pandas numpy xlsxwriter python-dotenv
```

### 4. Configure API credentials

Create a `.env` file in the project root:

```
CONSUMER_KEY = "your_consumer_key_here"
CONSUMER_SECRET = "your_consumer_secret_here"
```

These are your E-Trade API production keys. Do not commit this file (it is listed in `.gitignore`).

---

## Usage

```bash
cd "new work"
python main.py
```

### CLI Options

```
python main.py [--start YYYY-MM-DD] [--end YYYY-MM-DD] [--output-dir DIR]
```

| Flag | Default | Description |
|------|---------|-------------|
| `--start` | Jan 1 of current year | Start date for transaction history |
| `--end` | Today | End date for transaction history |
| `--output-dir` | `.` (current directory) | Where to write Excel output files |

Example — pull all of 2025:

```bash
python main.py --start 2025-01-01 --end 2025-12-31 --output-dir ~/reports/
```

### What Happens at Runtime

1. A browser window opens to E-Trade's OAuth authorization page
2. Log in and authorize the application
3. Copy the verification code from the browser
4. Paste the code into the terminal prompt
5. Holdings, cash balances, and transactions are fetched across all active accounts
6. Console prints a portfolio summary and transaction/income counts
7. Two Excel files are written to the output directory

### Output Files

| File | Contents |
|------|----------|
| `portfolio_consolidated_YYYY-MM-DD.xlsx` | Holdings, Transactions, Cash Flows, Income sheets |
| `portfolio_analytics_YYYY-MM-DD.xlsx` | Analytics Summary and Holdings Detail sheets |

---

## Module Reference

### `consolidator.py`

Core data retrieval, consolidation, and transaction classification.

#### `authenticate_etrade() -> dict`

Runs OAuth 2.0 flow. Opens the browser for user authorization. Returns a dict with `consumer_key`, `consumer_secret`, `oauth_token`, and `oauth_token_secret`.

#### `fetch_active_accounts(auth_tokens) -> (DataFrame, ETradeAccounts)`

Calls `list_accounts()`, filters to `accountStatus == 'ACTIVE'`. Returns a DataFrame of active accounts and the `pyetrade.ETradeAccounts` object for subsequent calls.

#### `get_portfolio(accounts_obj, account_id_key) -> DataFrame`

Fetches the complete portfolio for one account using the `Complete` view. Returns one row per position:

| Column | Source |
|--------|--------|
| Symbol | `Product.symbol` |
| Symbol Description | `Complete.symbolDescription` |
| Current Price | `Complete.price` |
| Quantity | `position.quantity` |
| Date Acquired | `position.dateAcquired` (epoch ms → datetime) |
| Price Paid | `position.pricePaid` |
| Total Cost | `position.totalCost` |
| Market Value | `position.marketValue` |
| Total Gain | `position.totalGain` |
| Total Gain % | `position.totalGainPct` |
| Percent of Portfolio | `position.pctOfPortfolio` |

#### `get_cash_balance(accounts_obj, account_id_key) -> float`

Returns `netCash` from the account balance endpoint.

#### `consolidate_holdings(df, cash=0) -> DataFrame`

Groups positions by `Symbol` across all accounts. Aggregates quantity, cost, and market value; recalculates weighted average price paid, total gain, gain %, and portfolio allocation. Appends a `CASH` row if `cash > 0`. Sorts by market value descending.

#### `portfolio_summary(consolidated_df, cash=0) -> dict`

Returns summary stats: stock count, total market value and cost basis, total unrealized gain ($ and %), cash amount and percentage, and top 3 holdings by market value.

#### `_date_chunks(start_date, end_date, chunk_days=89)`

Generator that splits `[start_date, end_date]` into consecutive windows of at most `chunk_days`. Used internally to work around the E-Trade API's 90-day limit on `list_transactions`.

#### `get_consolidated_transactions(accounts_obj, account_id_key, start_date, end_date) -> DataFrame`

Fetches **all** transactions for one account. Automatically chunks the date range into 89-day windows and merges results. Classifies every transaction into a `Category`:

| Category | Transaction Types |
|----------|-----------------|
| `Trade` | `Bought`, `Sold` — details fetched via `list_transaction_details` |
| `Deposit` | External money in: `Electronic Funds Transfer`, `ACH`, `Wire`, `Check`, `Contribution`, or description contains `"ACH DEPOSIT"` |
| `Withdrawal` | External money out: same types with negative amount, or `Distribution` |
| `Income` | `Dividend`, `Interest`, `Fee`, `Refund` |
| `Internal` | `Journal` with no external signal — **excluded from all output** |
| `Other` | Unrecognised types with no clear signal |

Returns columns: `Date`, `Security Name`, `Quantity`, `Price`, `Total Value`, `Transaction Type`, `Category`.

#### `_classify_non_trade(t_type, description, amount) -> str`

Priority-based classifier for non-trade transactions. See category table above. Called internally by `get_consolidated_transactions`.

#### `get_all_consolidated_transactions(accounts_obj, active_accounts, start_date, end_date) -> DataFrame`

Loops over all active accounts and concatenates their transaction DataFrames. Sorted newest-first.

#### `get_cash_flows(transaction_df) -> DataFrame`

Filters a full transaction DataFrame to only `Deposit` and `Withdrawal` rows. Returns columns `Date`, `Description`, `Total Value`, `Category`. Used as input for Modified Dietz return calculations.

#### `export_to_excel(consolidated_df, transactions_df=None, cash_flows_df=None, income_df=None, filename=None, summary_spacing=3)`

Writes a formatted `.xlsx` file with up to four sheets:

| Sheet | Condition | Contents |
|-------|-----------|----------|
| Holdings | Always | Position data with currency/percentage formatting + summary block |
| Transactions | If `transactions_df` provided | All trades and classified non-trade transactions |
| Cash Flows | If `cash_flows_df` provided | Deposits (green) and withdrawals (red) with totals |
| Income | If `income_df` provided | Dividends (gold) and interest (blue) with per-type totals |

Default filename: `portfolio_consolidated_YYYY-MM-DD.xlsx`.

---

### `analytics.py`

Portfolio analytics engine. All methods return plain dicts; the class performs no API calls.

#### Class: `PortfolioAnalytics`

```python
analytics = PortfolioAnalytics(
    consolidated_df,          # from consolidate_holdings()
    transactions_df=None,     # from get_all_consolidated_transactions()
    cash_flows_df=None,       # from get_cash_flows() — derived automatically if None
    risk_free_rate=0.04       # annual rate for Sharpe/Sortino (default 4%)
)
```

Internal attributes:
- `self.holdings` — positions only (CASH row excluded)
- `self.cash` — cash balance as a float
- `self.cash_flows` — Deposit/Withdrawal rows (derived from `transactions_df` if `cash_flows_df` not provided)
- `self.income` — Income-category rows (dividends, interest)

#### `concentration_analysis() -> dict`

HHI Score, Effective Positions, Top 3/5/10 Holdings %, Diversification Score.

HHI interpretation: < 1500 Well Diversified, < 2500 Moderately Diversified, < 5000 Concentrated, ≥ 5000 Highly Concentrated.

#### `sector_analysis() -> dict`

Maps symbols to sectors loaded from `sectors.json` (falls back to built-in defaults if missing). Returns dollar and percentage allocation per sector. Unmatched symbols go to `Other`. To add symbols, edit `sectors.json` — no Python changes needed.

#### `performance_metrics() -> dict`

- Simple return ($ and %) based on cost basis vs. current market value
- **Deposit-Adjusted Return (Modified Dietz)**: accounts for the timing of deposits and withdrawals so cash contributions are not mistaken for investment gains
  - Formula: `R = (EMV - BMV - CF) / (BMV + Σ(CF_i × W_i))`
  - BMV approximated as total cost basis (no period-start snapshot available)
- Total Deposits, Total Withdrawals, Net Cash Flows

#### `cash_flow_summary() -> dict`

Deposit and withdrawal counts, totals, largest transaction, and most recent date for each direction.

#### `income_summary() -> dict`

Total income received, broken down by type (Dividend, Interest, etc.) with counts and most recent payment date. Income is generated inside the account and does not affect deposit-adjusted return.

#### `risk_metrics() -> dict`

Volatility (std dev of position gain %s), Downside Deviation, Win Rate, Best/Worst Performer, Sharpe Ratio, Sortino Ratio.

Note: ratios use position-level gain percentages as a simplified proxy for time-series returns.

#### `liquidity_analysis() -> dict`

Cash balance, cash percentage, and a liquidity score (65–85).

#### `transaction_analysis() -> dict`

Buy/sell counts, total amounts, portfolio turnover, and average transaction size.

#### `holdings_quality_analysis() -> dict`

Positions bucketed by gain %: Highly Profitable (>50%), Profitable (0–50%), Breakeven, Small Loss, Moderate Loss, Major Loss. Identifies best and worst performers.

#### `generate_full_report() -> dict`

Runs all methods and returns a combined dict with keys: Concentration Analysis, Sector Analysis, Performance Metrics, Cash Flow Summary, Income Summary, Risk Metrics, Liquidity Analysis, Holdings Quality, Transaction Analysis.

#### `export_analytics_to_excel(consolidated_df, analytics_report, filename=None)`

Writes the analytics report to Excel with two sheets: **Analytics Summary** (all metrics as key-value pairs with nested dicts expanded) and **Holdings Detail** (full position table).

Default filename: `portfolio_analytics_YYYY-MM-DD.xlsx`.

---

## Data Flow

```
E-Trade API (OAuth 2.0)
    │
    ▼
fetch_active_accounts()         → list of account IDs (all active accounts)
    │
    ▼
For each account (in parallel loops):
    get_portfolio()             → raw positions DataFrame
    get_cash_balance()          → float
    │
    ▼
pd.concat(all accounts)         → combined DataFrame
    │
    ▼
consolidate_holdings()          → grouped by Symbol, gains recalculated, CASH row appended
    │
    ▼
portfolio_summary()             → dict of summary stats (printed to terminal)
    │
    ▼
get_all_consolidated_transactions()   → all transactions across all accounts
    │                                   (auto-chunked into 89-day windows)
    ├── get_cash_flows()         → Deposit/Withdrawal rows only
    └── income rows (inline)    → Income rows (Dividend/Interest)
    │
    ▼
export_to_excel()               → portfolio_consolidated_YYYY-MM-DD.xlsx
    │                             (Holdings + Transactions + Cash Flows + Income sheets)
    ▼
PortfolioAnalytics()            → analytics object
    │
    ▼
generate_full_report()          → dict of all analytics sections
    │
    ▼
export_analytics_to_excel()     → portfolio_analytics_YYYY-MM-DD.xlsx
```

---

## Excel Output Format

### `portfolio_consolidated_*.xlsx`

**Holdings sheet**

| Column | Format | Description |
|--------|--------|-------------|
| Symbol | Text | Ticker symbol |
| Symbol Description | Text | Full security name |
| Current Price | $#,##0.00 | Latest price per share |
| Quantity | Number | Total shares held |
| Date Acquired | Date | Earliest purchase date across accounts |
| Price Paid | $#,##0.00 | Weighted average cost per share |
| Total Cost | $#,##0.00 | Total amount invested |
| Market Value | $#,##0.00 | Current total value |
| Total Gain | $#,##0.00 | Unrealized gain/loss |
| Total Gain % | 0.00 | Return percentage (whole number, e.g. 21.47 = 21.47%) |
| Percent of Portfolio | 0.00 | Allocation weight (whole number) |

Followed by a summary block with totals, cash, and total portfolio value.

**Transactions sheet** — all transaction types with `Category` column.

**Cash Flows sheet** — Deposits highlighted green, Withdrawals red. Summary block with Total Deposited, Total Withdrawn, Net Cash Flow.

**Income sheet** — Dividends highlighted gold, Interest blue. Summary with per-type subtotals.

### `portfolio_analytics_*.xlsx`

**Analytics Summary** — all metrics as a two-column key/value table. Nested dicts are expanded with indented sub-rows.

**Holdings Detail** — full position data.

---

## Security

- API keys stored in `.env`, loaded via `python-dotenv`, excluded from version control
- OAuth tokens obtained at runtime, not persisted to disk
- Application uses production E-Trade endpoints (`dev=False`)
- **Read-only API calls only**: `list_accounts`, `get_account_portfolio`, `get_account_balance`, `list_transactions`, `list_transaction_details` — no trades or transfers

---

## Troubleshooting

### OAuth authorization fails

Verify `CONSUMER_KEY` and `CONSUMER_SECRET` in `.env` are correct. The verification code expires quickly — enter it promptly after authorizing.

### Empty or incomplete transaction data

The API limits each `list_transactions` call to a 90-day window. The code automatically chunks the date range, but if you see warnings like `failed to fetch transactions YYYY-MM-DD – YYYY-MM-DD`, the API may be throttling or returning errors for that window.

### Deposits not appearing / wrong classification

Every non-trade transaction prints a `[TXN]` debug line showing `type`, `description`, `amount`, and resolved `Category`. If a deposit is misclassified, share those lines — the description text drives classification for ambiguous types like `Transfer`.

### Suspicious withdrawals in Cash Flows

These are usually `Journal` entries — internal E-Trade bookkeeping for inter-account movements. `Journal` transactions with no external keywords in their description are classified as `Internal` and excluded. If a real deposit is being excluded, its `[TXN]` line will show `→ Internal`.

### Sector analysis shows most holdings as "Other"

Edit `sectors.json` in the `new work/` directory to add your symbols. No Python changes needed. The file is a simple dict of `{ "Sector Name": ["TICK1", "TICK2", ...] }`.

### Excel file won't open

Close any previously opened version of the file before re-running. XlsxWriter cannot append to existing files — it always creates new ones.

### Rate limiting

The script calls `list_transaction_details` once per trade in a loop with no backoff. Accounts with many trades in a single 89-day window may hit E-Trade's rate limits. If transactions are missing, wait a minute and re-run.

---

## Streamlit Dashboard

The project includes a browser-based dashboard at `new work/app.py`. It shows all the same data as the Excel output but live, in your browser, from anywhere.

### Installation

```bash
pip install streamlit plotly yfinance
# or install everything at once:
pip install -r "new work/requirements.txt"
```

### Configuration

Add the following to your root `.env` file (same one that holds `CONSUMER_KEY` and `CONSUMER_SECRET`):

```
APP_PASSWORD=choose_a_password_here
```

### Running locally

```bash
cd "new work"
streamlit run app.py
```

The app opens at `http://localhost:8501`.

### Running remotely (VPS or home server)

```bash
cd "new work"
streamlit run app.py --server.port 8501 --server.address 0.0.0.0
```

Then access it at `http://your-server-ip:8501`. Consider putting it behind a reverse proxy (nginx, Caddy) with HTTPS for production use.

### Connecting to E-Trade

1. Open the app and enter your password.
2. In the sidebar, click **Get Authorization URL** — this generates a fresh OAuth request.
3. Click **Authorize on E-Trade ↗** — this opens E-Trade's login page in a new tab.
4. After authorizing, E-Trade shows a verifier code. Paste it into the sidebar and click **Connect**.
5. Once connected (🟢 badge), set the date range and click **🔄 Refresh Data**.

The refresh fetches all holdings and transactions live and saves a snapshot to `data/portfolio_cache.json`. If you close the browser and reopen, the app loads from cache automatically (🟡 Cached badge) — no re-authentication needed until the cache is stale.

### Pages

| Page | Description |
|------|-------------|
| Home | Portfolio KPIs + top holdings snapshot |
| Holdings | Full holdings table + allocation & sector charts |
| Analytics | Performance, concentration, risk metrics + charts |
| Transactions | Filterable transaction history with pagination |
| Cash Flows & Income | Cash flow timeline + income breakdown by month/type |
| News & Earnings | Upcoming earnings calendar + per-stock news feed |
| Thesis Tracker | Per-holding investment thesis with status tracking |

### Thesis Tracker

The thesis tracker stores data in `data/thesis.json` (auto-created). For each holding you can record:

- **Status** — Unreviewed / On Track / Watch / At Risk / Broken / Exited
- **Thesis** — the core investment case
- **Entry Rationale** — why you bought it
- **Key Catalysts** — what to watch for
- **Target Price** — optional price target
- **Expected Hold Period** — e.g. "Long-term", "2–3 years"
- **Notes log** — append-only timestamped notes

The overview table shows all holdings with colour-coded status badges so you can quickly see which positions need attention.

### Data files (gitignored)

| File | Purpose |
|------|---------|
| `data/portfolio_cache.json` | Last-fetched portfolio snapshot |
| `data/thesis.json` | Thesis tracker data |

Both are excluded from git via `.gitignore`.
