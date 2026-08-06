"""
The report-key contract.

This is the regression guard for the defect that made the Analytics page look
thin: the pages asked for ``'Simple Return (%)'`` while analytics emitted
``'Total Return (%)'``, every lookup missed, and nothing raised. If a report key
can be spelled in two places, it will eventually be spelled two ways — so the
test asserts that every key an engine emits is declared in
:mod:`portfolio.schema`, and that the demo fixtures produce the same shape.
"""

import pandas as pd
import pytest

from portfolio import analytics, classify, schema


@pytest.fixture
def portfolio_frames():
    """A small portfolio with cash, gains, losses, deposits and dividends."""
    holdings = pd.DataFrame([
        {'Symbol': 'AAA', 'Symbol Description': 'Alpha', 'Current Price': 20.0,
         'Quantity': 100.0, 'Date Acquired': pd.Timestamp('2024-01-02'), 'Price Paid': 10.0,
         'Total Cost': 1000.0, 'Market Value': 2000.0, 'Total Gain': 1000.0,
         'Total Gain %': 100.0, 'Percent of Portfolio': 50.0},
        {'Symbol': 'BBB', 'Symbol Description': 'Beta', 'Current Price': 8.0,
         'Quantity': 100.0, 'Date Acquired': pd.Timestamp('2024-06-01'), 'Price Paid': 10.0,
         'Total Cost': 1000.0, 'Market Value': 800.0, 'Total Gain': -200.0,
         'Total Gain %': -20.0, 'Percent of Portfolio': 20.0},
        {'Symbol': 'CASH', 'Symbol Description': 'Cash', 'Current Price': 1.0,
         'Quantity': 1200.0, 'Date Acquired': pd.NaT, 'Price Paid': 1.0,
         'Total Cost': 1200.0, 'Market Value': 1200.0, 'Total Gain': 0.0,
         'Total Gain %': 0.0, 'Percent of Portfolio': 30.0},
    ])
    transactions = pd.DataFrame([
        {'Date': pd.Timestamp('2024-01-02'), 'Security Name': 'ACH DEPOSIT REFID:1',
         'Symbol': '', 'Quantity': None, 'Price': None, 'Total Value': 2000.0,
         'Transaction Type': 'Online Transfer', 'Category': classify.DEPOSIT},
        {'Date': pd.Timestamp('2024-01-03'), 'Security Name': 'BOUGHT AAA',
         'Symbol': 'AAA', 'Quantity': 100.0, 'Price': 10.0, 'Total Value': 1000.0,
         'Transaction Type': 'Bought', 'Category': classify.TRADE},
        {'Date': pd.Timestamp('2024-07-01'), 'Security Name': 'ALPHA DIVIDEND',
         'Symbol': 'AAA', 'Quantity': None, 'Price': None, 'Total Value': 25.0,
         'Transaction Type': 'Dividend', 'Category': classify.INCOME},
    ])
    return holdings, transactions


@pytest.fixture
def report(portfolio_frames):
    holdings, transactions = portfolio_frames
    return analytics.PortfolioAnalytics(holdings, transactions).generate_full_report()


def _keys_in(report):
    """Every section name and metric name the report uses."""
    found = set(report)
    for section in report.values():
        if isinstance(section, dict):
            found |= set(section)
    return found


def test_every_report_key_is_declared(report):
    undeclared = _keys_in(report) - schema.all_keys()
    assert not undeclared, (
        f'Report keys not declared in portfolio/schema.py: {sorted(undeclared)}. '
        'Add a constant there and use it — never spell a report key inline.'
    )


def test_report_has_every_section(report):
    assert set(report) == set(schema.SECTIONS)


def test_demo_data_matches_the_engine_contract():
    """
    Demo and live must produce the same shape. They did not, which is how the
    pages came to be written against keys the engine never emitted.
    """
    from ui.demo_data import build_demo_portfolio

    demo_report = build_demo_portfolio()['analytics_report']
    assert set(demo_report) == set(schema.SECTIONS)
    assert not _keys_in(demo_report) - schema.all_keys()


# ── Metric correctness ────────────────────────────────────────────────────────

def test_total_return_excludes_cash_from_both_sides(report):
    """
    Cash has no cost basis, so counting it in market value alone reported
    uninvested cash as investment gain.
    """
    performance = report[schema.PERFORMANCE]
    assert performance[schema.TOTAL_RETURN_DOLLARS] == pytest.approx(800.0)
    assert performance[schema.TOTAL_RETURN_PCT] == pytest.approx(40.0)


def test_concentration_weights_exclude_cash(report):
    """
    Weights are shares of invested value. Using shares of the whole account made
    them sum to less than 100 and produced more effective positions than held.
    """
    concentration = report[schema.CONCENTRATION]
    weights = concentration[schema.POSITION_WEIGHTS]
    assert sum(weights.values()) == pytest.approx(100.0, abs=0.05)
    assert concentration[schema.EFFECTIVE_POSITIONS] <= concentration[schema.TOTAL_POSITIONS]


def test_gain_buckets_agree_with_win_loss_counts(report):
    """A position at exactly 0% used to be counted as both flat and a small loss."""
    quality = report[schema.HOLDINGS_QUALITY]
    total = quality[schema.WINNERS] + quality[schema.LOSERS] + quality[schema.BREAKEVEN]
    assert sum(quality[schema.GAIN_DISTRIBUTION].values()) == total


def test_breakeven_position_is_not_a_loss():
    holdings = pd.DataFrame([
        {'Symbol': 'FLAT', 'Symbol Description': 'Flat', 'Current Price': 10.0,
         'Quantity': 10.0, 'Date Acquired': pd.NaT, 'Price Paid': 10.0,
         'Total Cost': 100.0, 'Market Value': 100.0, 'Total Gain': 0.0,
         'Total Gain %': 0.0, 'Percent of Portfolio': 100.0},
    ])
    quality = analytics.PortfolioAnalytics(holdings).holdings_quality()
    assert quality[schema.BREAKEVEN] == 1
    assert quality[schema.LOSERS] == 0
    assert quality[schema.GAIN_DISTRIBUTION]['Small loss (-10% to 0%)'] == 0


def test_deposit_adjusted_return_does_not_double_count_deposits(report):
    """
    Using cost basis as beginning market value subtracted the same deposits twice
    and reported a large loss on an account that had gained.
    """
    adjusted = report[schema.PERFORMANCE][schema.DEPOSIT_ADJUSTED_RETURN_PCT]
    assert adjusted is not None
    assert adjusted > 0
    assert schema.DEPOSIT_ADJUSTED_RETURN_BASIS in report[schema.PERFORMANCE]


def test_no_cash_flows_leaves_the_adjusted_return_unset(portfolio_frames):
    holdings, _ = portfolio_frames
    performance = analytics.PortfolioAnalytics(holdings).performance()
    assert performance[schema.DEPOSIT_ADJUSTED_RETURN_PCT] is None


def test_empty_portfolio_produces_a_full_report():
    report = analytics.PortfolioAnalytics(pd.DataFrame()).generate_full_report()
    assert set(report) == set(schema.SECTIONS)
