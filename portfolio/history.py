"""
Reconstruct end-of-day portfolio value for every day in a date range.

E*TRADE only reports the portfolio as it stands right now. Transaction history
plus daily closing prices are enough to rebuild the past, and this module does it
by walking **backwards** from today's known holdings rather than forwards from an
assumed-empty account:

    shares(d-1) = (shares(d) - net_shares_traded_on_d) / split_ratio_on_d

Anchoring on today matters. A forward walk from zero silently inherits every gap
in the transaction feed — a position transferred in from another broker, a
missing chunk of API results — and the error lands on the most recent, most
scrutinised numbers. Walking backwards makes today exact by construction and
pushes any inconsistency into the distant past, where it shows up as a non-zero
share count before the account existed. That residual is reported as a
diagnostic rather than hidden (see ``residual_shares``).

Everything is computed on **today's share basis**. Yahoo's historical closes are
split-adjusted (see lib/price_store.py), so share counts have to be too: a trade
of 10 raw shares before a 10:1 split counts as 100 shares today, and pairing
those 100 with the split-adjusted $122 close reproduces the same $1,224 the
position was actually worth. ``shares`` therefore holds today-equivalent counts;
``shares_actual`` holds the raw counts held at the time, for display only.
"""

from dataclasses import dataclass, field
from datetime import date

import numpy as np
import pandas as pd

CASH_SYMBOL = 'CASH'


@dataclass
class HistoryResult:
    shares: pd.DataFrame          # dates × symbols — today-equivalent share count EOD
    shares_actual: pd.DataFrame   # dates × symbols — raw count held at the time
    prices: pd.DataFrame          # dates × symbols — split-adjusted close, ffilled
    values: pd.DataFrame          # dates × symbols — market value EOD
    cash: pd.Series               # dates — reconstructed cash balance
    positions_total: pd.Series    # dates — sum of position values
    total: pd.Series              # dates — positions + cash
    contributions: pd.Series      # dates — cumulative net external deposits
    diagnostics: dict = field(default_factory=dict)

    @property
    def symbols(self):
        return list(self.values.columns)


# ── Helpers ───────────────────────────────────────────────────────────────────

def anchor_date(portfolio: dict) -> date:
    """The date the current holdings snapshot is valid for."""
    stamp = portfolio.get('snapshot_date') or portfolio.get('fetched_at') or ''
    try:
        return date.fromisoformat(str(stamp)[:10])
    except ValueError:
        return date.today()


def trade_rows(transactions_df) -> pd.DataFrame:
    """Buy/sell rows with usable dates and quantities."""
    if transactions_df is None or transactions_df.empty:
        return pd.DataFrame(columns=['Date', 'Security Name', 'Symbol', 'Quantity', 'Total Value'])
    df = transactions_df.copy()
    df['Date'] = pd.to_datetime(df['Date'], errors='coerce')
    df = df[df['Date'].notna()]
    if 'Category' in df.columns:
        df = df[df['Category'] == 'Trade']
    elif 'Transaction Type' in df.columns:
        df = df[df['Transaction Type'].isin(['Bought', 'Sold'])]
    df['Quantity'] = pd.to_numeric(df['Quantity'], errors='coerce')
    return df[df['Quantity'].notna()].copy()


def current_shares(holdings_df) -> pd.Series:
    """{symbol: quantity} for currently held positions, excluding cash."""
    if holdings_df is None or holdings_df.empty:
        return pd.Series(dtype=float)
    df = holdings_df.copy()
    df['Symbol'] = df['Symbol'].astype(str).str.strip().str.upper()
    df = df[~df['Symbol'].isin([CASH_SYMBOL, 'TOTAL', ''])]
    qty = pd.to_numeric(df['Quantity'], errors='coerce').fillna(0.0)
    return qty.groupby(df['Symbol']).sum()


def current_cash(holdings_df) -> float:
    if holdings_df is None or holdings_df.empty:
        return 0.0
    df = holdings_df.copy()
    df['Symbol'] = df['Symbol'].astype(str).str.strip().str.upper()
    row = df[df['Symbol'] == CASH_SYMBOL]
    if row.empty:
        return 0.0
    val = pd.to_numeric(row['Market Value'], errors='coerce').fillna(0.0).sum()
    return float(val)


def daily_cash_flow(transactions_df, index: pd.DatetimeIndex) -> pd.Series:
    """
    Net change in the cash balance on each day.

    Buys consume cash and sells produce it. E*TRADE signs sell quantities
    negative, so ``Total Value`` (quantity × price) is already negative on a sell
    — the cash effect is therefore the negation of that figure. Every non-trade
    row (deposits, withdrawals, dividends, interest, internal transfers) moves
    cash by its own signed amount; internal transfers appear as offsetting pairs
    across the consolidated accounts and net to zero on their own.
    """
    flow = pd.Series(0.0, index=index)
    if transactions_df is None or transactions_df.empty:
        return flow

    df = transactions_df.copy()
    df['Date'] = pd.to_datetime(df['Date'], errors='coerce')
    df = df[df['Date'].notna()]
    df['Total Value'] = pd.to_numeric(df['Total Value'], errors='coerce').fillna(0.0)
    df['_day'] = df['Date'].dt.normalize()

    is_trade = (
        df['Category'].eq('Trade') if 'Category' in df.columns
        else df['Transaction Type'].isin(['Bought', 'Sold'])
    )
    df['_cash'] = np.where(is_trade, -df['Total Value'], df['Total Value'])

    daily = df.groupby('_day')['_cash'].sum()
    return flow.add(daily.reindex(index).fillna(0.0), fill_value=0.0)


def net_contributions(transactions_df, index: pd.DatetimeIndex) -> pd.Series:
    """Cumulative external money in minus money out (deposits/withdrawals only)."""
    contrib = pd.Series(0.0, index=index)
    if transactions_df is None or transactions_df.empty or 'Category' not in transactions_df.columns:
        return contrib
    df = transactions_df.copy()
    df['Date'] = pd.to_datetime(df['Date'], errors='coerce')
    df = df[df['Date'].notna() & df['Category'].isin(['Deposit', 'Withdrawal'])]
    if df.empty:
        return contrib
    df['Total Value'] = pd.to_numeric(df['Total Value'], errors='coerce').fillna(0.0)
    daily = df.groupby(df['Date'].dt.normalize())['Total Value'].sum()
    return contrib.add(daily.reindex(index).fillna(0.0), fill_value=0.0).cumsum()


# ── Core reconstruction ───────────────────────────────────────────────────────

def build_share_history(holdings_df, trades_df, splits: pd.DataFrame,
                        index: pd.DatetimeIndex):
    """
    Walk share counts backwards from the anchor (the last date in ``index``).

    Returns (shares_today_basis, shares_actual_at_the_time).

    Implemented in closed form rather than as a day loop. Writing ``P(d)`` for
    the running product of split ratios up to day ``d``, a raw trade quantity
    ``q(d)`` is worth ``q(d)·P(anchor)/P(d)`` shares on today's basis, so the
    today-basis holding is just an anchor value minus a cumulative sum:

        a(d) = P(anchor) · [ a(anchor)/P(anchor) − Σ_{t>d} q(t)/P(t) ]

    Multiplying the same bracket by ``P(d)`` instead recovers the raw count the
    account actually held on day ``d``.
    """
    held = current_shares(holdings_df)
    # An account with no trades in range yields a frame with no columns at all,
    # so the Symbol column cannot be assumed to exist.
    traded = set()
    if trades_df is not None and not trades_df.empty and 'Symbol' in trades_df.columns:
        traded = set(trades_df['Symbol'].dropna().unique()) - {''}

    symbols = sorted(set(held.index) | traded)
    if not symbols or len(index) == 0:
        return pd.DataFrame(index=index), pd.DataFrame(index=index)

    # Daily net quantity traded, signed (+ buy, − sell)
    q = pd.DataFrame(0.0, index=index, columns=symbols)
    if traded:
        t = trades_df[trades_df['Symbol'].isin(symbols)].copy()
        t['_day'] = pd.to_datetime(t['Date']).dt.normalize()
        pivot = t.pivot_table(index='_day', columns='Symbol', values='Quantity',
                              aggfunc='sum', fill_value=0.0)
        pivot = pivot.reindex(index=index, columns=symbols).fillna(0.0)
        q = q.add(pivot, fill_value=0.0)[symbols]

    ratios = splits.reindex(index=index, columns=symbols).fillna(1.0)
    P = ratios.cumprod()

    adj_q = q / P
    # Shares still to be "unwound" after each date: everything traded later
    traded_after = adj_q.sum() - adj_q.cumsum()

    anchor_shares = pd.Series(
        {s: float(held.get(s, 0.0)) for s in symbols}, index=symbols, dtype=float
    )
    P_anchor = P.iloc[-1]
    A = (anchor_shares / P_anchor) - traded_after

    # Sub-share float noise from repeated division/multiplication
    return (A * P_anchor).round(8), (A * P).round(8)


def reconstruct(portfolio: dict, trades_df, store, start: date, end: date,
                price_module) -> HistoryResult:
    """
    Build the full daily history between ``start`` and ``end``.

    Args:
        portfolio: the session portfolio dict (holdings, transactions, fetched_at).
        trades_df: trade rows with a resolved ``Symbol`` column.
        store: loaded price store dict.
        start, end: requested bounds (``end`` is clamped to the holdings anchor —
            there is nothing to reconstruct after the snapshot date).
        price_module: lib.price_store (injected so this module stays import-light).
    """
    holdings_df = portfolio.get('holdings')
    transactions_df = portfolio.get('transactions')
    anchor = anchor_date(portfolio)
    end = min(end, anchor)

    # The walk always runs back past the first transaction, whatever window was
    # asked for. Cash and contributions are cumulative and would otherwise be
    # rebased on an arbitrary day, and the residual check is only meaningful when
    # measured on a day that predates all activity.
    origin = min(pd.Timestamp(start), _first_activity(transactions_df) - pd.Timedelta(days=1))
    walk_index = pd.date_range(start=origin, end=pd.Timestamp(anchor), freq='D')
    if len(walk_index) == 0:
        empty = pd.DataFrame()
        blank = pd.Series(dtype=float)
        return HistoryResult(empty, empty, empty, empty, blank, blank, blank,
                             blank, {'error': 'Empty date range'})

    held = current_shares(holdings_df)
    symbols = sorted(set(held.index) | (set(trades_df['Symbol'].dropna()) - {''}))

    splits = price_module.split_frame(store, symbols, walk_index)
    shares, shares_actual = build_share_history(holdings_df, trades_df, splits, walk_index)

    flow = daily_cash_flow(transactions_df, walk_index)
    cash_anchor = current_cash(holdings_df)
    cash = cash_anchor - (flow.sum() - flow.cumsum())

    contributions = net_contributions(transactions_df, walk_index)

    # Present only real trading days inside the requested window
    market_days = price_module.trading_days(store, shares.columns, start, end)
    if len(market_days) == 0:
        market_days = walk_index[(walk_index >= pd.Timestamp(start)) & (walk_index <= pd.Timestamp(end))]
    if pd.Timestamp(anchor) <= pd.Timestamp(end) and pd.Timestamp(anchor) not in market_days:
        market_days = market_days.union(pd.DatetimeIndex([pd.Timestamp(anchor)]))

    shares_out = shares.reindex(market_days)
    prices_out = price_module.price_frame(store, shares.columns, market_days)
    values_out = (shares_out.clip(lower=0.0) * prices_out).fillna(0.0)
    cash_out = cash.reindex(market_days).ffill().fillna(0.0)
    contrib_out = contributions.reindex(market_days).ffill().fillna(0.0)

    diagnostics = _diagnose(shares, shares_out, prices_out, values_out, holdings_df, walk_index)

    positions_total = values_out.sum(axis=1)

    return HistoryResult(
        shares=shares_out,
        shares_actual=shares_actual.reindex(market_days),
        prices=prices_out,
        values=values_out,
        cash=cash_out,
        positions_total=positions_total,
        total=positions_total + cash_out,
        contributions=contrib_out,
        diagnostics=diagnostics,
    )


def _first_activity(transactions_df) -> pd.Timestamp:
    if transactions_df is None or transactions_df.empty:
        return pd.Timestamp(date.today())
    dates = pd.to_datetime(transactions_df['Date'], errors='coerce').dropna()
    return dates.min().normalize() if not dates.empty else pd.Timestamp(date.today())


def _diagnose(shares_full, shares_win, prices, values, holdings_df, walk_index) -> dict:
    """Everything a reader needs to judge how much to trust the reconstruction."""
    diag = {}

    # 1. Residual shares on the day before the first transaction — should be ~0
    #    if the feed covers the whole life of the account. Anything else means
    #    shares existed beforehand, or transactions are missing.
    first = shares_full.iloc[0]
    residual = first[first.abs() > 1e-6]
    diag['residual_shares'] = residual.sort_values(ascending=False)

    # 2. Symbols we could never price, among those actually held in the window
    unpriced = [s for s in shares_win.columns if prices[s].notna().sum() == 0
                and shares_win[s].abs().max() > 1e-6]
    diag['unpriced_symbols'] = unpriced

    # 3. Days where a held position had no price yet (value understated)
    held_mask = shares_win.abs() > 1e-6
    missing_px = held_mask & prices.isna()
    diag['missing_price_days'] = int(missing_px.to_numpy().sum())
    any_missing = missing_px.any()
    diag['partial_price_symbols'] = sorted(any_missing[any_missing].index.tolist())

    # 4. Negative share counts — impossible in reality, so a data-quality flag
    neg = (shares_full < -1e-6).any()
    diag['negative_share_symbols'] = sorted(neg[neg].index.tolist())

    # 5. Reconciliation against the live snapshot — only meaningful when the
    #    window actually reaches the snapshot date.
    ends_on_anchor = len(values) > 0 and values.index[-1].date() == walk_index[-1].date()
    if ends_on_anchor:
        reported = 0.0
        rebuilt_cash = 0.0
        if holdings_df is not None and not holdings_df.empty:
            h = holdings_df.copy()
            h['Symbol'] = h['Symbol'].astype(str).str.upper()
            h = h[~h['Symbol'].isin(['TOTAL'])]
            reported = float(pd.to_numeric(h['Market Value'], errors='coerce').fillna(0).sum())
            rebuilt_cash = current_cash(holdings_df)
        rebuilt = float(values.iloc[-1].sum()) + rebuilt_cash
        diag['reconciliation'] = {
            'reported_total': reported,
            'rebuilt_total': rebuilt,
            'difference': rebuilt - reported,
        }
    else:
        diag['reconciliation'] = {}
    diag['residual_date'] = walk_index[0].date()
    diag['anchor_date'] = walk_index[-1].date()
    return diag


# ── Breakout attributes ───────────────────────────────────────────────────────

# yfinance reports GICS-style sector names; sectors.json uses the shorter labels
# the rest of the app displays. Without this bridge the same portfolio splits
# across "Tech" and "Technology", "Finance" and "Financial Services".
_SECTOR_ALIASES = {
    'TECHNOLOGY': 'Tech',
    'COMMUNICATION SERVICES': 'Communications',
    'FINANCIAL SERVICES': 'Finance',
    'FINANCIAL': 'Finance',
    'CONSUMER CYCLICAL': 'Consumer',
    'CONSUMER DEFENSIVE': 'Consumer',
    'CONSUMER STAPLES': 'Consumer',
    'CONSUMER DISCRETIONARY': 'Consumer',
    'BASIC MATERIALS': 'Materials',
    'INDUSTRIALS': 'Industrials',
    'HEALTHCARE': 'Healthcare',
    'ENERGY': 'Energy',
    'UTILITIES': 'Utilities',
    'REAL ESTATE': 'Real Estate',
}

_FUND_TYPES = {'ETF', 'Crypto ETP', 'Commodity ETP'}


def symbol_attributes(symbols, store, holdings_df, sector_groups=None) -> pd.DataFrame:
    """
    One row per symbol with the dimensions the UI groups by.

    Sector comes from the project's curated sectors.json first (it reflects how
    the owner thinks about the portfolio) and falls back to yfinance, mapped onto
    the same vocabulary. Funds carry no sector of their own and land in the
    curated ETFs/Index bucket.
    """
    sector_lookup = {}
    for sector, syms in (sector_groups or {}).items():
        for s in syms:
            sector_lookup[s.upper()] = sector

    held = set(current_shares(holdings_df).index)
    meta = store.get('meta', {})

    rows = []
    for sym in symbols:
        m = meta.get(sym, {})
        asset_type = m.get('asset_type') or 'Unknown'

        sector = sector_lookup.get(sym)
        if not sector:
            raw = (m.get('sector') or '').strip()
            sector = _SECTOR_ALIASES.get(raw.upper(), raw)
        if not sector:
            sector = 'ETFs/Index' if asset_type in _FUND_TYPES else 'Unclassified'

        rows.append({
            'Symbol': sym,
            'Name': m.get('name') or sym,
            'Asset Type': asset_type,
            'Sector': sector,
            'Status': 'Held' if sym in held else 'Closed',
        })
    return pd.DataFrame(rows).set_index('Symbol')


OTHER_LABEL = 'Other'


def collapse_by(values: pd.DataFrame, attributes: pd.DataFrame, dimension: str) -> pd.DataFrame:
    """Sum the per-symbol value matrix onto a grouping dimension, no capping."""
    if values.empty:
        return values
    if dimension == 'Symbol':
        return values.copy()
    mapping = attributes[dimension].reindex(values.columns).fillna('Unclassified')
    return values.T.groupby(mapping).sum().T


def rank_categories(values: pd.DataFrame, attributes: pd.DataFrame, dimension: str) -> list:
    """
    Categories ordered by how much of the portfolio they ever represented.

    Ranking on the peak rather than the latest day keeps a position that was
    once large but has since been sold from being buried in the tail.
    """
    grouped = collapse_by(values, attributes, dimension)
    if grouped.empty:
        return []
    peak = grouped.abs().max()
    latest = grouped.iloc[-1].abs()
    order = pd.DataFrame({'peak': peak, 'latest': latest})
    order = order.sort_values(['peak', 'latest'], ascending=False)
    return [c for c in order.index if order.loc[c, 'peak'] > 0]


def group_values(values: pd.DataFrame, attributes: pd.DataFrame, dimension: str,
                 top_n: int = 8) -> pd.DataFrame:
    """
    Collapse onto a grouping dimension, folding the tail into 'Other'.

    The cap applies to every dimension, not just symbols: a categorical palette
    has a fixed number of distinct hues, and a ninth series can only be served by
    inventing one or reusing another. Ranking is by peak value so a large but
    since-closed position keeps its own band.
    """
    if values.empty:
        return values

    grouped = collapse_by(values, attributes, dimension)
    grouped = grouped.loc[:, grouped.abs().sum() > 0]
    if grouped.shape[1] <= top_n:
        return grouped

    keep = [c for c in rank_categories(values, attributes, dimension) if c in grouped.columns][:top_n]
    rest = [c for c in grouped.columns if c not in keep]
    out = grouped[keep].copy()
    if rest:
        out[OTHER_LABEL] = grouped[rest].sum(axis=1)
    return out


def period_stats(result: HistoryResult, include_cash: bool = True) -> dict:
    """Headline numbers for the selected window."""
    series = result.total if include_cash else result.positions_total
    if series.empty:
        return {}
    start_val, end_val = float(series.iloc[0]), float(series.iloc[-1])
    contrib = result.contributions
    net_added = float(contrib.iloc[-1] - contrib.iloc[0]) if not contrib.empty else 0.0
    change = end_val - start_val
    return {
        'start_value': start_val,
        'end_value': end_val,
        'change': change,
        'change_pct': (change / start_val * 100) if start_val else None,
        'net_contributions': net_added,
        'market_gain': change - net_added,
        'peak_value': float(series.max()),
        'peak_date': series.idxmax().date(),
        'trough_value': float(series.min()),
        'trough_date': series.idxmin().date(),
        'max_drawdown_pct': _max_drawdown(series),
    }


def _max_drawdown(series: pd.Series):
    if series.empty:
        return None
    running_max = series.cummax()
    dd = (series - running_max) / running_max.replace(0, np.nan)
    return float(dd.min() * 100) if dd.notna().any() else None
