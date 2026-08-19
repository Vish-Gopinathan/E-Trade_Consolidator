"""
Annualised return: how fast the money actually grew, per year.

The dashboard already reports a Modified Dietz figure, but that is a *cumulative*
return over whatever period the history covers. "Up 24%" means nothing without
knowing whether that took eight months or four years, and it cannot be compared
against a savings rate or an index without dividing by something.

This module answers the other question: **what constant annual rate would have
turned each deposit, on the day it was made, into today's balance?** That is the
money-weighted return, or XIRR — the internal rate of return on the actual dated
cash flows.

**Why money-weighted rather than time-weighted.** A time-weighted return strips
out the effect of deposit timing to measure the picking of investments; it is
what a fund quotes, because a manager does not control when money arrives. Here
the deposits are the account holder's own decisions, so their timing is part of
the result rather than noise to be removed. Depositing heavily just before a rise
genuinely made money and should show.

The trade-off is that it is not comparable to a published fund return. That is
stated with the number rather than left for the reader to trip over.

**Short periods are not annualised.** Scaling two months of gains up to a yearly
rate produces a number that is arithmetically defensible and practically a lie —
a 6% quarter becomes "26% a year". Below the threshold here the function returns
None and says why, in keeping with this dashboard preferring an absent figure to
a misleading one.
"""

import logging

LOGGER = logging.getLogger(__name__)

#: Days in a year, for discounting. 365.25 rather than 365, so leap years do not
#: bias the result: a span of one calendar year across a leap year is 366 days,
#: and dividing by 365 reports 9.97% where the answer is 10.00%. Three basis
#: points is small, but it is a systematic understatement rather than noise.
DAYS_PER_YEAR = 365.25

#: Below this, the figure is suppressed instead of annualised. A year is the
#: shortest window where "per year" is a measurement rather than an extrapolation.
MIN_DAYS_TO_ANNUALISE = 365

#: Bracket for the search. The lower bound sits just above total loss, where the
#: discounting blows up; the upper allows a 10x-a-year result, far beyond
#: anything a brokerage account will produce but cheap to include.
_LOWER, _UPPER = -0.9999, 10.0
_TOLERANCE = 1e-7
_MAX_ITERATIONS = 200


def npv(rate: float, flows) -> float:
    """
    Net present value of dated cash flows at an annual ``rate``.

    ``flows`` is a sequence of ``(days_from_start, amount)``. Sign convention is
    the investor's: money leaving them for the portfolio is negative, money
    coming back — a withdrawal, or the closing value treated as if liquidated
    today — is positive.
    """
    total = 0.0
    for days, amount in flows:
        total += amount / (1.0 + rate) ** (days / DAYS_PER_YEAR)
    return total


def xirr(flows) -> float | None:
    """
    The annual rate at which :func:`npv` is zero, or None when there is not one.

    Solved by bisection rather than Newton's method. Newton converges faster but
    can diverge on the flat, near-vertical NPV curves that heavily-front-loaded
    deposits produce, and a silently wrong growth rate is worse than a slower
    one. Bisection cannot diverge: given a bracket where NPV changes sign, it
    always converges on the root inside it.

    Returns None when the cash flows admit no solution — all deposits and no
    value, or a portfolio that lost everything. A rate that does not exist is
    reported as absent rather than as zero.
    """
    flows = [(float(days), float(amount)) for days, amount in flows]
    if len(flows) < 2:
        return None
    if not (any(a < 0 for _, a in flows) and any(a > 0 for _, a in flows)):
        # No sign change means no root: money only ever went one way.
        return None

    low, high = _LOWER, _UPPER
    npv_low, npv_high = npv(low, flows), npv(high, flows)
    if npv_low * npv_high > 0:
        LOGGER.debug('xirr: no sign change across the bracket; no rate exists')
        return None

    for _ in range(_MAX_ITERATIONS):
        middle = (low + high) / 2.0
        value = npv(middle, flows)
        if abs(value) < _TOLERANCE or (high - low) / 2.0 < _TOLERANCE:
            return middle
        if value * npv_low > 0:
            low, npv_low = middle, value
        else:
            high = middle
    LOGGER.debug('xirr: did not converge in %d iterations', _MAX_ITERATIONS)
    return None


def annualised_return(cash_flows, ending_value: float, as_of=None) -> dict:
    """
    Money-weighted annual growth rate from dated deposits and withdrawals.

    Args:
        cash_flows: ``(date, amount)`` pairs with deposits **positive** — the
            convention used everywhere else in this codebase. They are negated
            here, because from the investor's side a deposit is money out.
        ending_value: Portfolio value today, treated as a final inflow as though
            the account were liquidated.
        as_of: Valuation date. Defaults to the latest flow date or today.

    Returns:
        ``{'rate_pct', 'days', 'first_flow', 'basis'}``. ``rate_pct`` is None
        when the period is too short to annualise or no rate exists, and
        ``basis`` always says which.
    """
    import pandas as pd

    dated = [
        (pd.Timestamp(date).normalize(), float(amount))
        for date, amount in cash_flows
        if pd.notna(date) and pd.notna(amount) and float(amount) != 0.0
    ]
    if not dated:
        return {'rate_pct': None, 'days': 0, 'first_flow': None,
                'basis': 'No deposits or withdrawals on record, so there is no '
                         'contribution history to measure growth against.'}

    dated.sort()
    start = dated[0][0]
    end = pd.Timestamp(as_of).normalize() if as_of is not None else max(
        pd.Timestamp.today().normalize(), dated[-1][0])
    days = int((end - start).days)

    if days < MIN_DAYS_TO_ANNUALISE:
        return {
            'rate_pct': None, 'days': days, 'first_flow': start.date().isoformat(),
            'basis': (
                f'Only {days} days of contribution history — too short to state a '
                'yearly rate. Annualising a partial year exaggerates it in both '
                f'directions, so this is left blank until {MIN_DAYS_TO_ANNUALISE} '
                'days have passed.'
            ),
        }

    # Investor's sign convention: a deposit is money out of their pocket.
    flows = [((date - start).days, -amount) for date, amount in dated]
    flows.append((days, float(ending_value)))

    rate = xirr(flows)
    if rate is None:
        return {
            'rate_pct': None, 'days': days, 'first_flow': start.date().isoformat(),
            'basis': ('No single annual rate fits these cash flows — that happens '
                      'when money only ever moved one way, or the account lost '
                      'its entire value.'),
        }

    years = days / DAYS_PER_YEAR
    return {
        'rate_pct': round(rate * 100, 2),
        'days': days,
        'first_flow': start.date().isoformat(),
        'basis': (
            f'Money-weighted (XIRR) over {years:.1f} years from '
            f'{start:%b %Y}, discounting each deposit and withdrawal from the day '
            'it happened. Because it counts when you added money, not just how '
            'much, it is not directly comparable to a published fund return.'
        ),
    }
