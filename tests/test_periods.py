"""
Time-weighted period returns.

The property under test is the one the metric exists for: **moving money in or
out must not move the number**. Every case is small enough to check by hand,
because a period return that is subtly wrong looks exactly like a period return
that is right.
"""

from datetime import date

import pandas as pd
import pytest

from portfolio import history, periods


def series(pairs):
    """(date, value) pairs → a daily value series."""
    index = pd.DatetimeIndex([pd.Timestamp(d) for d, _ in pairs])
    return pd.Series([float(v) for _, v in pairs], index=index)


def flows(pairs, index):
    """(date, amount) pairs → per-day external flow aligned to ``index``."""
    s = pd.Series(0.0, index=index)
    for d, amount in pairs:
        s.loc[pd.Timestamp(d)] = float(amount)
    return s


def twr(values, flow_pairs=(), start=None, end=None):
    return periods.window_return(
        values, flows(flow_pairs, values.index), 'test',
        start or values.index[0], end or values.index[-1],
    )


# ── Arithmetic that can be checked by hand ────────────────────────────────────

def test_a_pure_market_move_is_the_return():
    values = series([('2026-01-02', 100.0), ('2026-01-05', 110.0)])
    assert twr(values).return_pct == pytest.approx(10.0)


def test_daily_returns_chain_rather_than_add():
    """Up 10% then down 10% is -1%, not 0%. Adding percentages is the bug."""
    values = series([
        ('2026-01-02', 100.0), ('2026-01-05', 110.0), ('2026-01-06', 99.0),
    ])
    assert twr(values).return_pct == pytest.approx(-1.0)


def test_a_flat_portfolio_returns_zero():
    values = series([(f'2026-01-0{d}', 500.0) for d in range(2, 8)])
    assert twr(values).return_pct == pytest.approx(0.0)


# ── The property the metric exists for ────────────────────────────────────────

def test_a_deposit_is_not_a_gain():
    """
    The whole point. The balance doubles overnight because money was added, and
    the return must stay at zero. Read naively off the balance this is +100%.
    """
    values = series([('2026-01-02', 1_000.0), ('2026-01-05', 2_000.0)])
    result = twr(values, [('2026-01-05', 1_000.0)])
    assert result.return_pct == pytest.approx(0.0)
    assert result.net_flow == pytest.approx(1_000.0)


def test_a_withdrawal_is_not_a_loss():
    values = series([('2026-01-02', 2_000.0), ('2026-01-05', 1_000.0)])
    assert twr(values, [('2026-01-05', -1_000.0)]).return_pct == pytest.approx(0.0)


def test_growth_after_a_deposit_is_measured_on_the_larger_balance():
    """
    $1,000 grows to $1,100 having received $1,000 half way. The gain is $100 on
    a $2,000 base for the second leg — 10%, not the 120% the balance suggests
    and not the 10% of the original $1,000 either.
    """
    values = series([
        ('2026-01-02', 1_000.0), ('2026-01-05', 2_000.0), ('2026-01-06', 2_200.0),
    ])
    assert twr(values, [('2026-01-05', 1_000.0)]).return_pct == pytest.approx(10.0)


def test_deposit_timing_does_not_change_the_return():
    """
    Two accounts hold the same investments and earn the same 10%, one having
    deposited early and one late. Money-weighted returns differ here by design
    (see test_returns.py); a time-weighted return must not.
    """
    early = series([
        ('2026-01-02', 1_000.0), ('2026-01-05', 2_000.0), ('2026-01-06', 2_200.0),
    ])
    late = series([
        ('2026-01-02', 1_000.0), ('2026-01-05', 1_100.0), ('2026-01-06', 2_100.0),
    ])
    a = twr(early, [('2026-01-05', 1_000.0)]).return_pct
    b = twr(late, [('2026-01-06', 1_000.0)]).return_pct
    assert a == pytest.approx(b, abs=0.01)
    assert a == pytest.approx(10.0, abs=0.01)


# ── Windows ───────────────────────────────────────────────────────────────────

def test_the_window_is_measured_from_the_close_before_it():
    """
    Year to date starts on 1 January, so it is measured from the last close of
    December. Starting from the first close *of* January silently discards the
    first trading day's move.
    """
    values = series([
        ('2025-12-31', 100.0), ('2026-01-02', 110.0), ('2026-01-05', 121.0),
    ])
    result = twr(values, start=date(2026, 1, 1))
    assert result.base_date == date(2025, 12, 31)
    assert result.return_pct == pytest.approx(21.0)


def test_days_before_the_account_had_a_balance_are_skipped():
    """An empty account is not a denominator; the window starts where money did."""
    values = series([
        ('2026-01-02', 0.0), ('2026-01-05', 0.0),
        ('2026-01-06', 1_000.0), ('2026-01-07', 1_100.0),
    ])
    result = twr(values, [('2026-01-06', 1_000.0)])
    assert result.base_date == date(2026, 1, 6)
    assert result.return_pct == pytest.approx(10.0)


def test_a_day_worth_less_than_its_deposit_refuses_to_link():
    """
    A return below -100% is impossible, so a day closing below the money that
    arrived in it means a transaction is missing or misclassified. Chaining
    through it would produce a confidently wrong number.
    """
    values = series([('2026-01-02', 1_000.0), ('2026-01-05', 500.0)])
    result = twr(values, [('2026-01-05', 2_000.0)])
    assert result.return_pct is None
    assert 'misclassified' in result.note


def test_selling_out_and_withdrawing_the_proceeds_is_still_a_real_gain():
    """
    The mirror case, which must *not* be refused: a position doubles, is sold,
    and every dollar leaves the same day. The balance ends at zero and the day
    still earned 100%.
    """
    values = series([('2026-01-02', 1_000.0), ('2026-01-05', 0.0)])
    assert twr(values, [('2026-01-05', -2_000.0)]).return_pct == pytest.approx(100.0)


# ── Annualisation and dispersion ──────────────────────────────────────────────

def test_short_periods_are_not_annualised():
    values = series([('2026-01-02', 100.0), ('2026-04-02', 106.0)])
    result = twr(values)
    assert result.return_pct == pytest.approx(6.0)
    assert result.annualised_pct is None


def test_two_years_of_growth_annualises_to_the_yearly_rate():
    """21% over two years is 10% a year. Quoting 21% as a rate is the mistake."""
    values = series([('2024-01-02', 100.0), ('2026-01-02', 121.0)])
    assert twr(values).annualised_pct == pytest.approx(10.0, abs=0.05)


def test_volatility_needs_enough_observations():
    values = series([('2026-01-02', 100.0), ('2026-01-05', 101.0)])
    assert twr(values).volatility_pct is None


def test_max_drawdown_tracks_the_return_index_not_the_balance():
    """
    A withdrawal halves the balance without hurting performance, so the
    deposit-agnostic drawdown must stay at zero. Value Over Time reports the
    other kind, on purpose, and the two must not be confused.
    """
    values = series([
        ('2026-01-02', 1_000.0), ('2026-01-05', 1_100.0), ('2026-01-06', 550.0),
    ])
    result = twr(values, [('2026-01-06', -550.0)])
    assert result.max_drawdown_pct == pytest.approx(0.0)


# ── Period selection ──────────────────────────────────────────────────────────

class _Result:
    """The two fields of a HistoryResult that period_returns reads."""

    def __init__(self, total, contributions):
        self.total = total
        self.positions_total = total
        self.contributions = contributions


def _history(values, cumulative_flows=None):
    contributions = (
        pd.Series(cumulative_flows, index=values.index) if cumulative_flows is not None
        else pd.Series(0.0, index=values.index)
    )
    return _Result(values, contributions)


def test_periods_longer_than_the_data_are_dropped_not_shortened():
    """
    Labelling one year of history "5 years" states a fact about a period that
    was never measured. It is left out; "Since <date>" covers what there is.
    """
    index = pd.bdate_range('2025-09-01', '2026-09-01')
    values = pd.Series(range(100, 100 + len(index)), index=index, dtype=float)
    labels = [p.label for p in periods.period_returns(_history(values), date(2026, 9, 1))]

    assert '5 years' not in labels
    assert '3 years' not in labels
    assert '3 months' in labels
    assert any(label.startswith('Since') for label in labels)


def test_cumulative_contributions_become_daily_flows():
    """
    HistoryResult carries the running total; the chain needs the daily
    difference. Feeding it the cumulative figure would subtract every past
    deposit from every day.
    """
    index = pd.bdate_range('2026-01-05', periods=4)
    result = _history(pd.Series([100.0, 200.0, 200.0, 220.0], index=index),
                      cumulative_flows=[0.0, 100.0, 100.0, 100.0])
    flow = periods.flow_series(result)
    assert list(flow) == [0.0, 100.0, 0.0, 0.0]


def test_calendar_years_start_where_the_data_does():
    index = pd.bdate_range('2025-06-02', '2026-03-02')
    values = pd.Series(range(100, 100 + len(index)), index=index, dtype=float)
    labels = [p.label for p in periods.year_returns(_history(values), date(2026, 3, 2))]
    assert labels[0].startswith('2025 (from Jun')
    assert labels[1] == '2026 (to date)'


# ── Against the real reconstruction ───────────────────────────────────────────

def test_a_deposit_in_the_reconstructed_history_does_not_read_as_return():
    """
    End to end through portfolio.history: an account holding one position at a
    flat price, into which cash is deposited. The balance grows, the return
    does not.
    """
    index = pd.bdate_range('2026-01-05', periods=10)
    values = pd.Series(1_000.0, index=index)
    values.iloc[5:] = 6_000.0                      # $5,000 deposited, price flat
    contributions = pd.Series(0.0, index=index)
    contributions.iloc[5:] = 5_000.0

    result = _history(values, list(contributions))
    since = periods.period_returns(result, index[-1].date())[-1]
    assert since.return_pct == pytest.approx(0.0)
    assert since.net_flow == pytest.approx(5_000.0)
    assert since.market_gain == pytest.approx(0.0)


def test_history_result_is_the_shape_period_returns_expects():
    """Guards the coupling: these are the attributes read off HistoryResult."""
    for attribute in ('total', 'positions_total', 'contributions'):
        assert attribute in history.HistoryResult.__dataclass_fields__


# ── A negative reconstructed balance is missing history, not a flat year ──────

def test_a_negative_balance_is_refused_rather_than_reported_as_zero():
    """
    The cash walk runs backwards from today's balance, so on an account whose
    transaction feed does not reach its opening the reconstructed balance goes
    negative in the earliest years. Those years have no return to report. Read
    naively they divide a small change by a large negative number and come out
    at a confident 0.0%, which looks exactly like a year the portfolio went
    nowhere.
    """
    values = series([
        ('2009-01-02', -44_000.0), ('2009-06-01', -43_900.0),
        ('2009-12-31', -43_800.0),
    ])
    result = twr(values)
    assert result.return_pct is None
    assert 'negative' in result.note


def test_a_window_reaching_into_negative_history_starts_where_it_becomes_real():
    values = series([
        ('2009-12-30', -44_000.0), ('2009-12-31', -43_800.0),
        ('2010-03-01', 46_000.0), ('2010-03-02', 46_460.0),
    ])
    result = twr(values)
    assert result.base_date == date(2010, 3, 1)
    assert result.return_pct == pytest.approx(1.0)
    assert result.truncated
    assert result.negative_days == 2


def test_calendar_years_before_the_usable_history_are_left_out():
    """
    The demo portfolio trades from 2001 while its cash flows start in 2010, so
    every year in between reconstructs to a negative balance. Nine bars reading
    "0%" is worse than nine missing bars.
    """
    index = pd.bdate_range('2008-01-01', '2010-12-31')
    values = pd.Series(-44_000.0, index=index)
    values.loc['2010-03-01':] = 50_000.0
    labels = [p.label for p in periods.year_returns(_history(values), date(2010, 12, 31))]

    assert labels == ['2010 (from Mar 01)']


def test_a_trailing_window_that_cannot_reach_back_is_dropped():
    """
    Not shortened and not zeroed: a "3 years" heading over a window that starts
    in the middle of last year is a statement about a period nobody measured.
    """
    index = pd.bdate_range('2026-01-02', '2026-09-01')
    values = pd.Series(range(100, 100 + len(index)), index=index, dtype=float)
    labels = [p.label for p in periods.period_returns(_history(values), date(2026, 9, 1))]
    assert '3 years' not in labels and '1 year' not in labels
    assert '3 months' in labels
