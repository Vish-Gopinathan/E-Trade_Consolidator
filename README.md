# E\*TRADE Portfolio Consolidator

A private dashboard that pulls holdings, balances and transaction history from
every active E\*TRADE account, merges them into one view, and answers the
questions a brokerage statement does not: how much of the gain is real
performance rather than money you added, where the portfolio is concentrated,
what it was worth on any past day, and whether selling was the right call.

Runs locally against your own account. No portfolio data is ever sent anywhere.

---

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env    # then fill in your E*TRADE keys and an app password
streamlit run app.py
```

Get E\*TRADE API keys from [developer.etrade.com](https://developer.etrade.com).
In the app, open **E\*TRADE Connection** in the sidebar, follow the authorization
link, paste the verifier code back, and press **Refresh data**.

To see the app without an account:

```bash
streamlit run demo.py
```

For a scripted export instead of the dashboard:

```bash
python cli.py --start 2024-01-01 --output-dir outputs/
```

---

## What's in it

| Section | Page | What it answers |
|---|---|---|
| Portfolio | Overview | What is it worth, and where did the numbers come from |
| | Holdings | Every position, allocation, gain and loss |
| | Value Over Time | What it was worth on any past day, rebuilt from transactions |
| | Performance | Return with contributions factored out; concentration; sector mix |
| Money | Cash Flows & Income | Deposits, withdrawals, dividends — and the transfer review |
| | Transactions | The full history, filterable, with how each row was classified |
| Research | Earnings | Next report date and EPS estimate per holding; how recent ones landed |
| | Thesis Tracker | Why you own each position, and whether that still holds |
| | What-If: Hold | What sold positions would be worth today |

---

## Deployed on Streamlit Cloud?

Set **Main file path** to `app.py` in the app's settings. It used to be
`new work/app.py`; Cloud keeps that path in its own settings rather than the repo,
so an older deployment silently keeps serving the previous build. Details and the
cold-start caveats are in [docs/GUIDE.md](docs/GUIDE.md#deploying-to-streamlit-cloud).

## Documentation

- **[docs/GUIDE.md](docs/GUIDE.md)** — setup, every page, how money is classified,
  what each metric means *and what it does not*, data storage, troubleshooting.
- **[CLAUDE.md](CLAUDE.md)** — architecture and invariants, for AI agents working
  on this codebase.

## Layout

```
app.py                 Dashboard entry point — navigation and the refresh flow
cli.py                 Batch Excel export
demo.py                Dashboard driven by fictional data
portfolio/             Data and logic. Never imports streamlit.
  etrade.py              API access and holdings consolidation
  classify.py            What each transaction means (two passes)
  analytics.py           The report
  schema.py              Report key contract — keys are declared here only
  excel.py               Workbook generation
  history.py             Daily value reconstruction
  market.py, symbols.py  yfinance prices, splits, ticker resolution
  storage/               Local JSON stores: cache, prices, earnings, thesis, accounts
ui/                    Streamlit pages, chrome and chart theme
config/sectors.json    Sector mappings — edit freely
data/                  Runtime state. Gitignored: this is real financial data.
tests/                 pytest suite
```

## Tests

```bash
pip install -r requirements-dev.txt
pytest
```

## Privacy

`data/` holds real holdings, cost basis and transaction history, and is
gitignored. Nothing is written to any remote. Snapshots are local files you
export and import yourself — an earlier version pushed them to this repository
through the GitHub API, which is public.

The password gate is a speed bump for a local app, not authentication. Do not
expose this to the internet without a real authenticating proxy in front of it;
see the Security section of the guide.
