"""Holdings: every position, with allocation and gain/loss charts."""

import pandas as pd
import plotly.express as px
import streamlit as st

from portfolio import schema
from ui import theme
from ui.common import money, page_header, percent, require_portfolio, signed_money

page_header('Holdings', '📊')

portfolio = require_portfolio()
holdings = require_portfolio('holdings')
summary = portfolio.get('summary') or {}
report = portfolio.get('analytics_report') or {}
colours = theme.palette()

k1, k2, k3, k4 = st.columns(4)
k1.metric('Portfolio Value', money(summary.get('Total Portfolio Value'), 0))
k2.metric(
    'Unrealised Gain', money(summary.get('Total Unrealized Gain'), 0),
    percent(summary.get('Total Unrealized Gain %'), signed=True),
)
k3.metric('Cash', money(summary.get('Cash'), 0),
          f'{percent(summary.get("Cash Percentage"), 1)} of portfolio', delta_color='off')
k4.metric('Positions', summary.get('Total Stocks', '—'))

st.markdown('---')

positions = holdings[holdings['Symbol'] != 'CASH'].copy()
cash_value = float(holdings[holdings['Symbol'] == 'CASH']['Market Value'].sum())

# ── Table ─────────────────────────────────────────────────────────────────────

st.subheader('All positions')

columns = [c for c in [
    'Symbol', 'Symbol Description', 'Quantity', 'Current Price',
    'Market Value', 'Total Cost', 'Total Gain', 'Total Gain %', 'Percent of Portfolio',
] if c in positions.columns]

table = positions[columns].sort_values('Market Value', ascending=False)


def _gain_ink(value):
    """Green above zero, red below. The signed number is always shown too."""
    if pd.isna(value) or value == 0:
        return ''
    return f'color: {colours["positive"] if value > 0 else colours["negative"]}'


st.dataframe(
    table.style
    .map(_gain_ink, subset=[c for c in ('Total Gain', 'Total Gain %') if c in columns])
    .format({
        'Quantity': '{:,.4g}', 'Current Price': '${:,.2f}', 'Market Value': '${:,.2f}',
        'Total Cost': '${:,.2f}', 'Total Gain': signed_money, 'Total Gain %': '{:+.2f}%',
        'Percent of Portfolio': '{:.2f}%',
    }, na_rep='—'),
    use_container_width=True, hide_index=True,
)

if cash_value:
    st.caption(f'Plus {money(cash_value)} in cash, not shown in the table above.')

st.markdown('---')

# ── Charts ────────────────────────────────────────────────────────────────────

left, right = st.columns(2)

with left:
    st.subheader('Allocation')
    top = positions.nlargest(8, 'Market Value')[['Symbol', 'Market Value']].copy()
    remainder = positions['Market Value'].sum() - top['Market Value'].sum()
    for label, value in (('Other', remainder), ('Cash', cash_value)):
        if value > 0:
            top = pd.concat(
                [top, pd.DataFrame([{'Symbol': label, 'Market Value': value}])],
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
    fig.update_layout(showlegend=False, margin=dict(t=0, b=0, l=0, r=0),
                      height=380, paper_bgcolor='rgba(0,0,0,0)')
    st.plotly_chart(fig, use_container_width=True)

with right:
    sector_weights = (report.get(schema.SECTORS) or {}).get(schema.SECTOR_WEIGHTS_PCT) or {}
    if sector_weights:
        st.subheader('By sector')
        frame = pd.DataFrame(
            list(sector_weights.items()), columns=['Sector', 'Weight']
        ).sort_values('Weight')
        bar_colours = [
            colours['other'] if sector == 'Unclassified' else colours['primary']
            for sector in frame['Sector']
        ]
        fig = px.bar(frame, x='Weight', y='Sector', orientation='h', text='Weight')
        fig.update_traces(
            marker_color=bar_colours, marker_cornerradius=4,
            texttemplate='%{text:.1f}%', textposition='outside',
            hovertemplate='%{y}: %{x:.2f}%<extra></extra>',
        )
        theme.apply_layout(fig, colours)
        fig.update_layout(height=380, yaxis_title=None, xaxis_title=None)
        fig.update_xaxes(showticklabels=False, range=[0, frame['Weight'].max() * 1.2])
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.subheader('Gain and loss by position')
        frame = positions[['Symbol', 'Total Gain']].sort_values('Total Gain')
        fig = px.bar(frame, x='Total Gain', y='Symbol', orientation='h')
        fig.update_traces(
            marker_color=theme.signed_colours(frame['Total Gain'], colours),
            marker_cornerradius=4,
            hovertemplate='%{y}: $%{x:,.0f}<extra></extra>',
        )
        theme.apply_layout(fig, colours, y_prefix='')
        fig.add_vline(x=0, line_width=1, line_color=colours['reference'])
        fig.update_layout(height=380, yaxis_title=None, xaxis_title='Gain / loss ($)')
        fig.update_xaxes(tickprefix='$', tickformat=',.0f')
        st.plotly_chart(fig, use_container_width=True)
