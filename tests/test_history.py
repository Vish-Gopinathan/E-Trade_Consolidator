"""
Portfolio history reconstruction.

The backward walk is the subtlest code in the repo, and its correctness rests on
two invariants that are easy to break and hard to notice:

1. **Today is exact.** The walk anchors on current holdings, so the share count on
   the anchor date must equal what is actually held — no drift, no rounding.
2. **Gaps surface, they do not hide.** A trade missing from the feed leaves a
   non-zero share count before the account existed, which the diagnostics report
   as a residual rather than absorbing.

Splits are the third trap: historical closes from Yahoo are split-adjusted, so
share counts must be expressed on today's basis to pair with them.
"""

import pandas as pd
import pytest

from portfolio import history


def trading_days(start, end):
    return pd.bdate_range(start, end)


def trades(rows):
    """rows: (date, symbol, signed_quantity)."""
    return pd.DataFrame(
        [{'Date': pd.Timestamp(d), 'Symbol': s, 'Quantity': q} for d, s, q in rows]
    )


def holdings(rows):
    """rows: (symbol, quantity)."""
    frame = pd.DataFrame([{'Symbol': s, 'Quantity': q, 'Market Value': q * 10.0} for s, q in rows])
    return frame


def no_splits(index, symbols):
    return pd.DataFrame(1.0, index=index, columns=symbols)


# ── Invariant 1: the anchor is exact ──────────────────────────────────────────

def test_anchor_matches_current_holdings():
    index = trading_days('2024-01-01', '2024-03-29')
    shares, _ = history.build_share_history(
        holdings([('AAA', 150.0)]),
        trades([('2024-01-15', 'AAA', 100.0), ('2024-02-15', 'AAA', 50.0)]),
        no_splits(index, ['AAA']), index,
    )
    assert shares['AAA'].iloc[-1] == pytest.approx(150.0)


def test_walk_unwinds_each_trade_on_its_day():
    index = trading_days('2024-01-01', '2024-03-29')
    shares, _ = history.build_share_history(
        holdings([('AAA', 150.0)]),
        trades([('2024-01-15', 'AAA', 100.0), ('2024-02-15', 'AAA', 50.0)]),
        no_splits(index, ['AAA']), index,
    )
    series = shares['AAA']
    assert series.loc[:'2024-01-12'].eq(0.0).all()            # before the first buy
    assert series.loc['2024-01-15':'2024-02-14'].eq(100.0).all()
    assert series.loc['2024-02-15':].eq(150.0).all()


def test_a_fully_sold_position_returns_to_zero():
    index = trading_days('2024-01-01', '2024-06-28')
    shares, _ = history.build_share_history(
        holdings([]),   # nothing held today
        trades([('2024-01-15', 'BBB', 80.0), ('2024-04-15', 'BBB', -80.0)]),
        no_splits(index, ['BBB']), index,
    )
    series = shares['BBB']
    assert series.iloc[-1] == pytest.approx(0.0)
    assert series.loc['2024-01-15':'2024-04-12'].eq(80.0).all()


# ── Invariant 2: gaps surface as a residual ───────────────────────────────────

def test_a_complete_history_leaves_no_residual():
    index = trading_days('2024-01-01', '2024-03-29')
    shares, _ = history.build_share_history(
        holdings([('AAA', 100.0)]),
        trades([('2024-01-15', 'AAA', 100.0)]),
        no_splits(index, ['AAA']), index,
    )
    assert shares.iloc[0].abs().max() == pytest.approx(0.0, abs=1e-6)


def test_a_missing_buy_shows_up_before_the_account_existed():
    """
    Holding 150 shares with only a 100-share buy on record means 50 shares came
    from somewhere the feed does not show. The walk pushes that into the earliest
    date instead of silently adjusting today.
    """
    index = trading_days('2024-01-01', '2024-03-29')
    shares, _ = history.build_share_history(
        holdings([('AAA', 150.0)]),
        trades([('2024-01-15', 'AAA', 100.0)]),
        no_splits(index, ['AAA']), index,
    )
    assert shares['AAA'].iloc[0] == pytest.approx(50.0)
    assert shares['AAA'].iloc[-1] == pytest.approx(150.0)     # today stays exact


# ── Splits ────────────────────────────────────────────────────────────────────

def test_pre_split_trades_are_expressed_on_todays_share_basis():
    """
    10 shares bought before a 10:1 split are 100 shares today. Historical closes
    are split-adjusted, so only the today-basis count pairs with them correctly;
    the raw count held at the time is reported separately for display.
    """
    index = trading_days('2024-01-01', '2024-12-31')
    splits = no_splits(index, ['AAA'])
    splits.loc[pd.Timestamp('2024-06-10'), 'AAA'] = 10.0

    today_basis, actual = history.build_share_history(
        holdings([('AAA', 100.0)]),
        trades([('2024-02-01', 'AAA', 10.0)]),
        splits, index,
    )

    # Today's basis: 100 shares for the whole holding period.
    assert today_basis['AAA'].loc['2024-02-01'] == pytest.approx(100.0)
    assert today_basis['AAA'].iloc[-1] == pytest.approx(100.0)
    # Raw count at the time: 10 before the split, 100 after.
    assert actual['AAA'].loc['2024-02-01'] == pytest.approx(10.0)
    assert actual['AAA'].iloc[-1] == pytest.approx(100.0)


# ── Cash and contributions ────────────────────────────────────────────────────

def test_net_contributions_accumulate_external_flows_only():
    index = trading_days('2024-01-01', '2024-06-28')
    transactions = pd.DataFrame([
        {'Date': pd.Timestamp('2024-01-15'), 'Total Value': 5000.0, 'Category': 'Deposit'},
        {'Date': pd.Timestamp('2024-03-15'), 'Total Value': -1000.0, 'Category': 'Withdrawal'},
        {'Date': pd.Timestamp('2024-04-15'), 'Total Value': 250.0, 'Category': 'Income'},
        {'Date': pd.Timestamp('2024-05-15'), 'Total Value': 3000.0, 'Category': 'Internal'},
    ])
    contributions = history.net_contributions(transactions, index)

    assert contributions.iloc[-1] == pytest.approx(4000.0)
    assert contributions.loc['2024-01-15'] == pytest.approx(5000.0)
    # Dividends and inter-account moves are not contributions.
    assert contributions.loc['2024-04-15'] == pytest.approx(4000.0)


# ── Degenerate inputs ─────────────────────────────────────────────────────────

def test_no_symbols_returns_empty_frames():
    index = trading_days('2024-01-01', '2024-01-31')
    shares, actual = history.build_share_history(
        holdings([]), trades([]), no_splits(index, []), index)
    assert shares.empty and actual.empty


def test_empty_index_returns_empty_frames():
    index = pd.DatetimeIndex([])
    shares, actual = history.build_share_history(
        holdings([('AAA', 10.0)]), trades([]), pd.DataFrame(), index)
    assert shares.empty and actual.empty
