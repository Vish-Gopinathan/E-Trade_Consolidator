"""
Performance: returns, concentration, sector mix and the spread of outcomes.

Every value is read through a :mod:`portfolio.schema` constant. This page used to
read literal key names that analytics never emitted, so almost every metric
rendered as an em dash — and because the demo fixtures used the page's spelling
rather than the engine's, it looked fine in demo mode.
"""

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from portfolio import history as ph
from portfolio import periods as pp
from portfolio import schema
from ui import rebuild, theme
from ui.common import md, money, page_header, percent, require_portfolio

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

r1, r2, r3, r4, r5 = st.columns(5)
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
# The two adjusted figures answer different questions and are easy to conflate:
# one is the whole period, the other is per year. Labelled so the difference is
# visible without reading the captions.
annualised = performance.get(schema.ANNUALISED_RETURN_PCT)
r5.metric('Annual Growth Rate', percent(annualised))
r5.caption('per year' if annualised is not None else 'needs 1 year of history')

for key in (schema.DEPOSIT_ADJUSTED_RETURN_BASIS, schema.ANNUALISED_RETURN_BASIS):
    basis = performance.get(key)
    if basis:
        # These approximations are material enough to belong on the page rather
        # than in a tooltip — a reader who does not know them can misread the
        # numbers badly.
        st.caption(md(f'ℹ️ {basis}'))

st.caption(
    'Unrealised gain compares current positions to what was paid for them and '
    'excludes cash from both sides. **Deposit-adjusted return** is cumulative — '
    'the whole period, however long that is. **Annual growth rate** is the same '
    'money expressed as a yearly rate, which is the form comparable to a savings '
    'rate or an index. Both remove the effect of when you added money, so '
    'contributions do not read as performance.'
)

st.markdown('---')

# ── Performance over time ─────────────────────────────────────────────────────
#
# The two figures above are anchored to the first cash flow on record, so
# neither can answer "how did this do since January". This section can, using a
# time-weighted return over the daily value series portfolio.history rebuilds.
# See portfolio/periods.py for why time-weighted here and money-weighted there.

st.subheader('Performance over time')
st.caption(
    'How the **investments** did between two dates, with deposits and '
    'withdrawals divided out. Each day is measured against its own opening '
    'balance and the days are chained together, so money arriving or leaving '
    'changes the balance without touching the return. This is the only figure '
    'on this page that can fairly be set beside an index — the two above count '
    'the timing of your deposits on purpose, and a published index has none.'
)

resolution = rebuild.resolve(portfolio)
history_result = None

if resolution.empty:
    st.info(
        'No buy/sell transactions on record, so there is no daily history to '
        'measure period returns against.'
    )
else:
    history_result = rebuild.daily_history(
        portfolio, resolution, resolution.first_trade, ph.anchor_date(portfolio),
        key='_perf', label='Measuring period returns…',
    )[0]

if history_result is not None and len(history_result.total) < 2:
    st.info('Not enough reconstructed trading days yet to measure a period return.')
    history_result = None

if history_result is not None:
    anchor = ph.anchor_date(portfolio)
    trailing = pp.period_returns(history_result, anchor)
    measurable = [p for p in trailing if p.ok]

    if not measurable:
        st.warning(
            'None of the standard periods could be measured. '
            + (trailing[0].note if trailing else '')
        )
    else:
        # ── Anything that actively distorts the figures below ─────────────────
        #
        # These two do not merely add uncertainty, they bend the numbers in a
        # specific direction, so they belong on the page rather than inside the
        # expander at the bottom of the section.
        diagnostics = history_result.diagnostics or {}
        distorting = []
        if diagnostics.get('negative_share_symbols'):
            distorting.append(
                '**' + ', '.join(diagnostics['negative_share_symbols'])
                + '** reconstruct to a *negative* share count: more was sold than '
                'the history says was bought. The position is then valued at zero '
                'while the cash from those trades is still counted, which shows up '
                'as a cliff in the return on the days they traded.'
            )
        if diagnostics.get('unpriced_symbols'):
            distorting.append(
                'No price could be found for **'
                + ', '.join(diagnostics['unpriced_symbols'])
                + '**, so those positions are worth zero throughout and every '
                'return below is understated.'
            )
        if distorting:
            st.warning(md(
                'These returns rest on a reconstruction that does not fully '
                'reconcile:\n\n' + '\n\n'.join(f'- {d}' for d in distorting)
                + '\n\nValue Over Time → **Data quality** has the detail.'
            ))

        # ── Headline periods ──────────────────────────────────────────────────
        #
        # The return is the metric *value*, never a delta: st.metric's delta
        # arrow always points up for a text delta, which would render a losing
        # quarter as a rise.
        headline_labels = ['1 month', '3 months', 'Year to date', '1 year']
        headline = [p for p in measurable if p.label in headline_labels]
        headline += [p for p in measurable if p.label.startswith('Since')]
        # A history too short for any of the named windows still has something
        # to say, and st.columns(0) raises.
        headline = headline or measurable[:5]

        for column, period in zip(st.columns(len(headline)), headline):
            column.metric(period.label, percent(period.return_pct, 1, signed=True))
            column.caption(md(
                f'{money(period.market_gain, 0)} of market gain'
                + (f' · {percent(period.annualised_pct, 1, signed=True)} a year'
                   if period.annualised_pct is not None else '')
            ))

        # ── The deposit-agnostic equity curve ─────────────────────────────────

        chartable = [p for p in measurable if len(p.index) > 2]
        if chartable:
            default = next(
                (i for i, p in enumerate(chartable) if p.label == 'Year to date'),
                len(chartable) - 1,
            )
            chosen_label = st.columns([1.4, 3])[0].selectbox(
                'Period', [p.label for p in chartable], index=default,
                key='_perf_curve_period',
            )
            chosen = next(p for p in chartable if p.label == chosen_label)

            curve = chosen.index - 100.0
            fig = go.Figure()
            fig.add_trace(go.Scatter(
                x=curve.index, y=curve.values, mode='lines', name='Return',
                line=dict(color=colours['primary'], width=2),
                hovertemplate='%{x|%b %d, %Y}<br><b>%{y:+.2f}%</b><extra></extra>',
            ))
            fig.add_hline(y=0, line_width=1, line_dash='dot',
                          line_color=colours['reference'])
            theme.apply_layout(fig, colours, y_suffix='%')
            fig.update_layout(height=340, margin=dict(t=20, b=10, l=0, r=10),
                              showlegend=False, hovermode='x unified')
            st.plotly_chart(fig, use_container_width=True)

            st.caption(md(
                f'Cumulative return from the close of '
                f'{chosen.base_date:%b %d, %Y} to {chosen.end:%b %d, %Y}, '
                f'over {chosen.observations:,} trading days. '
                f'{money(chosen.net_flow, 0)} of net deposits moved through the '
                'account in that window and are divided out of every point on '
                'this line — it moves only when the investments do.'
            ))

        # ── Every period, with the numbers behind the chart ───────────────────

        table = pd.DataFrame([
            {
                'Period': p.label,
                'Return': percent(p.return_pct, 2, signed=True) if p.ok else '—',
                'Per year': percent(p.annualised_pct, 2, signed=True)
                            if p.annualised_pct is not None else '—',
                'Market gain': money(p.market_gain, 0) if p.ok else '—',
                'Net deposits': money(p.net_flow, 0) if p.ok else '—',
                'Volatility': percent(p.volatility_pct, 1)
                              if p.volatility_pct is not None else '—',
                'Max drawdown': percent(p.max_drawdown_pct, 1)
                                if p.max_drawdown_pct is not None else '—',
                'From': f'{p.base_date:%b %d, %Y}' if p.base_date else '—',
                'Days': f'{p.observations:,}',
            }
            for p in trailing
        ])
        st.dataframe(table, hide_index=True, use_container_width=True)

        st.caption(
            '**Return and market gain can disagree in sign**, and that is the '
            'point of having both: the dollar figure counts when the money was '
            'in the account, and the percentage deliberately does not. A year '
            'that fell while the balance was small and recovered after a large '
            'deposit ends up ahead in dollars while every dollar invested '
            'throughout it lost ground.'
        )
        st.caption(
            '**Per year** is blank below a year of history: annualising a partial '
            'year exaggerates it in both directions. **Volatility** is the '
            'annualised spread of the same daily returns — legitimate here, '
            'unlike the position-level dispersion further down this page, '
            'because these *are* periodic returns. **Max drawdown** is the worst '
            'fall in this return line, so a withdrawal does not count as one; '
            'Value Over Time reports the other kind, against the balance.'
        )

        # ── Calendar years ────────────────────────────────────────────────────

        years = [p for p in pp.year_returns(history_result, anchor) if p.ok]
        if len(years) > 1:
            st.markdown('##### By calendar year')
            frame = pd.DataFrame(
                [{'Year': p.label, 'Return': p.return_pct} for p in years]
            )
            losing = frame['Return'] < 0
            fig = px.bar(frame, x='Year', y='Return', text='Return')
            fig.update_traces(
                marker_color=[colours['negative'] if bad else colours['positive']
                              for bad in losing],
                marker_cornerradius=4, texttemplate='%{text:+.1f}%',
                textposition='outside',
                hovertemplate='%{x}: %{y:+.2f}%<extra></extra>',
            )
            fig.add_hline(y=0, line_width=1, line_color=colours['reference'])
            theme.apply_layout(fig, colours, y_suffix='%')
            fig.update_layout(height=300, xaxis_title=None, yaxis_title=None,
                              margin=dict(t=30, b=10, l=0, r=10))
            # Most labels look like numbers, so plotly infers a linear axis and
            # then has nowhere to put "2010 (from Jan 15)" or "2026 (to date)" —
            # the two bars that most need their own slot.
            fig.update_xaxes(type='category')
            st.plotly_chart(fig, use_container_width=True)

            st.dataframe(
                pd.DataFrame([
                    {
                        'Year': p.label,
                        'Return': percent(p.return_pct, 2, signed=True),
                        'Market gain': money(p.market_gain, 0),
                        'Net deposits': money(p.net_flow, 0),
                        'Max drawdown': percent(p.max_drawdown_pct, 1)
                                        if p.max_drawdown_pct is not None else '—',
                        'Trading days': f'{p.observations:,}',
                    }
                    for p in years
                ]),
                hide_index=True, use_container_width=True,
            )

        # ── What these figures rest on ────────────────────────────────────────

        with st.expander('How far to trust these numbers'):
            residual = diagnostics.get('residual_shares')
            notes = [
                'Every figure here is rebuilt from transaction history and daily '
                'closes, walking share counts **backwards** from today. Today is '
                'exact by construction and the reconstruction gets looser the '
                'further back it reaches, so the short periods are the most '
                'trustworthy figures on this page and the longest the least.',
                'Deposits and withdrawals are assumed to land at the **close** of '
                'their day, so they do not earn that day\'s return. E\\*TRADE '
                'dates a transfer to the day, not the minute, so the alternative '
                'is equally arbitrary; it affects only the day of the flow.',
                'Cash is included in the balance. It has to be — a deposit lands '
                'as cash, and measuring positions alone would read it as a loss. '
                'That does mean an idle cash pile drags these returns down, '
                'which is a real property of the portfolio rather than an '
                'artefact.',
            ]

            missing_flows = (report.get(schema.CASH_FLOWS) or {}).get(
                schema.FLOWS_NEEDING_REVIEW
            )
            if missing_flows:
                notes.append(
                    f'**{missing_flows} transfer(s) are still unclassified** and are '
                    'being counted as external money. If any of them is actually a '
                    'move between your own accounts, it is being divided out of '
                    'these returns when it should not be. Tag them under Cash '
                    'Flows & Income → Transfer Review.'
                )

            dropped = [
                label for label, _ in pp.trailing_windows(anchor)
                if label not in {p.label for p in trailing}
            ]
            if dropped:
                notes.append(
                    f'**{", ".join(dropped)}** are not listed: the reconstructed '
                    'history does not reach that far back. Showing them would put '
                    'a shorter period under a longer label.'
                )

            if residual is not None and len(residual):
                notes.append(
                    f'**{len(residual)} symbol(s) carry a residual share count** '
                    'before the first transaction on record, which means shares '
                    'existed that the feed does not explain. The earliest periods '
                    'inherit that error. See Value Over Time → Data quality.'
                )
            if diagnostics.get('missing_price_days'):
                notes.append(
                    f'**{diagnostics["missing_price_days"]:,} position-days have no '
                    'price**, usually before a symbol\'s data begins. Value is '
                    'understated on those days.'
                )

            # Anything a specific window had to say about itself — a start that
            # had to move, a chain that could not be linked. These are about one
            # row of the table each, so they belong beside the general caveats
            # rather than crowding the table with a notes column.
            for period in trailing + years:
                if period.note:
                    notes.append(f'**{period.label}:** {period.note}')

            for note in notes:
                st.markdown(md(f'- {note}'))

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
