# E-Trade Portfolio Consolidator - Developer & User Guide

## Table of Contents

- [Overview](#overview)
- [Architecture](#architecture)
- [Project Structure](#project-structure)
- [Prerequisites](#prerequisites)
- [Setup](#setup)
- [Usage](#usage)
- [Module Reference](#module-reference)
- [Data Flow](#data-flow)
- [Excel Output Format](#excel-output-format)
- [Analytics Engine](#analytics-engine)
- [Enhanced Versions](#enhanced-versions)
- [Security](#security)
- [Troubleshooting](#troubleshooting)

---

## Overview

A Python application that consolidates investment holdings across multiple E-Trade brokerage accounts into a single unified view. It connects to the E-Trade API via OAuth, aggregates positions by symbol, calculates performance metrics, runs portfolio analytics, and exports professionally formatted Excel reports.

### What It Does

- Authenticates with E-Trade via OAuth 2.0 (browser-based)
- Pulls holdings and cash balances from all active accounts
- Merges duplicate positions across accounts (same symbol held in multiple accounts)
- Calculates weighted average cost basis, gain/loss, and portfolio allocation
- Retrieves transaction history (buys and sells)
- Generates risk and concentration analytics
- Exports everything to formatted `.xlsx` files

---

## Architecture

The project has three implementation tiers:

| Tier | Location | Purpose |
|------|----------|---------|
| **Original** | `Portfolio Consolidator.py` | Single-file script, self-contained |
| **Modular** | `new work/` | Refactored into separate modules with analytics |
| **Enhanced** | `Modified/` | Advanced analysis with historical prices and charts |

The **modular** version (`new work/`) is the recommended entry point for development. The original script works identically but keeps everything in one file.

---

## Project Structure

```
E-Trade consolidator/
├── Portfolio Consolidator.py       # Original single-file implementation
├── Portfolio_consolidator.ipynb    # Initial prototyping notebook
├── .env                            # API credentials (gitignored)
├── .gitignore                      # Excludes secrets and outputs
├── todo.txt                        # Development notes
│
├── new work/                       # Modular refactor (recommended)
│   ├── main.py                     # Orchestrator entry point
│   ├── consolidator.py             # Core data retrieval and consolidation
│   └── analytics.py                # Portfolio analytics engine
│
├── Modified/                       # Enhanced analysis variants
│   ├── Portfolio_Analyzer_Historical.py    # Yahoo Finance historical analysis
│   ├── Portfolio_Consolidator_Enhanced.py  # Transaction-based analysis
│   ├── requirements.txt                    # Python dependencies
│   └── *.md                                # Various documentation files
│
└── outputs/                        # Generated Excel reports (gitignored)
    └── portfolio_consolidated_YYYY-MM-DD.xlsx
```

---

## Prerequisites

- **Python 3.10+** (developed on 3.12)
- **E-Trade developer account** with API access enabled
- **Consumer key and secret** from E-Trade's developer portal

### Dependencies

Install from the requirements file:

```bash
pip install -r Modified/requirements.txt
```

Or install individually:

```
pyetrade>=1.0.0          # E-Trade API wrapper
pandas>=1.3.0            # Data manipulation
numpy>=1.20.0            # Numerical operations
xlsxwriter>=3.0.0        # Excel file generation
python-dotenv>=0.19.0    # .env file loading
matplotlib>=3.5.0        # Charts (enhanced versions)
yfinance>=0.2.0          # Historical prices (enhanced versions)
python-dateutil>=2.8.0   # Date parsing
```

---

## Setup

### 1. Clone and enter the project

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
pip install -r Modified/requirements.txt
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

### Running the Original Script

```bash
python "Portfolio Consolidator.py"
```

### Running the Modular Version (Recommended)

```bash
cd "new work"
python main.py
```

### What Happens at Runtime

1. A browser window opens to E-Trade's OAuth authorization page
2. Log in and authorize the application
3. Copy the verification code from the browser
4. Paste the code into the terminal prompt
5. The script fetches all account data, consolidates it, and exports Excel files
6. A portfolio summary prints to the terminal

### Output Files

Two Excel files are generated in the working directory:

- `portfolio_consolidated_YYYY-MM-DD.xlsx` -- Holdings, summary, and transactions
- `portfolio_analytics_YYYY-MM-DD.xlsx` -- Analytics report (modular version only)

---

## Module Reference

### `consolidator.py` / `Portfolio Consolidator.py`

#### `authenticate_etrade() -> dict`

Runs OAuth 2.0 flow. Opens the browser for user authorization. Returns a dict with `consumer_key`, `consumer_secret`, `oauth_token`, and `oauth_token_secret`.

#### `fetch_active_accounts(auth_tokens) -> (DataFrame, ETradeAccounts)`

Calls `list_accounts()`, filters to `accountStatus == 'ACTIVE'`. Returns a DataFrame of active accounts and the `pyetrade.ETradeAccounts` object for subsequent calls.

#### `get_portfolio(accounts_obj, account_id_key) -> DataFrame`

Fetches the complete portfolio for one account. Extracts per-position data:

| Column | Type | Source |
|--------|------|--------|
| Symbol | str | `Product.symbol` |
| Symbol Description | str | `Complete.symbolDescription` |
| Current Price | float | `Complete.price` |
| Quantity | float | `position.quantity` |
| Date Acquired | datetime | `position.dateAcquired` (epoch ms) |
| Price Paid | float | `position.pricePaid` |
| Total Cost | float | `position.totalCost` |
| Market Value | float | `position.marketValue` |
| Total Gain | float | `position.totalGain` |
| Total Gain % | float | `position.totalGainPct` |
| Percent of Portfolio | float | `position.pctOfPortfolio` |

#### `get_cash_balance(accounts_obj, account_id_key) -> float`

Returns `netCash` from the account balance endpoint.

#### `consolidate_holdings(df, cash=0) -> DataFrame`

Groups positions by `Symbol` across all accounts. Aggregates:
- `Quantity`: sum
- `Total Cost`: sum
- `Market Value`: sum
- `Date Acquired`: earliest (min)
- `Current Price`: max (latest)
- `Price Paid`: recalculated as `Total Cost / Quantity` (weighted average)

Recalculates `Total Gain`, `Total Gain %`, and `Percent of Portfolio`. Appends a `CASH` row if `cash > 0`. Sorts by `Market Value` descending.

#### `portfolio_summary(consolidated_df, cash=0) -> dict`

Returns summary stats:
- Total stock count, market value, cost basis
- Total unrealized gain ($ and %)
- Cash amount and percentage
- Top 3 holdings by market value

#### `get_consolidated_transactions(accounts_obj, account_id_key, start_date, end_date) -> DataFrame`

Fetches buy/sell transactions for one account. Calls `list_transactions()` then `list_transaction_details()` for each. Both `start_date` and `end_date` must be `datetime.date` objects.

Returns columns: `Date`, `Security Name`, `Quantity`, `Price`, `Total Value`, `Transaction Type`.

#### `get_all_consolidated_transactions(accounts_obj, active_accounts, start_date, end_date) -> DataFrame`

Loops over all active accounts, concatenates transaction DataFrames, sorts by date descending.

#### `export_to_excel(consolidated_df, transactions_df=None, filename=None, summary_spacing=3)`

Writes a formatted `.xlsx` file using XlsxWriter:
- **Holdings sheet**: Position data with currency/percentage formatting
- **Summary section**: Below the holdings table with portfolio totals
- **Transactions sheet**: Included if `transactions_df` is provided
- Auto-adjusts column widths

Default filename: `portfolio_consolidated_YYYY-MM-DD.xlsx`

---

### `analytics.py`

#### Class: `PortfolioAnalytics`

```python
analytics = PortfolioAnalytics(consolidated_df, transactions_df=None, risk_free_rate=0.04)
```

Separates holdings from the CASH row internally. All methods return plain dicts.

#### `concentration_analysis() -> dict`

Measures portfolio diversification:
- **HHI Score**: Herfindahl-Hirschman Index (sum of squared allocation percentages). Range 0-10000. Below 1500 = well diversified.
- **Effective Positions**: `10000 / HHI`. Represents how many equally-weighted positions the portfolio behaves like.
- **Top N Holdings %**: Concentration of top 3, 5, and 10 positions.
- **Diversification Score**: Ratio of actual to effective positions.

#### `sector_analysis() -> dict`

Maps symbols to predefined sector groups (Tech, Finance, Healthcare, Consumer, Energy, Utilities, ETFs). Returns dollar and percentage allocation per sector. Unmatched symbols go to "Other".

Note: Sector mappings are hardcoded. You may need to update them if your portfolio contains symbols not in the lists.

#### `performance_metrics() -> dict`

- Total return ($ and %)
- Unrealized vs realized gains (realized calculated from sell transactions)
- Total cost basis and current market value
- Average cost per position

#### `risk_metrics() -> dict`

- **Volatility**: Standard deviation of position gain percentages
- **Downside Deviation**: Std dev of only negative returns
- **Win Rate**: Percentage of positions with positive gains
- **Best/Worst Performer**: Max and min gain %
- **Sharpe Ratio**: `(avg_return - risk_free_rate) / volatility`
- **Sortino Ratio**: `(avg_return - risk_free_rate) / downside_deviation`

Note: These ratios are simplified -- calculated from position-level gain percentages rather than time-series returns.

#### `liquidity_analysis() -> dict`

Evaluates cash position. Returns cash balance, percentage, and a liquidity score (65-85 scale based on cash percentage thresholds).

#### `transaction_analysis() -> dict`

Counts buys/sells, total amounts, portfolio turnover, and average transaction size. Returns a message if no transaction data is available.

#### `holdings_quality_analysis() -> dict`

Categorizes positions into gain/loss buckets:
- Highly Profitable (>50%), Profitable (0-50%), Breakeven
- Small Loss (-10% to 0%), Moderate Loss (-50% to -10%), Major Loss (<-50%)

Identifies best and worst performers.

#### `generate_full_report() -> dict`

Runs all analysis methods and returns a combined dictionary.

#### `export_analytics_to_excel(consolidated_df, analytics_report, filename=None)`

Writes the analytics report to a formatted Excel file with an "Analytics Summary" sheet and a "Holdings Detail" sheet.

---

## Data Flow

```
E-Trade API (OAuth 2.0)
    |
    v
fetch_active_accounts() --> list of account IDs
    |
    v
For each account:
    get_portfolio()      --> raw positions DataFrame
    get_cash_balance()   --> float
    |
    v
pd.concat(all accounts) --> combined DataFrame
    |
    v
consolidate_holdings()  --> grouped by Symbol, gains recalculated
    |
    v
portfolio_summary()     --> dict of summary stats
    |
    v
get_all_consolidated_transactions() --> transaction DataFrame
    |
    v
export_to_excel()       --> portfolio_consolidated_YYYY-MM-DD.xlsx
    |
    v
PortfolioAnalytics()    --> analytics object
    |
    v
generate_full_report()  --> dict of all analytics
    |
    v
export_analytics_to_excel() --> portfolio_analytics_YYYY-MM-DD.xlsx
```

---

## Excel Output Format

### Holdings Sheet

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
| Total Gain % | 0.00% | Return percentage |
| Percent of Portfolio | 0.00% | Allocation weight |

### Summary Section (below holdings)

Written as key-value pairs: Total Stocks, Total Quantity, Total Cost, Total Market Value, Total Gain/Loss, Total Gain/Loss %, Cash, Total Portfolio Value, Cash %.

### Transactions Sheet (optional)

| Column | Format |
|--------|--------|
| Date | YYYY-MM-DD |
| Security Name | Text |
| Quantity | Number |
| Price | $#,##0.00 |
| Total Value | $#,##0.00 |
| Transaction Type | Bought/Sold |

---

## Enhanced Versions

The `Modified/` directory contains two advanced scripts:

### Portfolio_Analyzer_Historical.py

Uses **Yahoo Finance** (`yfinance`) to fetch historical price data and reconstruct daily/monthly portfolio values. This approach is more reliable than the transaction API because it:

- Does not depend on E-Trade's transaction endpoint
- Calculates true daily/monthly portfolio values
- Provides time-series risk metrics (drawdowns, rolling volatility)
- Generates multi-panel matplotlib charts

Best for: Ongoing performance monitoring and historical trend analysis.

### Portfolio_Consolidator_Enhanced.py

An enhanced transaction-based analysis that:

- Fixes a date handling bug where `str` objects were passed instead of `datetime.date`
- Supports the **Modified Dietz method** for returns calculation
- Accounts for deposits and withdrawals when calculating performance
- Separates investment gains from contribution effects

Best for: Precise return attribution when you need to distinguish market gains from cash inflows/outflows.

### Which to Use

| Need | Use |
|------|-----|
| Basic monthly snapshot | `Portfolio Consolidator.py` or `new work/main.py` |
| Analytics and risk metrics | `new work/main.py` |
| Historical trends and charts | `Modified/Portfolio_Analyzer_Historical.py` |
| Deposit-adjusted returns | `Modified/Portfolio_Consolidator_Enhanced.py` |

---

## Security

### Credentials

- API keys are stored in `.env` and loaded via `python-dotenv`
- `.env` and `Keys.txt` are excluded from version control via `.gitignore`
- OAuth tokens are obtained at runtime and not persisted to disk
- The application uses production E-Trade endpoints (`dev=False`)

### API Access Scope

The application only performs **read** operations:
- `list_accounts` -- account enumeration
- `get_account_portfolio` -- holdings data
- `get_account_balance` -- cash balances
- `list_transactions` / `list_transaction_details` -- transaction history

No trades, transfers, or account modifications are made.

---

## Troubleshooting

### OAuth authorization fails

- Verify your `CONSUMER_KEY` and `CONSUMER_SECRET` in `.env` are correct
- Ensure your E-Trade API application is approved for production access
- The verification code expires quickly -- enter it promptly after authorization

### "start_date must be a datetime.date object"

Pass `datetime.date` objects, not strings:

```python
import datetime
start = datetime.date(2025, 1, 1)
end = datetime.date.today()
```

### Empty transaction data

- The transaction API only returns `Bought` and `Sold` types. Dividends, interest, and transfers are filtered out.
- Check that the date range covers a period with actual trades.

### Missing positions after consolidation

- Only accounts with `accountStatus == 'ACTIVE'` are included
- Positions with zero quantity may not appear in the API response

### Excel file won't open

- Close any previously opened version of the file before re-running
- XlsxWriter cannot append to existing files -- it always creates new ones

### Sector analysis shows most holdings as "Other"

The sector mappings in `analytics.py` are hardcoded for common large-cap symbols. Update the `sector_groups` dictionary in `sector_analysis()` to include your specific tickers.

### Rate limiting

The E-Trade API has rate limits. If fetching transaction details for many transactions, you may hit throttling. The script does not currently implement retry logic or rate-limit backoff.
