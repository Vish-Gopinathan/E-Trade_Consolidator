"""
FIFO realised P&L.

The case that matters most here is the one with no right answer: a sell whose
purchase is not in the history. Treating its cost basis as zero would report the
entire proceeds as profit, which on the real account would have invented several
thousand dollars of gain. It has to be reported and excluded instead, and that
is what most of these tests pin down.
"""

import pandas as pd
import pytest

from portfolio import realized


def _trade(date, symbol, quantity, price):
    """One trade row. Sells carry a negative quantity, as E*TRADE reports them."""
    return {
        'Date': pd.Timestamp(date), 'Symbol': symbol, 'Quantity': float(quantity),
        'Price': float(price), 'Total Value': float(quantity) * float(price),
        'Transaction Type': 'Bought' if quantity > 0 else 'Sold', 'Category': 'Trade',
    }


def _frame(*rows):
    return pd.DataFrame(list(rows))


# ── FIFO ordering ─────────────────────────────────────────────────────────────

def test_shares_are_matched_oldest_buy_first():
    result = realized.realized_pnl(_frame(
        _trade('2024-01-10', 'AAA', 10, 100.0),   # oldest, and the cheaper lot
        _trade('2024-02-10', 'AAA', 10, 150.0),
        _trade('2024-03-10', 'AAA', -10, 200.0),
    ))
    # FIFO consumes the $100 lot: 10 x (200 - 100).
    assert result['net'] == 1000.0
    assert result['gains'] == 1000.0
    assert result['losses'] == 0.0
    assert result['unmatched'] == {}


def test_a_sell_spanning_two_lots_takes_from_each():
    result = realized.realized_pnl(_frame(
        _trade('2024-01-10', 'AAA', 10, 100.0),
        _trade('2024-02-10', 'AAA', 10, 150.0),
        _trade('2024-03-10', 'AAA', -15, 200.0),
    ))
    # 10 x (200-100) from the first lot, 5 x (200-150) from the second.
    assert result['net'] == pytest.approx(1250.0)


def test_a_partly_consumed_lot_keeps_its_remainder():
    result = realized.realized_pnl(
        _frame(
            _trade('2024-01-10', 'AAA', 10, 100.0),
            _trade('2024-03-10', 'AAA', -4, 150.0),
        ),
        held_symbols=(),
    )
    assert result['net'] == pytest.approx(200.0)
    # Six shares are still open, and AAA is not in holdings, so it is flagged.
    assert result['orphan_lots'] == {'AAA': 6.0}


def test_gains_and_losses_are_reported_separately_not_netted():
    result = realized.realized_pnl(_frame(
        _trade('2024-01-10', 'WIN', 10, 100.0),
        _trade('2024-01-10', 'LOSE', 10, 100.0),
        _trade('2024-06-10', 'WIN', -10, 130.0),
        _trade('2024-06-10', 'LOSE', -10, 80.0),
    ))
    assert result['gains'] == 300.0
    assert result['losses'] == -200.0
    assert result['net'] == 100.0


# ── The honest hard case ──────────────────────────────────────────────────────

def test_a_sell_with_no_buy_is_reported_and_excluded():
    """The whole point: never assume a zero cost basis."""
    result = realized.realized_pnl(_frame(_trade('2024-03-10', 'GHOST', -5, 200.0)))

    assert result['net'] == 0.0, 'proceeds with no basis must not become profit'
    assert result['gains'] == 0.0
    assert result['unmatched'] == {'GHOST': {'shares': 5.0, 'proceeds': 1000.0}}
    assert result['unmatched_proceeds'] == 1000.0


def test_only_the_uncovered_part_of_a_sell_is_unmatched():
    result = realized.realized_pnl(_frame(
        _trade('2024-01-10', 'AAA', 4, 100.0),
        _trade('2024-03-10', 'AAA', -10, 200.0),
    ))
    # Four shares matched and profitable; six have no purchase behind them.
    assert result['net'] == pytest.approx(400.0)
    assert result['unmatched']['AAA']['shares'] == 6.0
    assert result['unmatched']['AAA']['proceeds'] == pytest.approx(1200.0)


def test_a_position_still_held_is_not_an_orphan_lot():
    result = realized.realized_pnl(
        _frame(_trade('2024-01-10', 'AAA', 10, 100.0)),
        held_symbols=['AAA'],
    )
    assert result['orphan_lots'] == {}


# ── Splits ────────────────────────────────────────────────────────────────────

def test_a_split_between_buy_and_sell_matches_the_shares():
    """
    100 bought before a 10:1 split are 1,000 shares to sell afterwards. Without
    the adjustment the sale looks like it exceeded the position, leaving a
    phantom unmatched sell and a stranded lot.
    """
    splits = {'AAA': pd.Series({pd.Timestamp('2024-02-01'): 10.0})}
    result = realized.realized_pnl(
        _frame(
            _trade('2024-01-10', 'AAA', 100, 1000.0),   # $100,000 in
            _trade('2024-03-10', 'AAA', -1000, 120.0),  # $120,000 out
        ),
        splits=splits,
    )
    assert result['unmatched'] == {}, 'the split-adjusted sell should match exactly'
    assert result['net'] == pytest.approx(20000.0)
    assert result['orphan_lots'] == {}


def test_no_split_data_leaves_quantities_alone():
    result = realized.realized_pnl(_frame(
        _trade('2024-01-10', 'AAA', 10, 100.0),
        _trade('2024-03-10', 'AAA', -10, 110.0),
    ), splits={})
    assert result['net'] == pytest.approx(100.0)


def test_cumulative_split_factor_ignores_splits_before_the_date():
    series = pd.Series({
        pd.Timestamp('2020-01-01'): 2.0,    # before — already in the share count
        pd.Timestamp('2024-01-01'): 10.0,   # after — must be applied
    })
    assert realized.cumulative_split_factor(series, pd.Timestamp('2022-01-01')) == 10.0
    assert realized.cumulative_split_factor(None, pd.Timestamp('2022-01-01')) == 1.0
    assert realized.cumulative_split_factor({}, pd.Timestamp('2022-01-01')) == 1.0


# ── Ordering and degenerate input ─────────────────────────────────────────────

def test_a_same_day_buy_and_sell_closes_out():
    """
    Buys are processed before sells on the same date. Sorted the other way the
    sell finds no lot, and a completed round trip reads as unmatched.
    """
    result = realized.realized_pnl(_frame(
        _trade('2024-03-10', 'AAA', -10, 120.0),   # listed first on purpose
        _trade('2024-03-10', 'AAA', 10, 100.0),
    ))
    assert result['unmatched'] == {}
    assert result['net'] == pytest.approx(200.0)


def test_rows_that_are_not_trades_are_ignored():
    frame = _frame(
        _trade('2024-01-10', 'AAA', 10, 100.0),
        {
            'Date': pd.Timestamp('2024-02-01'), 'Symbol': '', 'Quantity': None,
            'Price': None, 'Total Value': 5000.0,
            'Transaction Type': 'Contribution', 'Category': 'Deposit',
        },
    )
    result = realized.realized_pnl(frame, held_symbols=['AAA'])
    assert result['net'] == 0.0
    assert result['sell_count'] == 0


def test_empty_input_produces_a_full_result():
    result = realized.realized_pnl(pd.DataFrame())
    assert result['net'] == 0.0
    assert result['by_symbol'] == {}
    assert result['unmatched'] == {}
