"""
Performance: returns, concentration, sector mix and the spread of outcomes.

Every value is read through a :mod:`portfolio.schema` constant. This page used to
read literal key names that analytics never emitted, so almost every metric
rendered as an em dash — and because the demo fixtures used the page's spelling
rather than the engine's, it looked fine in demo mode.
"""

import pandas as pd
import plotly.express as px
import streamlit as st

from portfolio import schema
from ui import theme
from ui.common import money, page_header, percent, require_portfolio

page_header('Performance', '🎯')

portfolio = require_portfolio()
report = portfolio.get('analytics_report') or {}
if not report:
    st.info('No analytics available. Refresh from the Overview page.')
    st.stop()

colours = theme.palette()
performance = report.get(schema.PERFORMANCE, {})
concentration = report.get(schema.CONCENTRATION, {})
sectors = report.get(schema.SECTORS, {})
quality = report.get(schema.HOLDINGS_QUALITY, {})
trading = report.get(schema.TRADING, {})

# ── Returns ───────────────────────────────────────────────────────────────────

st.subheader('Returns')

r1, r2, r3, r4 = st.columns(4)
r1.metric('Cost Basis', money(performance.get(schema.COST_BASIS), 0))
r2.metric('Portfolio Value', money(performance.get(schema.MARKET_VALUE), 0))
r3.metric(
    'Unrealised Gain', money(performance.get(schema.TOTAL_RETURN_DOLLARS), 0),
    percent(performance.get(schema.TOTAL_RETURN_PCT), signed=True),
)
r4.metric(
    'Deposit-Adj. Return',
    percent(performance.get(schema.DEPOSIT_ADJUSTED_RETURN_PCT)),
)

basis = performance.get(schema.DEPOSIT_ADJUSTED_RETURN_BASIS)
if basis:
    # The approximation is material enough that it belongs on the page, not in a
    # tooltip — a reader who does not know it can misread the number badly.
    st.caption(f'ℹ️ {basis}')

st.caption(
    'Unrealised gain compares current positions to what was paid for them and '
    'excludes cash from both sides. Deposit-adjusted return removes the effect of '
    'when you added or withdrew money, so contributions do not read as performance.'
)

st.markdown('---')

# ── Concentration ─────────────────────────────────────────────────────────────

st.subheader('Concentration')

c1, c2, c3, c4 = st.columns(4)
c1.metric(
    'HHI', f'{concentration.get(schema.HHI, 0):,.0f}',
    concentration.get(schema.HHI_INTERPRETATION, ''), delta_color='off',
    help='Sum of squared position weights. Under 1500 is well diversified; '
         'above 2500 is concentrated.',
)
c2.metric(
    'Effective positions', f'{concentration.get(schema.EFFECTIVE_POSITIONS, 0):,.1f}',
    f'of {concentration.get(schema.TOTAL_POSITIONS, 0)} held', delta_color='off',
    help='How many equally weighted positions would give the same concentration.',
)
c3.metric('Top 3 weight', percent(concentration.get(schema.TOP_3_PCT), 1),
          help='Share of invested value, excluding cash.')
c4.metric('Top 10 weight', percent(concentration.get(schema.TOP_10_PCT), 1),
          help='Share of invested value, excluding cash.')

st.caption('Weights below are shares of **invested value** — cash is excluded, '
           'since concentration is a question about how the invested money is spread.')

weights = concentration.get(schema.POSITION_WEIGHTS) or {}
if weights:
    frame = pd.DataFrame(
        list(weights.items()), columns=['Symbol', 'Weight']
    ).sort_values('Weight')
    fig = px.bar(frame, x='Weight', y='Symbol', orientation='h', text='Weight')
    fig.update_traces(
        marker_color=colours['primary'],
        marker_cornerradius=4,
        texttemplate='%{text:.1f}%', textposition='outside',
        hovertemplate='%{y}: %{x:.2f}% of portfolio<extra></extra>',
    )
    theme.apply_layout(fig, colours)
    fig.update_layout(height=max(280, 22 * len(frame)), yaxis_title=None, xaxis_title=None)
    fig.update_xaxes(showticklabels=False, range=[0, frame['Weight'].max() * 1.18])
    st.plotly_chart(fig, use_container_width=True)

st.markdown('---')

# ── Sector mix ────────────────────────────────────────────────────────────────

sector_weights = sectors.get(schema.SECTOR_WEIGHTS_PCT) or {}
if sector_weights:
    st.subheader('Sector mix')
    frame = pd.DataFrame(
        list(sector_weights.items()), columns=['Sector', 'Weight']
    ).sort_values('Weight')

    # Unclassified is a remainder, not a sector — neutral ink so it does not read
    # as an allocation decision.
    bar_colours = [
        colours['other'] if sector == 'Unclassified' else colours['primary']
        for sector in frame['Sector']
    ]
    fig = px.bar(frame, x='Weight', y='Sector', orientation='h', text='Weight')
    fig.update_traces(
        marker_color=bar_colours, marker_cornerradius=4,
        texttemplate='%{text:.1f}%', textposition='outside',
        hovertemplate='%{y}: %{x:.2f}% of portfolio<extra></extra>',
    )
    theme.apply_layout(fig, colours)
    fig.update_layout(height=max(240, 34 * len(frame)), yaxis_title=None, xaxis_title=None)
    fig.update_xaxes(showticklabels=False, range=[0, frame['Weight'].max() * 1.18])
    st.plotly_chart(fig, use_container_width=True)

    if 'Unclassified' in sector_weights:
        st.caption(
            f'{percent(sector_weights["Unclassified"], 1)} is unclassified — add those '
            'symbols to `config/sectors.json` to place them.'
        )

st.markdown('---')

# ── Spread of outcomes ────────────────────────────────────────────────────────

st.subheader('How positions are doing')

q1, q2, q3, q4 = st.columns(4)
q1.metric('In profit', quality.get(schema.WINNERS, 0),
          percent(quality.get(schema.WIN_RATE_PCT), 0), delta_color='off')
q2.metric('At a loss', quality.get(schema.LOSERS, 0))
q3.metric('Flat', quality.get(schema.BREAKEVEN, 0),
          help='Positions showing exactly no gain or loss — usually a price the '
               'data source could not refresh.')
q4.metric(
    'Spread of returns', percent(quality.get(schema.GAIN_DISPERSION_PCT), 1),
    help='Standard deviation of position lifetime gains. Descriptive only — these '
         'are not periodic returns, so this is not volatility and cannot be used '
         'for a Sharpe ratio.',
)

distribution = quality.get(schema.GAIN_DISTRIBUTION) or {}
if distribution:
    frame = pd.DataFrame(list(distribution.items()), columns=['Bucket', 'Positions'])
    # Ordered from worst to best, and coloured on the diverging scale by which
    # side of zero the bucket sits on.
    losing = frame['Bucket'].str.contains('loss', case=False)
    fig = px.bar(frame, x='Bucket', y='Positions', text='Positions')
    fig.update_traces(
        marker_color=[colours['negative'] if is_loss else colours['positive']
                      for is_loss in losing],
        marker_cornerradius=4, textposition='outside',
        hovertemplate='%{x}: %{y} position(s)<extra></extra>',
    )
    theme.apply_layout(fig, colours)
    fig.update_layout(height=320, xaxis_title=None, yaxis_title='Positions')
    st.plotly_chart(fig, use_container_width=True)

best = quality.get(schema.BEST_PERFORMER)
worst = quality.get(schema.WORST_PERFORMER)
if best and worst:
    b1, b2 = st.columns(2)
    b1.success(
        f'**Best:** {best["Symbol"]} · {percent(best["Gain %"], signed=True)} · '
        f'{money(best["Market Value"])}'
    )
    b2.error(
        f'**Worst:** {worst["Symbol"]} · {percent(worst["Gain %"], signed=True)} · '
        f'{money(worst["Market Value"])}'
    )

# ── Trading activity ──────────────────────────────────────────────────────────

if trading.get(schema.BUY_COUNT) or trading.get(schema.SELL_COUNT):
    st.markdown('---')
    st.subheader('Trading activity')
    t1, t2, t3, t4 = st.columns(4)
    t1.metric('Buys', trading.get(schema.BUY_COUNT, 0), money(trading.get(schema.TOTAL_BOUGHT)),
              delta_color='off')
    t2.metric('Sells', trading.get(schema.SELL_COUNT, 0), money(trading.get(schema.TOTAL_SOLD)),
              delta_color='off')
    t3.metric('Average trade', money(trading.get(schema.AVG_TRADE_SIZE)))
    t4.metric('Turnover', percent(trading.get(schema.TURNOVER_PCT), 1),
              help='Average of buys and sells, as a share of current position value.')

with st.expander('Full report (raw)'):
    st.json(report)
