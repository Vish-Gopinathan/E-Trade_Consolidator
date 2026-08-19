"""
Money-weighted annual growth rate.

Every case here is one whose answer can be worked out by hand, because the whole
value of this figure is that it is trustworthy enough to compare against a
savings rate. Anchoring on hand-checkable arithmetic is the only way to know the
solver is right rather than merely stable.
"""

import pytest

from portfolio import returns


def rate(flows, ending, as_of):
    return returns.annualised_return(flows, ending, as_of=as_of)


# ── Arithmetic that can be checked by hand ────────────────────────────────────

def test_ten_percent_over_one_year():
    assert rate([('2025-01-01', 10_000)], 11_000, '2026-01-01')['rate_pct'] == pytest.approx(10.0, abs=0.05)


def test_compounding_is_annual_not_total():
    """
    $10,000 to $12,100 over two years is 21% in total but 10% a year. Reporting
    the cumulative figure as a rate is the mistake this metric exists to avoid.
    """
    assert rate([('2024-01-01', 10_000)], 12_100, '2026-01-01')['rate_pct'] == pytest.approx(10.0, abs=0.05)


def test_a_flat_account_returns_zero():
    assert rate([('2024-01-01', 10_000)], 10_000, '2026-01-01')['rate_pct'] == pytest.approx(0.0, abs=0.05)


def test_losses_come_back_negative():
    """Halving over two years is about -29.3% a year, not -50%."""
    assert rate([('2024-01-01', 10_000)], 5_000, '2026-01-01')['rate_pct'] == pytest.approx(-29.3, abs=0.2)


def test_leap_years_do_not_bias_the_rate():
    """
    A calendar year spanning a leap year is 366 days. Dividing by 365 reported
    9.97% where the answer is 10.00% — small, but a systematic understatement.
    """
    leap = rate([('2024-01-01', 10_000)], 11_000, '2025-01-01')['rate_pct']
    ordinary = rate([('2025-01-01', 10_000)], 11_000, '2026-01-01')['rate_pct']
    assert abs(leap - ordinary) < 0.05


# ── Why money-weighted, and what it implies ───────────────────────────────────

def test_deposit_timing_changes_the_rate():
    """
    The point of the metric. Two accounts end at the same value having received
    the same money; the one that deposited later earned that gain in less time,
    so its annual rate is higher. A cumulative return cannot see this.
    """
    early = rate([('2024-01-01', 10_000)], 12_000, '2026-01-01')['rate_pct']
    late = rate([('2025-01-01', 10_000)], 12_000, '2026-01-01')['rate_pct']
    assert late > early


def test_a_late_deposit_does_not_count_as_a_full_year_of_growth():
    """
    $10,000 in at the start and $10,000 more the day before valuation ends at
    $21,000. Only the first was invested, so the rate reflects a $1,000 gain on
    $10,000 — not on $20,000.
    """
    result = rate(
        [('2024-01-01', 10_000), ('2025-12-31', 10_000)], 21_000, '2026-01-01')
    assert 4.0 < result['rate_pct'] < 6.0


def test_withdrawals_are_handled():
    """A withdrawal is money back to the investor, so it counts as return."""
    result = rate(
        [('2024-01-01', 10_000), ('2025-01-01', -2_000)], 9_500, '2026-01-01')
    assert result['rate_pct'] is not None
    assert result['rate_pct'] > 0


# ── Refusing to answer ────────────────────────────────────────────────────────

def test_short_history_is_not_annualised():
    """
    Scaling a partial year to a yearly rate is arithmetically defensible and
    practically a lie: a good quarter becomes a spectacular year.
    """
    result = rate([('2025-06-01', 10_000)], 11_000, '2025-10-01')
    assert result['rate_pct'] is None
    assert 'too short' in result['basis']


def test_no_cash_flows_has_no_rate():
    result = returns.annualised_return([], 10_000)
    assert result['rate_pct'] is None
    assert 'No deposits' in result['basis']


def test_a_total_loss_reports_no_rate_rather_than_minus_one_hundred():
    result = rate([('2024-01-01', 10_000)], 0.0, '2026-01-01')
    assert result['rate_pct'] is None or result['rate_pct'] < -90


def test_zero_amounts_are_ignored():
    """E*TRADE posts $0.00 marker rows; they are not cash flows."""
    with_markers = rate(
        [('2024-01-01', 10_000), ('2024-06-01', 0.0)], 12_100, '2026-01-01')
    without = rate([('2024-01-01', 10_000)], 12_100, '2026-01-01')
    assert with_markers['rate_pct'] == without['rate_pct']


def test_the_basis_always_explains_the_number():
    """A figure this easy to misread must never appear without its caveat."""
    for flows, ending, as_of in (
        ([('2024-01-01', 10_000)], 12_100, '2026-01-01'),
        ([('2025-06-01', 10_000)], 11_000, '2025-10-01'),
        ([], 0, None),
    ):
        result = returns.annualised_return(flows, ending, as_of=as_of)
        assert result['basis'] and len(result['basis']) > 40


# ── The solver itself ─────────────────────────────────────────────────────────

def test_npv_is_zero_at_the_solved_rate():
    """
    Bisection converges on the *rate* to 1e-7, so the residual left in NPV scales
    with the size of the flows — a tenth of a cent on $18,000 here. A cent is the
    meaningful threshold for money; demanding more would be testing float noise.
    """
    flows = [(0, -10_000), (365, -5_000), (730, 18_000)]
    solved = returns.xirr(flows)
    assert solved is not None
    assert abs(returns.npv(solved, flows)) < 0.01


def test_one_way_cash_flows_have_no_root():
    assert returns.xirr([(0, -1_000), (365, -1_000)]) is None
    assert returns.xirr([(0, 1_000)]) is None
