"""
Reconciliation: where the portfolio's money actually came from.

Every other page answers "what do I have?". This one answers "how did it get
there?", by holding the account to an identity::

    Portfolio Value = Net Deposits + Realised P&L + Income + Unrealised P&L

Whatever the recorded activity cannot explain is shown as its own line rather
than absorbed into one of the others. On an account older than E*TRADE's
two-year transaction window that gap is real and large, and hiding it would make
four honest numbers add up to a dishonest one.
"""

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from portfolio import schema
from ui import theme
from ui.common import md, money, page_header, require_portfolio, signed_money

page_header(
    'Reconciliation', '🧾',
    'Every dollar in the account, traced back to where it came from.',
)

portfolio = require_portfolio()
report = portfolio.get('analytics_report') or {}
section = report.get(schema.RECONCILIATION) or {}

if not section:
    st.info(
        'No reconciliation available in the current data. Refresh from E\\*TRADE '
        'to build it.'
    )
    st.stop()

colours = theme.palette()


def value(key, default=0.0):
    """Report values are ``None`` when unavailable; treat that as zero for maths."""
    result = section.get(key)
    return default if result is None else result


portfolio_value = value(schema.TOTAL_PORTFOLIO_VALUE)
net_deposits = value(schema.NET_DEPOSITS)
realised_net = value(schema.REALIZED_NET)
income = value(schema.TOTAL_INCOME)
realised_gains = value(schema.REALIZED_GAINS)
realised_losses = value(schema.REALIZED_LOSSES)
unrealised_gains = value(schema.UNREALIZED_GAINS)
unrealised_losses = value(schema.UNREALIZED_LOSSES)
residual = value(schema.UNEXPLAINED_RESIDUAL)

flows = report.get(schema.CASH_FLOWS) or {}
deposited = flows.get(schema.TOTAL_DEPOSITED) or 0.0
withdrawn = flows.get(schema.TOTAL_WITHDRAWN) or 0.0

# ── Headline ──────────────────────────────────────────────────────────────────

# Captions rather than metric deltas. A text delta always renders an arrow, and
# Streamlit points it down whenever the string starts with a minus — so "6
# deposits" grew an upward arrow and "-$8,325 realised" a downward one, neither
# describing a change in anything.
k1, k2, k3, k4, k5 = st.columns(5)
k1.metric('Portfolio Value', money(portfolio_value, 0))
k2.metric('Deposits', money(deposited, 0))
k2.caption(f'{flows.get(schema.DEPOSIT_COUNT, 0)} deposits')
k3.metric('Withdrawals', money(withdrawn, 0))
k3.caption(f'{flows.get(schema.WITHDRAWAL_COUNT, 0)} withdrawals')
k4.metric('Total Gains', money(realised_gains + unrealised_gains, 0))
k4.caption(md(f'{money(realised_gains, 0)} realised · {money(unrealised_gains, 0)} on paper'))
k5.metric('Total Losses', money(realised_losses + unrealised_losses, 0))
k5.caption(md(f'{money(realised_losses, 0)} realised · {money(unrealised_losses, 0)} on paper'))

st.caption(
    'Gains and losses are shown gross — the two columns do not net against each '
    'other. "Realised" is money locked in by selling; "unrealised" is on paper '
    'until you sell.'
)

# ── The identity ──────────────────────────────────────────────────────────────

st.subheader('How the balance is made up')

history_starts = section.get(schema.HISTORY_STARTS)
before_label = (
    f'Before {pd.Timestamp(history_starts):%-d %b %Y}' if history_starts else 'Before records'
)

steps = [
    ('Net deposits', net_deposits, 'Money you put in, less anything taken out'),
    ('Realised P&L', realised_net, 'Locked in by selling'),
    ('Income', income, 'Dividends and interest'),
    ('Unrealised P&L', unrealised_gains + unrealised_losses, 'On paper, current positions'),
    (before_label, residual, 'Not explained by activity on record'),
]

# The diverging green/red pair measures ΔE 7.2 for protanopia — permitted only
# with a second, non-colour encoding. This chart carries all three required:
# position against the zero line, a direct label on every mark, and the same
# numbers as a table immediately below.
figure = go.Figure(go.Waterfall(
    orientation='v',
    measure=['relative'] * len(steps) + ['total'],
    x=[label for label, _, _ in steps] + ['Portfolio value'],
    y=[amount for _, amount, _ in steps] + [portfolio_value],
    text=[signed_money(amount, 0) for _, amount, _ in steps] + [money(portfolio_value, 0)],
    textposition='outside',
    connector=dict(line=dict(color=colours['grid'])),
    increasing=dict(marker=dict(color=colours['positive'])),
    decreasing=dict(marker=dict(color=colours['negative'])),
    totals=dict(marker=dict(color=colours['primary'])),
))
figure.add_hline(y=0, line_width=1, line_color=colours['reference'])
theme.apply_layout(figure, colours, y_prefix='$')
figure.update_layout(height=420, showlegend=False)
st.plotly_chart(figure, use_container_width=True)

table = pd.DataFrame(
    [{'Component': label, 'Amount': amount, 'What it is': note} for label, amount, note in steps]
    + [{'Component': 'Portfolio value', 'Amount': portfolio_value, 'What it is': 'Total today'}]
)
st.dataframe(
    table.style.format({'Amount': lambda v: signed_money(v, 2)}),
    use_container_width=True, hide_index=True,
)

basis = section.get(schema.RECONCILIATION_BASIS)
if basis:
    st.caption(md(f'ℹ️ {basis}'))

manual_rows = section.get(schema.MANUAL_ROW_COUNT) or 0
if manual_rows:
    st.caption(
        f'📥 {manual_rows} row(s) above were entered by hand rather than reported '
        'by E\\*TRADE. Manage them under **Backfill**.'
    )

# ── Gains and losses in detail ────────────────────────────────────────────────

st.subheader('Gains and losses')

left, right = st.columns(2)
with left:
    st.markdown('**Realised** — closed positions')
    a, b = st.columns(2)
    a.metric('Gains', money(realised_gains, 0))
    b.metric('Losses', money(realised_losses, 0))
    st.metric('Net', money(realised_net, 0))
with right:
    st.markdown('**Unrealised** — positions you still hold')
    a, b = st.columns(2)
    a.metric('Gains', money(unrealised_gains, 0))
    b.metric('Losses', money(unrealised_losses, 0))
    st.metric('Net', money(unrealised_gains + unrealised_losses, 0))

by_symbol = section.get(schema.REALIZED_BY_SYMBOL) or {}
if by_symbol:
    with st.expander(f'Realised P&L by position ({len(by_symbol)} closed)'):
        detail = pd.DataFrame([
            {
                'Symbol': symbol, 'Gain': book['gain'], 'Loss': book['loss'],
                'Net': book['net'], 'Proceeds': book['proceeds'], 'Shares': book['shares'],
            }
            for symbol, book in by_symbol.items()
        ]).sort_values('Net')
        st.dataframe(
            detail.style.format({
                'Gain': lambda v: money(v, 2), 'Loss': lambda v: money(v, 2),
                'Net': lambda v: signed_money(v, 2), 'Proceeds': lambda v: money(v, 2),
                'Shares': '{:,.4g}',
            }),
            use_container_width=True, hide_index=True,
        )

realised_basis = section.get(schema.REALIZED_BASIS)
if realised_basis:
    st.caption(md(f'ℹ️ {realised_basis}'))

# ── What the history cannot explain ───────────────────────────────────────────

unmatched_count = section.get(schema.UNMATCHED_SELL_COUNT) or 0
orphans = section.get(schema.ORPHAN_LOTS) or {}

if unmatched_count or orphans or abs(residual) >= 1:
    st.subheader('What the records cannot explain')
    if residual >= 0:
        st.markdown(
            f'**{md(money(residual, 0))}** of the balance has no matching activity in the '
            'transaction history. E\\*TRADE serves about two years, so an account older '
            'than that has deposits and purchases the feed never contained — an opening '
            'transfer of securities from another broker is the usual cause, because '
            'those positions arrive carrying a cost basis but no purchase to explain it.'
        )
    else:
        st.markdown(
            f'Recorded activity accounts for **{md(money(abs(residual), 0))} more** than the '
            'account actually holds. That points the other way from missing history: '
            'either withdrawals are absent from the record, or a position left without '
            'a sale behind it. Worth resolving before trusting the gain figures.'
        )

    columns = st.columns(2)
    if unmatched_count:
        with columns[0]:
            st.metric('Sells with no cost basis', unmatched_count)
            st.caption(
                md(money(section.get(schema.UNMATCHED_SELL_PROCEEDS), 0)) + ' of proceeds. '
            )
            st.caption(
                'Sold shares whose purchase predates the history. Their profit is '
                '**excluded** from realised P&L rather than assumed — counting the '
                'basis as zero would report the whole sale as gain.'
            )
    if orphans:
        with columns[1]:
            st.metric('Positions unaccounted for', len(orphans))
            st.caption(
                'Bought, never sold, and not held today: '
                + ', '.join(f'{symbol} ({shares:,.4g})' for symbol, shares in orphans.items())
            )

    st.info(
        'Enter what you can remember or find on an old statement under **Backfill** — '
        'this figure shrinks as the gap closes.'
    )

# ── IRA contributions ─────────────────────────────────────────────────────────

ira = section.get(schema.IRA_CONTRIBUTIONS_BY_YEAR) or {}
if ira:
    st.subheader('IRA contributions by tax year')
    st.caption(
        'Funding a retirement account from another of your accounts is an internal '
        'move, so it is **not** counted as a deposit above — but it still counts '
        'against the annual IRS limit. Read from E\\*TRADE\'s own tax-year markers, '
        'which is why the figures survive that reclassification. The tax year is not '
        'always the year of payment: a January contribution can be designated for the '
        'year before.'
    )
    columns = st.columns(min(len(ira), 4))
    for column, (year, amount) in zip(columns, sorted(ira.items(), reverse=True)):
        column.metric(f'TY {year}', money(amount, 0))
