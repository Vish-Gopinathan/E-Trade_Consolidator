# User & Developer Guide

- [Setup](#setup)
- [Running it](#running-it)
- [The pages](#the-pages)
- [How money is classified](#how-money-is-classified)
- [What each metric means](#what-each-metric-means)
- [Data storage and privacy](#data-storage-and-privacy)
- [Deploying to Streamlit Cloud](#deploying-to-streamlit-cloud)
- [Security](#security)
- [Troubleshooting](#troubleshooting)
- [Developing](#developing)

---

## Setup

Python 3.10 or newer.

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

### Credentials

Fill in `.env` (gitignored):

```
CONSUMER_KEY=...          # from developer.etrade.com
CONSUMER_SECRET=...
APP_PASSWORD_HASH=...     # dashboard login
GUEST_PASSWORD=           # optional, read-only; leave blank to disable
```

Generate the password hash:

```bash
python -c "import hashlib,secrets;s=secrets.token_hex(16);p=input('password: ');print(f'{s}\${hashlib.sha256((s+p).encode()).hexdigest()}')"
```

A plain `APP_PASSWORD=` still works, so an existing install keeps running, but the
hash keeps the password out of the environment in readable form.

On Streamlit Cloud, put the same keys in the app's secrets instead of `.env`.

### Two dependencies that are not optional

Both are already pinned in `requirements.txt`; they are called out because
removing either produces a confusing failure rather than an error.

- **`lxml`** — yfinance parses the earnings-dates table with `pandas.read_html`,
  which needs a parser backend. Without it, `get_earnings_dates()` raises
  `ImportError`, the exception is caught, and every symbol quietly stores empty
  earnings. Symptom: no EPS anywhere, no history, no error.
- **`starlette<1.4`** — starlette 1.4 changed `GZipResponder.__init__`, which
  Streamlit subclasses. Every page request returns 500. Remove the ceiling once
  Streamlit adapts.

---

## Running it

**Dashboard** — `streamlit run app.py`

Log in, open **E\*TRADE Connection** in the sidebar, press **Get authorization
URL**, authorize in the browser tab that opens, paste the verifier code back, then
press **Refresh data**.

The default date range goes back three years, which is as far as E\*TRADE will
answer. It documents a two-year limit but has been observed returning more, so the
app asks for three and clamps anything earlier — and says so when it does, rather
than silently returning less than you asked for.

A refresh takes roughly ten seconds. If it takes minutes, see
[Troubleshooting](#troubleshooting).

**Demo** — `streamlit run demo.py`

The same pages against fictional holdings with live prices. Thesis notes go to a
separate file, so the demo cannot overwrite real ones.

**CLI** — `python cli.py [--start YYYY-MM-DD] [--end YYYY-MM-DD] [--output-dir DIR]`

Writes `portfolio_consolidated_<date>.xlsx` and `portfolio_analytics_<date>.xlsx`,
and prints a summary. `--verbose` logs every classification decision.

---

## The pages

### Overview
Headline numbers with their provenance stated: live, cached, or a saved snapshot,
and when. **Per-account balances** expands to show each account's Total Account
Value and cash so you can check them against the E\*TRADE website directly. If
positions plus cash disagree with what E\*TRADE reports by more than 0.5%, the
page says so rather than picking one.

### Holdings
Every position, sorted by value, with allocation and sector charts. Cash is
summarised separately — it is not a holding.

### Value Over Time
End-of-day value for every trading day, rebuilt from transaction history and daily
closes. Share counts are walked **backwards** from today's holdings rather than
forwards from an assumed-empty account, so today is exact by construction and any
gap in the transaction feed surfaces as a residual share count in the earliest
dates. **Data quality** reports those residuals rather than absorbing them.

### Performance
Returns, concentration, sector mix, and the spread of position outcomes. See
[metrics](#what-each-metric-means).

### Cash Flows & Income
Deposits and withdrawals, dividends and interest, and **Transfer Review** — the
transfers the app could not classify alone. See [classification](#how-money-is-classified).

### Transactions
The full history with filters. Internal transfers are hidden by default (they net
to zero and would bury the trades) and the Category filter surfaces them. An
expander explains how every transfer was classified and why.

### Earnings
Next report date and consensus EPS estimate per holding, and how recent quarters
landed. Past quarters are fetched once and kept; upcoming dates are re-checked
weekly. First load takes a few seconds for a typical portfolio.

The **Reported** column is labelled honestly. The EPS source is indexed by fiscal
*quarter end*, not the announcement date, and the two can be a month apart. Where
the real announcement date is known the column shows it; where it is not, the
quarter end is shown, the page says so, and the next-day price move is left blank
rather than measured against the wrong day.

ETFs, trusts and funds have no earnings. Seeing them listed under "no earnings
data" is correct, not a failure.

### Thesis Tracker
Free-text thesis, catalysts, target price and dated notes per position, with a
status you set.

### What-If: Hold
What sold positions would be worth today, split-adjusted. Reported as **decision
value** — proceeds minus value if held — so positive means selling was the right
call and is green. Percent leads because a dollar difference mostly measures how
large the position was.

---

## How money is classified

Every non-trade transaction gets exactly one category:

| Category | Meaning | Counts as a cash flow? |
|---|---|---|
| `Deposit` | External money in (ACH, wire, IRA contribution) | Yes |
| `Withdrawal` | External money out | Yes |
| `Income` | Dividends, interest, fees — generated inside the account | No |
| `Internal` | Movement between your own accounts | No |
| `Trade` | Security buy or sell | No |
| `Other` | Unrecognised — shown, never silently included | No |

This matters because deposit-adjusted return subtracts external cash flows. Count
a dividend as a deposit and it disappears from your return; count an inter-account
transfer as a withdrawal and your return is overstated.

### Why transfers need two passes

E\*TRADE describes a move between your own accounts and a real withdrawal almost
identically:

```
TRANSFER TO XXXXX1344 REFID:132627026906     -111.38   your other E*TRADE account
TRANSFER FROM XXXXX7449 REFID:132627026906   +111.38   ... the matching leg
TRANSFER TO XXXXX1607 REFID:128218358906     -500.00   money that actually left
```

No single row can tell those apart, so classification runs twice.

**Pass 1** settles everything one row can prove. `ACH DEPOSIT`, `Contribution`,
`Dividend`, `Interest Income` are unambiguous. So is an in-kind security move
(`NVDA TFR TO ACCT XXXXX-7449-0`, $0) — no cash changed hands. Anything with a
counterparty account number is parked.

**Pass 2** runs once over **all accounts combined** and settles the parked rows:

1. **Reference pairing.** Two legs sharing a REFID, one positive and one negative,
   summing to zero, are two halves of one move. Both become `Internal`. This is
   evidence, not inference, so it wins outright.
2. **Counterparty lookup.** Otherwise the last four digits are checked against your
   own accounts and against the account map you build in Transfer Review.
3. **External by default.** If the far side were one of your accounts, its matching
   leg would be in the data and step 1 would have found it. So an unrecognised
   counterparty is treated as external — and flagged **Needs Review** so the app
   asks rather than assumes.

### Transfer Review

Cash Flows & Income → **Transfer Review** lists every counterparty account still
unresolved, with how many transfers and how much money hinge on the answer. Tag
each as yours or external once; the answer is stored in `data/account_map.json`
and applied on the next refresh.

Until you tag one, its transfers count as external cash flows, and Overview says
so. That is the conservative direction — it understates return rather than
overstating it.

### If a category looks wrong

Transactions → **How transfers were classified** shows the reason for every
decision. `Other` rows are excluded from all totals and listed so you can see what
was skipped.

---

## What each metric means

Percentages are whole numbers throughout (30.11 means 30.11%).

### Unrealised gain
Current position value minus what was paid for it. **Cash is excluded from both
sides** — it has no cost basis, so including it in value alone would report
uninvested cash as investment gain.

### Deposit-adjusted return

Modified Dietz. Weights each deposit and withdrawal by how long it was invested,
so contributions do not read as performance:

```
R = (EMV − BMV − CF) / (BMV + Σ CFᵢ × Wᵢ)
```

**The assumption that matters.** BMV — portfolio value at the start of the range —
defaults to zero, which is correct when the transaction range covers the account's
whole life. That is why the default start date is 2000. **If your range does not
reach account opening, this overstates the return**, and the figure on screen says
so beneath it.

Do not compare it to a fund's published return: it is money-weighted (it reflects
your timing), while fund returns are time-weighted (they do not).

### Concentration

**HHI** is the sum of squared position weights, 0–10000. Under 1500 is well
diversified, above 2500 concentrated. **Effective positions** (10000 ÷ HHI) is how
many equally weighted positions would give the same concentration — 20 holdings
with an effective count of 5 means five of them are doing the work.

Weights are shares of **invested value, excluding cash**. Concentration is a
question about how the invested money is spread.

### Spread of returns
Standard deviation of position lifetime gains. **Descriptive only.** These are not
periodic returns — a position held four years and one held a week contribute
equally, and the number has no time unit.

### What was removed, and why

Earlier versions reported **Sharpe and Sortino ratios** computed from position
lifetime gains. Those ratios are defined over a time series of periodic returns; a
cross-section of "how much is each position up since I bought it" is not one. The
resulting number could not be compared to a published Sharpe ratio for anything.
It was removed rather than displayed with a caveat. The raw dispersion remains,
labelled for what it is.

### Portfolio history
Value is rebuilt on **today's share basis**: historical closes from the data source
are split-adjusted, so share counts must be too. A trade of 10 shares before a 10:1
split counts as 100 shares today. The raw count held at the time is shown
separately, for display only.

Prices are split-adjusted but **not** dividend-adjusted. That is intentional: a
dividend was paid out as cash and is picked up by the cash reconstruction, so
adjusting the price for it as well would count it twice.

---

## Data storage and privacy

Everything lives in `data/`, which is gitignored.

| File | Contents |
|---|---|
| `portfolio_cache.json` | Last refresh: holdings, transactions, report |
| `month_end_snapshot.json` | Saved point-in-time copy; what guests see |
| `price_store.json` | Daily closes and splits, cached per symbol |
| `earnings_store.json` | Past quarters and next report dates |
| `thesis.json` / `thesis_demo.json` | Your notes / demo fixtures |
| `symbol_map.json` | Manual ticker mappings you confirmed |
| `account_map.json` | Transfer counterparties you tagged |

**Nothing is written to any remote.** An earlier version pushed snapshots and the
earnings store to this GitHub repository through the Contents API — which bypasses
`.gitignore`, into a repository that is public. That path is gone. Moving a
snapshot between machines is an explicit download-then-upload in the Snapshot
panel.

Deleting anything in `data/` is safe: caches rebuild on the next refresh. Only
`thesis.json` and `account_map.json` hold decisions you cannot regenerate — back
those up.

---

## Security

**The password gate is a speed bump, not authentication.** The failed-attempt
counter and lockout live in Streamlit session state, so they slow down someone
guessing in one browser tab but reset on reload and stop nothing scripted.

For local use that is fine. **Do not expose this to the internet without a real
authenticating proxy in front of it** — Tailscale, Cloudflare Access, an OAuth
proxy, or an SSH tunnel.

Other notes:

- E\*TRADE OAuth tokens live in session state only and are never written to disk.
  They expire when the session ends.
- Guest mode is read-only: no refresh, no exports, no tagging, no thesis edits.
- Never commit `.env` or anything in `data/`. Both are gitignored; the Contents
  API bypasses gitignore, which is why remote persistence was removed.
- Transaction descriptions are logged at DEBUG only. `--verbose` on the CLI prints
  them; do not use it where the terminal is shared.

---

## Deploying to Streamlit Cloud

### The entry point moved

The restructure replaced `new work/app.py` with `app.py` at the repository root.
**Streamlit Cloud stores the main file path in its own settings, not in the
repo**, so a deployment created before that still points at a file that no longer
exists. It does not fall back — it keeps serving the last build that worked, which
means you are testing old code while the repo has new code.

Fix it in the app's settings: *Manage app → Settings → Main file path* → `app.py`,
then reboot. Nothing in the repository can do this for you.

### Secrets

Cloud has no `.env`. Put the same keys in *Settings → Secrets*:

```toml
CONSUMER_KEY = "..."
CONSUMER_SECRET = "..."
APP_PASSWORD_HASH = "..."
```

### Cold starts refetch everything

Cloud gives every container a fresh filesystem, and `data/` is gitignored. So on
each restart — and Cloud restarts apps that go idle — the app starts with **no
cache at all**:

| Missing | Consequence |
|---|---|
| `portfolio_cache.json` | Opens with no data; needs an E\*TRADE refresh |
| `earnings_store.json` | Earnings refetches every symbol (seconds) |
| `price_store.json` | Value Over Time rebuilds all daily prices (slower) |
| `month_end_snapshot.json` | Guests see nothing |

This is the deliberate cost of not writing portfolio data to the repository, which
is public. The replacement is manual: **Save as snapshot → Export snapshot file**
while the container is warm, then **Restore** after a restart. Keep that file
somewhere you control.

If you would rather not do that dance, run the app locally — `data/` persists
there and cold starts are instant.

### Round trips cost more from Cloud

Every E\*TRADE call goes from Streamlit's data centre rather than your machine, so
per-request latency is higher and request count matters more. A refresh is about
26 requests; if it is taking minutes, the deployment is running pre-fix code — see
the entry point above.

### Before you expose this publicly

A Cloud app is reachable by anyone with the URL. The password gate is a speed bump
— its lockout resets on reload — so it is not adequate on its own for a page that
displays your holdings and cost basis. Use *Settings → Sharing* to restrict
viewers to your own account, or keep the app private and run it locally. See
[Security](#security).

---

## Troubleshooting

**Cash shows $0.**
Fixed, but if it recurs: `Computed.accountBalance` is a cash-side figure, not
total account value — that is `Computed.RealTimeValues.totalAccountValue`. Any
code deriving cash by subtracting positions from `accountBalance` will produce a
negative number. Check the per-account expander on Overview against the E\*TRADE
website.

**Withdrawals total $0 but you know you withdrew money.**
A transfer type is missing from `TRANSFER_TYPES` in `portfolio/classify.py`, or the
counterparty is tagged internal in `data/account_map.json`. Transactions →
Category → `Other` shows anything unclassified.

**Earnings are empty and EPS never populates.**
`lxml` is missing, or the symbol is an ETF. Check the errors expander at the
bottom of the Earnings page. `pip install -r requirements.txt` fixes the first.

**The dashboard 500s on every request.**
starlette 1.4+. `pip install -r requirements.txt` restores the pin.

**Excel download fails.**
The error and traceback are shown in an expander under the button. If the
workbook builds but is stale, refresh — it is cached per dataset.

**"No data loaded" on a fresh machine.**
No cache and no snapshot. Connect to E\*TRADE and refresh, or import a snapshot
file in the Snapshot panel.

**A metric reads as an em dash.**
The report key is missing from the contract. Run `pytest tests/test_schema.py` —
it fails on exactly this.

**Transaction fetch is slow.**
First check *what code is actually running*. Streamlit reloads page files but not
the modules they import, so a server started before a change is still serving the
old code — restart it. On Streamlit Cloud, check the main file path is `app.py`
(see [Deploying](#deploying-to-streamlit-cloud)); a stale path keeps the previous
build alive.

The refresh prints per-phase timings, so the panel tells you which step is slow.
A refresh should be about ten seconds for two accounts. Minutes means one of the
three things that used to make it slow has come back:

1. **Asking for more history than exists.** Every 89-day window is one round trip
   whether or not it holds anything, so a 2000–2026 request was 108 windows per
   account, 99 of them necessarily empty. `clamp_start_date` caps the request at
   `MAX_HISTORY_DAYS`.
2. **A detail call per trade.** The transaction list already carries a
   `brokerage` block with quantity, price and symbol; fetching each trade's
   detail anyway added one round trip per trade — 215 on a real account. The
   detail call is now only a fallback for rows the list leaves incomplete.
3. **Paging.** Each response holds at most 50 rows, so busy windows need several
   requests. This one is unavoidable, and skipping it loses transactions.

Together those took a refresh from 431 requests to 26.

---

## Developing

```bash
pip install -r requirements-dev.txt
pytest
```

### The one architectural rule

`portfolio/` holds data and logic and **must not import streamlit**. `ui/` holds
everything that renders and may import `portfolio/`. This is what makes the logic
testable without a Streamlit runtime.

### Report keys

Every key in the analytics report is a constant in `portfolio/schema.py`. Never
spell one inline. `tests/test_schema.py` fails the build otherwise — this is the
guard for the drift that once made the whole Performance page render em dashes
while demo mode looked fine.

### Adding a page

Create `ui/yourpage.py`, start it with `page_header(...)`, and add an `st.Page`
entry to the navigation dict in **both** `app.py` and `demo.py`. There are no
filename prefixes; ordering comes from those dicts.

### Adding a sector

Edit `config/sectors.json`. Unmapped symbols appear as "Unclassified", and the
Performance page tells you how much weight is unplaced.

### Changing classification

`portfolio/classify.py`, and add a fixture to `tests/test_classify.py` using the
real description wording. Every failure this code has had came from the exact
text — `Online Transfer` missing from a list, a fraction not scaled to a
percentage, a $0 in-kind row treated as cash.
