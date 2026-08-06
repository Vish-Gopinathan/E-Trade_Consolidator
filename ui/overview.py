"""Overview: the headline numbers and where they came from."""

import pandas as pd
import plotly.express as px
import streamlit as st

from portfolio import schema
from ui import theme
from ui.common import money, page_header, percent, require_portfolio, signed_money

page_header('Portfolio Dashboard', '📈')

portfolio = require_portfolio()
summary = portfolio.get('summary') or {}
report = portfolio.get('analytics_report') or {}
holdings = portfolio.get('holdings')

# ── Provenance banner ─────────────────────────────────────────────────────────

fetched_at = portfolio.get('fetched_at', '')
if st.session_state.get('_is_snapshot'):
    st.info(
        f'Viewing the saved snapshot from '
        f'**{portfolio.get("snapshot_date", fetched_at[:10])}**. '
        'Connect to E\\*TRADE and refresh for live data.',
        icon='📅',
    )
elif fetched_at:
    label = '🟢 Live' if st.session_state.get('etrade_connected') else '🟡 Cached'
    st.caption(f'{label} — last updated {fetched_at[:19].replace("T", " ")}')

# ── Headline numbers ──────────────────────────────────────────────────────────

performance = report.get(schema.PERFORMANCE, {})
cash_flow = report.get(schema.CASH_FLOWS, {})

k1, k2, k3, k4, k5 = st.columns(5)
k1.metric('Portfolio Value', money(summary.get('Total Portfolio Value'), 0))
k2.metric(
    'Unrealised Gain',
    money(summary.get('Total Unrealized Gain'), 0),
    percent(summary.get('Total Unrealized Gain %'), signed=True),
)
# Cash is shown in dollars first: it read as $0 for a long time, and a percentage
# alone hid that. The percentage is the caption.
k3.metric(
    'Cash', money(summary.get('Cash'), 0),
    f'{percent(summary.get("Cash Percentage"), 1)} of portfolio',
    delta_color='off',
)
k4.metric('Positions', summary.get('Total Stocks', '—'))

adjusted_return = performance.get(schema.DEPOSIT_ADJUSTED_RETURN_PCT)
k5.metric(
    'Deposit-Adj. Return',
    percent(adjusted_return),
    help=performance.get(schema.DEPOSIT_ADJUSTED_RETURN_BASIS, ''),
)

if cash_flow.get(schema.FLOWS_NEEDING_REVIEW):
    st.warning(
        f'{cash_flow[schema.FLOWS_NEEDING_REVIEW]} transfer(s) are counted as external '
        'money movements because their counterparty account is unrecognised. If any is '
        'another account of yours, the return figures above are understated — resolve '
        'them under **Cash Flows & Income → Transfer Review**.',
        icon='⚠️',
    )

# ── Per-account reconciliation ────────────────────────────────────────────────

balances = portfolio.get('account_balances')
if balances:
    with st.expander('Per-account balances — check these against the E\\*TRADE website'):
        st.caption(
            'Total Account Value comes from the API field of the same name. Cash is '
            'netCash, the free uninvested balance. If a figure here disagrees with '
            'E\\*TRADE, the problem is upstream of this dashboard.'
        )
        frame = pd.DataFrame(balances).rename(columns={
            'account_id_key': 'Account',
            'total_account_value': 'Total Account Value',
            'net_cash': 'Cash',
        })
        st.dataframe(
            frame.style.format({'Total Account Value': '${:,.2f}', 'Cash': '${:,.2f}'}),
            use_container_width=True, hide_index=True,
        )
        reported = portfolio.get('reported_total')
        if reported:
            st.caption(
                f'Sum of Total Account Value: {money(reported)} · '
                f'positions + cash on this page: {money(summary.get("Total Portfolio Value"))}'
            )

st.markdown('---')

# ── Top holdings ──────────────────────────────────────────────────────────────

if holdings is not None and not holdings.empty:
    left, right = st.columns([3, 2])

    positions = holdings[holdings['Symbol'] != 'CASH'].sort_values(
        'Market Value', ascending=False)

    with left:
        st.subheader('Largest positions')
        columns = [c for c in ['Symbol', 'Symbol Description', 'Market Value',
                               'Total Gain', 'Total Gain %', 'Percent of Portfolio']
                   if c in positions.columns]
        st.dataframe(
            positions[columns].head(10).style.format({
                'Market Value': '${:,.2f}', 'Total Gain': signed_money,
                'Total Gain %': '{:+.2f}%', 'Percent of Portfolio': '{:.2f}%',
            }, na_rep='—'),
            use_container_width=True, hide_index=True,
        )

    with right:
        st.subheader('Allocation')
        colours = theme.palette()
        top = positions.head(8)[['Symbol', 'Market Value']].copy()
        remainder = positions['Market Value'].sum() - top['Market Value'].sum()
        if remainder > 0:
            top = pd.concat(
                [top, pd.DataFrame([{'Symbol': 'Other', 'Market Value': remainder}])],
                ignore_index=True,
            )
        cash_value = float(holdings[holdings['Symbol'] == 'CASH']['Market Value'].sum())
        if cash_value > 0:
            top = pd.concat(
                [top, pd.DataFrame([{'Symbol': 'Cash', 'Market Value': cash_value}])],
                ignore_index=True,
            )

        fig = px.pie(
            top, values='Market Value', names='Symbol', hole=0.55, color='Symbol',
            color_discrete_map=theme.allocation_colours(top['Symbol'], colours),
        )
        fig.update_traces(
            textposition='inside', textinfo='label+percent',
            marker=dict(line=dict(color=colours['surface'], width=2)),
            hovertemplate='%{label}: $%{value:,.0f} (%{percent})<extra></extra>',
        )
        fig.update_layout(
            showlegend=False, margin=dict(t=0, b=0, l=0, r=0), height=340,
            paper_bgcolor='rgba(0,0,0,0)',
        )
        st.plotly_chart(fig, use_container_width=True)

# ── Quick reads ───────────────────────────────────────────────────────────────

concentration = report.get(schema.CONCENTRATION, {})
quality = report.get(schema.HOLDINGS_QUALITY, {})
income = report.get(schema.INCOME, {})

if concentration or quality or income:
    st.markdown('---')
    c1, c2, c3 = st.columns(3)
    c1.metric(
        'Top 5 weight', percent(concentration.get(schema.TOP_5_PCT), 1),
        concentration.get(schema.HHI_INTERPRETATION, ''), delta_color='off',
    )
    c2.metric(
        'Positions in profit',
        f'{quality.get(schema.WINNERS, 0)} of {concentration.get(schema.TOTAL_POSITIONS, 0)}',
        percent(quality.get(schema.WIN_RATE_PCT), 0), delta_color='off',
    )
    c3.metric(
        'Income received', money(income.get(schema.TOTAL_INCOME)),
        f'{income.get(schema.INCOME_COUNT, 0)} payments', delta_color='off',
    )
    st.caption('More detail under **Performance** and **Cash Flows & Income**.')
