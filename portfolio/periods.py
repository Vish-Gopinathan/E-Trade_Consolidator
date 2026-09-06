"""
Period returns: how the investments did, regardless of when money moved.

The dashboard already reports two returns over the *whole* history — Modified
Dietz (cumulative) and XIRR (per year). Neither answers "how did this do since
January", and neither can, because both are anchored to the first cash flow on
record.

This module answers it with a **time-weighted return** (TWR) over an arbitrary
window, built from the daily value series :mod:`portfolio.history` reconstructs.

**Why time-weighted here, when returns.py argues for money-weighted.** They
answer different questions and both belong on the page.

* XIRR is money-weighted: it counts *when* money was added, because the timing of
  a deposit is the account holder's own decision and depositing before a rise
  genuinely made money. That is the right lens on "how did I do".
* A period return is a different question — "how did the *investments* do between
  these two dates" — and there the deposits are noise. Depositing $50k in
  February must not read as a February gain, and it must not read as a loss when
  a withdrawal shrinks the balance. Time-weighting removes the balance's size
  from the answer entirely, which is exactly the "agnostic of deposits and
  withdrawals" property, and it is the form a published index return takes, so
  this figure — unlike the other two — *can* be set beside the S&P.

**The mechanism.** Each day is a sub-period. The return of day ``t`` is::

    r(t) = (V(t) - F(t)) / V(t-1) - 1

where ``V`` is end-of-day total value (positions plus cash) and ``F`` is the net
external deposit/withdrawal attributed to that day. Subtracting the flow from the
closing value removes money that arrived rather than was earned. Chain-linking
the daily factors gives the period return, and because each link divides by that
day's own opening value, a balance that doubles overnight from a deposit changes
no link.

Flows are assumed to land at the **close** of their day, so they do not earn that
day's return. E*TRADE timestamps a transfer to the day, not the minute, so the
alternative — assuming it arrived at the open — is equally arbitrary; the choice
matters only for the single day of the flow and is stated rather than hidden.

**What this inherits.** Everything rests on the reconstruction in
:mod:`portfolio.history`, which walks share counts backwards from today. Recent
windows are therefore the most trustworthy figures the app produces, and distant
ones the least: any gap in the transaction feed lands in the earliest dates. A
period whose window starts before the reconstruction does is **not shown under
that label** — a "5 years" figure covering three years of data is a mislabelled
number, and this dashboard would rather omit it.
"""

from dataclasses import dataclass, field
from datetime import date

import numpy as np
import pandas as pd

from portfolio import returns

#: Market days in a year, for annualising volatility. Not 365.25: the daily
#: return series is indexed on trading days, and scaling by calendar days would
#: understate the spread by about a fifth.
TRADING_DAYS_PER_YEAR = 252

#: Below this many observations, a standard deviation is describing the sample
#: rather than the portfolio, so volatility is left blank.
MIN_OBSERVATIONS_FOR_VOLATILITY = 30

#: A balance at or below this is treated as an empty account rather than a
#: denominator. Dividing a day's gain by $0.40 of residual cash produces a
#: five-figure percentage that then multiplies through every later link.
DORMANT_FLOOR = 1.0

#: How much of a requested window may fall before the data starts before the
#: period is dropped rather than labelled. Five days absorbs a window that opens
#: on a weekend or a holiday; anything more is a genuinely shorter period.
TRUNCATION_TOLERANCE_DAYS = 5


@dataclass
class PeriodReturn:
    """One window's performance, with everything needed to judge it."""

    label: str
    start: date | None = None          # first day *measured* (base is the day before)
    end: date | None = None
    base_date: date | None = None      # the close the window is measured from
    days: int = 0                      # calendar days, base_date → end
    observations: int = 0              # daily links in the chain
    return_pct: float | None = None    # time-weighted, cumulative over the window
    annualised_pct: float | None = None
    start_value: float = 0.0
    end_value: float = 0.0
    net_flow: float = 0.0              # external deposits less withdrawals, in window
    market_gain: float = 0.0           # value change not explained by those flows
    volatility_pct: float | None = None
    max_drawdown_pct: float | None = None
    best_day: tuple | None = None      # (date, pct)
    worst_day: tuple | None = None
    #: Days dropped off the front because the account could not open them — it
    #: did not exist yet, or the reconstruction had not become consistent.
    skipped_days: int = 0
    #: How many of those had a *negative* balance. Not the same thing: an empty
    #: account is ordinary, a negative one means the cash walk ran back past the
    #: start of the transaction feed and the figures before it are fiction.
    negative_days: int = 0
    #: The window could not start where it was asked to. A period that is
    #: truncated must not keep its label — three years under a "5 years" heading
    #: states a fact about a period nobody measured.
    truncated: bool = False
    index: pd.Series = field(default_factory=lambda: pd.Series(dtype=float))
    note: str = ''

    @property
    def ok(self) -> bool:
        return self.return_pct is not None


# ── The chain ─────────────────────────────────────────────────────────────────

def daily_returns(values: pd.Series, flows: pd.Series):
    """
    Per-day time-weighted returns as fractions, and a count of dormant days.

    Args:
        values: End-of-day total value, ascending by date.
        flows: Net external flow attributed to each of those dates — positive for
            a deposit. Reindexed onto ``values`` and missing days read as zero.

    Returns:
        ``(series, dormant_days, broken_days)``. ``series`` is indexed by the day
        each return covers, so it is one shorter than ``values``. ``dormant_days``
        counts days opening on an effectively empty account, which contribute a
        flat 0% rather than a division by nearly nothing. ``broken_days`` counts
        days that closed worth less than the money recorded as arriving into
        them — a return at or below −100%, which a long-only account cannot
        produce. Their presence invalidates the chain rather than being absorbed
        into it.
    """
    values = pd.Series(values).astype(float)
    if len(values) < 2:
        return pd.Series(dtype=float), 0, 0

    flow = pd.Series(flows).reindex(values.index).fillna(0.0).astype(float)

    opening = values.shift(1).iloc[1:]
    closing = values.iloc[1:]
    net_of_flow = closing - flow.iloc[1:]

    dormant = opening <= DORMANT_FLOOR
    # A day cannot lose more than everything: net of the money that arrived,
    # the close must still be positive. At or below zero the history and the
    # flows disagree, and no amount of linking will reconcile them.
    broken = (~dormant) & (net_of_flow <= 0)

    with np.errstate(divide='ignore', invalid='ignore'):
        result = net_of_flow / opening - 1.0
    result[dormant] = 0.0
    result = result.replace([np.inf, -np.inf], 0.0).fillna(0.0)

    return result, int(dormant.sum()), int(broken.sum())


def growth_index(daily: pd.Series, base: float = 100.0) -> pd.Series:
    """
    Chain-linked value of ``base`` invested at the window's open.

    This is the deposit-agnostic equity curve: it moves only when the
    investments move, so it can be read against an index without the account's
    contribution schedule distorting the shape.
    """
    if daily.empty:
        return pd.Series(dtype=float)
    return base * (1.0 + daily).cumprod()


def _max_drawdown_pct(index: pd.Series):
    if index.empty:
        return None
    peak = index.cummax()
    drawdown = (index - peak) / peak.replace(0, np.nan)
    return float(drawdown.min() * 100) if drawdown.notna().any() else None


# ── One window ────────────────────────────────────────────────────────────────

def window_return(values: pd.Series, flows: pd.Series, label: str,
                  start, end=None) -> PeriodReturn:
    """
    Time-weighted return between ``start`` and ``end``.

    ``start`` names the first day *inside* the window; the return is measured
    from the close of the last observation before it, which is the balance the
    period actually opened with — a year-to-date figure runs from 31 December,
    not from the first close of January.

    Every day but the last supplies a denominator, and a denominator at or below
    zero is not a small number, it is a broken one. Those days are dropped off
    the front of the window and counted in :attr:`PeriodReturn.skipped_days`; if
    that moves the start of the window, :attr:`PeriodReturn.truncated` is set and
    the caller decides whether the window still deserves its label. It usually
    does not: :func:`period_returns` drops truncated periods and
    :func:`year_returns` renames them.
    """
    period = PeriodReturn(label=label)
    if values is None or len(values) < 2:
        period.note = 'Not enough daily history to measure a return.'
        return period

    values = pd.Series(values).astype(float).sort_index()
    start = pd.Timestamp(start).normalize()
    end = pd.Timestamp(end).normalize() if end is not None else values.index[-1]

    inside = values.index[(values.index >= start) & (values.index <= end)]
    if len(inside) == 0:
        period.note = 'No trading days fall inside this period.'
        return period

    # The base is the close before the window opens: what the period started
    # with. Absent one — the window reaches back past the data — the first day
    # inside becomes the base, which costs a day and shows up as truncation.
    earlier = values.index[values.index < start]
    base_ts = earlier[-1] if len(earlier) else inside[0]
    span = values.loc[base_ts:inside[-1]]

    # A balance at or below the floor cannot open a day. Leading ones are
    # ordinary — the account had not started — so the window moves forward past
    # the last of them rather than dividing by nothing. The final value is not a
    # denominator and is left alone: an account emptied by a withdrawal on the
    # last day still earned whatever it earned on the way there.
    openings = span.iloc[:-1]
    unusable = openings[openings <= DORMANT_FLOOR]
    if len(unusable):
        cutoff = span.index.get_loc(unusable.index[-1])
        period.skipped_days = int(cutoff) + 1
        period.negative_days = int((unusable < 0).sum())
        span = span.iloc[cutoff + 1:]

    if len(span) < 2:
        period.note = _unusable_note(period, label)
        return period

    flow = pd.Series(flows).reindex(values.index).fillna(0.0).astype(float)
    daily, dormant, broken = daily_returns(span, flow)

    period.base_date = span.index[0].date()
    period.start = span.index[1].date()
    period.end = span.index[-1].date()
    period.days = int((span.index[-1] - span.index[0]).days)
    period.observations = int(len(daily))
    period.start_value = float(span.iloc[0])
    period.end_value = float(span.iloc[-1])
    period.net_flow = float(flow.loc[span.index[1:]].sum())
    period.market_gain = period.end_value - period.start_value - period.net_flow
    period.truncated = (span.index[0] - start).days > TRUNCATION_TOLERANCE_DAYS

    if broken:
        # A day worth less than the deposit that landed in it cannot happen in
        # a complete history. Something is missing or misclassified, and chaining
        # through it produces a confidently wrong number rather than an obviously
        # wrong one.
        period.note = (
            f'{broken} day(s) in this period closed worth less than the money '
            'recorded as arriving into them, which no account can do. That points '
            'at a missing or misclassified transaction rather than at a real '
            'loss, so no return is shown for this period.'
        )
        return period

    index = growth_index(daily)
    period.index = pd.concat([pd.Series([100.0], index=[span.index[0]]), index])
    period.return_pct = round(float(index.iloc[-1]) - 100.0, 2)

    if period.days >= returns.MIN_DAYS_TO_ANNUALISE:
        years = period.days / returns.DAYS_PER_YEAR
        growth = float(index.iloc[-1]) / 100.0
        period.annualised_pct = round((growth ** (1.0 / years) - 1.0) * 100, 2)

    if period.observations >= MIN_OBSERVATIONS_FOR_VOLATILITY:
        period.volatility_pct = round(
            float(daily.std(ddof=1)) * np.sqrt(TRADING_DAYS_PER_YEAR) * 100, 2
        )

    period.max_drawdown_pct = _max_drawdown_pct(period.index)
    if not daily.empty:
        best, worst = daily.idxmax(), daily.idxmin()
        period.best_day = (best.date(), round(float(daily.loc[best]) * 100, 2))
        period.worst_day = (worst.date(), round(float(daily.loc[worst]) * 100, 2))

    if period.negative_days:
        period.note = (
            f'Measured from {period.base_date:%b %d, %Y}. Before that the '
            f'reconstructed balance is negative on {period.negative_days:,} '
            'day(s) — the cash walk ran back past the start of the transaction '
            'feed, so there is nothing there to measure.'
        )
    elif period.skipped_days:
        period.note = (
            f'Measured from {period.base_date:%b %d, %Y}, the first day the '
            'account held anything.'
        )
    return period


def _unusable_note(period: PeriodReturn, label: str) -> str:
    """Why a window with no usable opening balance is being left blank."""
    if period.negative_days:
        return (
            'The reconstructed balance is negative throughout this period, which '
            'means the cash walk has run back past the start of the transaction '
            'feed. There is no return to measure here, only missing history.'
        )
    if period.skipped_days:
        return 'The account held nothing for this whole period.'
    return 'Fewer than two trading days with a balance in this period.'


# ── The set of windows ────────────────────────────────────────────────────────

def trailing_windows(anchor: date) -> list:
    """
    ``(label, start_date)`` for the standard trailing periods, longest last.

    Calendar offsets rather than fixed day counts: "3 months" ending on 31 May
    starts on 28 February, not 91 days earlier. The difference is a couple of
    days of return, which is enough to make a figure quoted here disagree with
    one quoted anywhere else.
    """
    stamp = pd.Timestamp(anchor).normalize()
    return [
        ('1 month', (stamp - pd.DateOffset(months=1)).date()),
        ('3 months', (stamp - pd.DateOffset(months=3)).date()),
        ('6 months', (stamp - pd.DateOffset(months=6)).date()),
        ('Year to date', date(anchor.year, 1, 1)),
        ('1 year', (stamp - pd.DateOffset(years=1)).date()),
        ('3 years', (stamp - pd.DateOffset(years=3)).date()),
        ('5 years', (stamp - pd.DateOffset(years=5)).date()),
    ]


def calendar_years(anchor: date, first: date) -> list:
    """``(label, start_date, end_date)`` for each calendar year with data."""
    out = []
    for year in range(first.year, anchor.year + 1):
        label = f'{year}' if year != anchor.year else f'{year} (to date)'
        out.append((label, date(year, 1, 1), min(date(year, 12, 31), anchor)))
    return out


def flow_series(result) -> pd.Series:
    """
    Net external deposit/withdrawal per observation date.

    :attr:`~portfolio.history.HistoryResult.contributions` is the cumulative
    figure, so the per-day flow is its first difference. The first observation
    has no predecessor inside the frame and reads as zero: whatever was
    contributed before it is already inside that day's opening value, which is
    where it belongs.
    """
    contributions = getattr(result, 'contributions', None)
    if contributions is None or len(contributions) == 0:
        return pd.Series(dtype=float)
    return contributions.diff().fillna(0.0)


def period_returns(result, anchor: date, include_cash: bool = True) -> list:
    """
    Every trailing period the reconstructed history can honestly support.

    A period whose window starts more than
    :data:`TRUNCATION_TOLERANCE_DAYS` before the data does is left out rather
    than shown short: labelling three years of history "5 years" states a fact
    about a period that was not measured. "Since <date>" always appears and
    covers whatever the data does reach, named for the date it actually starts.
    """
    values = (result.total if include_cash else result.positions_total)
    if values is None or len(values) < 2:
        return []

    flows = flow_series(result)
    first = values.index[0].date()
    end = values.index[-1].date()

    out = []
    for label, start in trailing_windows(anchor):
        period = window_return(values, flows, label, start, end)
        if period.truncated:
            continue
        out.append(period)

    # "Since" is the one window allowed to move: it names its own start, so
    # beginning where the data becomes usable tells the truth rather than
    # bending a fixed label around a shorter period.
    since = window_return(values, flows, 'Since', first, end)
    if since.base_date:
        since.label = f'Since {pd.Timestamp(since.base_date):%b %Y}'
    since.truncated = False
    out.append(since)
    return out


def year_returns(result, anchor: date, include_cash: bool = True) -> list:
    """Calendar-year time-weighted returns, oldest first."""
    values = (result.total if include_cash else result.positions_total)
    if values is None or len(values) < 2:
        return []

    flows = flow_series(result)
    first = values.index[0].date()

    out = []
    for label, start, end in calendar_years(anchor, first):
        period = window_return(values, flows, label, start, end)
        if not period.observations:
            # Nothing measurable in that year — usually a year that predates the
            # usable reconstruction. Left out rather than shown as a flat 0%,
            # which would read as a year the portfolio went nowhere.
            continue
        if period.truncated:
            # A year the data starts partway through is still worth showing —
            # it is where the history begins, not a mislabelled full year — so
            # the label says where it began.
            period.label = f'{period.base_date.year} (from {period.base_date:%b %d})'
        out.append(period)
    return out


def to_frame(periods: list) -> pd.DataFrame:
    """Tabular view of a list of :class:`PeriodReturn`, for display and export."""
    return pd.DataFrame([
        {
            'Period': p.label,
            'Return (%)': p.return_pct,
            'Annualised (%)': p.annualised_pct,
            'Market gain ($)': p.market_gain if p.ok else None,
            'Net deposits ($)': p.net_flow if p.ok else None,
            'Volatility (%)': p.volatility_pct,
            'Max drawdown (%)': p.max_drawdown_pct,
            'From': p.base_date,
            'To': p.end,
            'Trading days': p.observations,
        }
        for p in periods
    ])
