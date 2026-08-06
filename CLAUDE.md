# CLAUDE.md

Guidance for AI agents working in this repository. User-facing documentation is
[README.md](README.md) and [docs/GUIDE.md](docs/GUIDE.md) — read the guide's
"How money is classified" and "What each metric means" sections before changing
anything in `portfolio/`.

## What this is

A private Streamlit dashboard over one person's real E\*TRADE accounts. The
numbers on screen are used to make actual financial decisions, so a metric that is
subtly wrong is worse than one that is absent. When a figure cannot be computed
honestly, the app shows nothing and says why — it does not approximate silently.

## Layout

```
app.py         Dashboard entry point: auth, sidebar, st.navigation
cli.py         Batch Excel export
demo.py        Same pages, fictional data
portfolio/     Data and logic
ui/            Streamlit pages, chrome, chart theme
config/        sectors.json — user-editable
data/          Runtime state. Gitignored. Real financial data.
tests/         pytest
```

`archive/` and `.claude/worktrees/` are not live code. Everything that runs is in
the tree above.

## Rules

### 1. `portfolio/` must not import streamlit

`portfolio/` is the logic layer; `ui/` renders and may import `portfolio/`. This
keeps the logic testable without a Streamlit runtime. The one exception is a
`try: import streamlit` inside a function to read secrets or session state, which
must degrade cleanly when there is no runtime.

### 2. Report keys are declared once, in `portfolio/schema.py`

Never write a report key as a string literal. `tests/test_schema.py` fails the
build if a key escapes the contract.

This rule exists because it was broken: the pages read `'Simple Return (%)'` while
analytics emitted `'Total Return (%)'`. Every lookup missed, nothing raised, and
the whole Performance page rendered em dashes — while demo mode looked correct,
because the demo fixtures happened to use the page's spelling. Demo data now runs
through the real engine for the same reason.

### 3. Never write portfolio data to a remote

The GitHub repository is **public**. An earlier version pushed snapshots, the
earnings store and the price store there through the Contents API, which bypasses
`.gitignore`. Holdings, cost basis and full transaction history would have been
world-readable. All persistence is local disk. Snapshots move between machines by
explicit user download/upload.

### 4. Do not swallow exceptions

`except Exception: pass` around cache and snapshot writes made a failed save look
identical to a successful one, so data vanished on restart. Log it and surface it.

### 5. Fix the data, not the metric

When a demo fixture produces a nonsense figure, the fixture is usually wrong. The
demo once claimed $1.33M of deposits against a $705k portfolio, and
deposit-adjusted return correctly reported −76%. Fixing the fixture was right;
massaging the formula would have hidden a real failure mode.

## API gotchas

### E\*TRADE (`portfolio/etrade.py`)

- `Computed.accountBalance` is a **cash-side** figure, not total account value.
  Total value is `Computed.RealTimeValues.totalAccountValue`. Conflating them made
  cash read as $0 — the residual went negative and was clamped.
- Cash is `Computed.netCash`. Never derive it by subtraction.
- `list_transactions` rejects ranges over 90 days. `_date_chunks` splits into
  89-day windows.
- Each trade needs a second call for quantity and price. That is why long fetches
  are slow; it is not a bug to optimise away without changing what is fetched.
- Sells come back with negative quantity.

### yfinance (`portfolio/storage/earnings.py`, `prices.py`, `portfolio/market.py`)

- `get_earnings_dates()` needs **lxml** and raises `ImportError` without it. That
  error was caught and every symbol stored empty earnings. The JSON endpoints
  (`earnings_history`, `calendar`) are primary; `get_earnings_dates` is enrichment.
- `earnings_history.surprisePercent` is a **fraction**: 0.0410 means +4.10%.
- `earnings_history` is indexed by fiscal **quarter end**, not announcement date —
  they can be a month apart. Rows carry `date_is_report_date`; where it is false,
  the price reaction is left blank rather than measured against the wrong day.
- `calendar['Earnings Average']` is the consensus EPS estimate. The old code
  hardcoded the estimate to `None`, so that column was always empty.
- `auto_adjust=False` returns prices adjusted for **splits but not dividends**,
  despite the flag's name. Both halves matter — see the docstring in `prices.py`.
- ETFs return 404 for fundamentals. Expected, not an error.

### Streamlit

- `st.set_page_config` belongs only in the entry script (`app.py`, `demo.py`).
- Never put `st.download_button` inside `if st.button(...)`: the next rerun
  destroys it before it can be used. Build the payload eagerly, cache it on
  `fetched_at`, render the download button unconditionally.
- `@st.cache_data` cannot hash DataFrames. Key on a scalar like `fetched_at` and
  read the frame from session state.
- Adding a symbol to a module that pages import requires a **server restart** —
  the file watcher reloads page modules but not their imports.
- `st.metric`'s delta arrow always points up for a text delta. Do not use a text
  delta to describe a negative result.

## Invariants worth preserving

**Classification** (`portfolio/classify.py`) runs two passes. Pass 1 sees one row;
pass 2 sees all accounts combined and is the only place inter-account transfers can
be recognised, because the two legs live in different accounts. `PENDING_TRANSFER`
must never survive pass 2. An unmatched counterparty defaults to **external** — if
the far side were yours, its matching leg would be in the data — and is flagged
`Needs Review` rather than assumed.

**History** (`portfolio/history.py`) walks share counts backwards from today's
holdings, so today is exact by construction and any gap in the feed surfaces as a
residual in the earliest dates. Do not switch to a forward walk: it would put the
error on the most recent, most scrutinised numbers. Everything is on today's split
basis.

**Charts** (`ui/theme.py`) use one validated palette. The green/red diverging pair
measures ΔE 7.2 for protanopia, inside the band that is only permissible with a
second, non-colour encoding — so those charts must also carry position against a
zero line, direct labels, and a table of the same numbers. Categorical hues are
assigned by identity, never by position: passing a sequence shorter than the label
list makes plotly recycle, which once gave a Cash slice the same blue as the
largest holding.

## Working here

```bash
pytest                          # 58 tests, ~4s
streamlit run demo.py           # exercise the UI without real credentials
python cli.py --help
```

Verify UI changes in demo mode rather than asking the user to log in — it needs no
credentials and covers every page.

Use real E\*TRADE description wording in classification fixtures. Every failure
this code has had came from the exact text.
