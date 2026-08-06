"""
What-If: Hold — was selling the right call?

**The framing.** The number reported is *decision value*: proceeds minus what the
position would be worth today. Positive means selling beat holding; negative means
holding would have won. An earlier version measured the opposite direction, so a
"good" outcome was a negative number painted red — technically consistent, and
backwards from every other green-is-good number in the app.

**Percent leads, dollars follow.** A dollar difference mostly measures how large
the position was. Selling $80k of one stock badly and $800 of another badly are
the same decision, made at different sizes; the percentage is what says whether
the call was right.

Colour alone cannot carry this — the green/red pair sits at ΔE 7.2 for protanopia
(see :mod:`ui.theme`). Every mark is therefore also placed by sign against a zero
line, directly labelled with its value, and repeated in the table below.
"""

import datetime
import re

import pandas as pd
import plotly.express as px
import streamlit as st

from portfolio.market import cumulative_split_factor, get_current_prices, get_split_history
from ui import theme
from ui.common import money, page_header, percent, require_portfolio, signed_money

page_header(
    'What-If: Hold', '🔮',
    'What your sold positions would be worth today if you had kept them.',
)

transactions = require_portfolio('transactions')
colours = theme.palette()

# ── Range ─────────────────────────────────────────────────────────────────────

# Default to the whole sell history rather than year-to-date. Most accounts have
# no sales in the current calendar year, so the old default opened on "no sales in
# that date range" — which reads as a broken page rather than a narrow filter.
_sell_dates = pd.to_datetime(
    transactions.loc[transactions['Transaction Type'] == 'Sold', 'Date'], errors='coerce'
).dropna()
_earliest_sell = _sell_dates.min().date() if len(_sell_dates) else datetime.date(
    datetime.date.today().year, 1, 1)

c1, c2, c3 = st.columns([2, 2, 1])
with c1:
    start_date = st.date_input('From', value=_earliest_sell, key='wih_start')
with c2:
    end_date = st.date_input('To', value=datetime.date.today(), key='wih_end')
with c3:
    st.markdown('<div style="height:28px"></div>', unsafe_allow_html=True)
    run = st.button('Run analysis', type='primary', use_container_width=True)

if not run and 'wih_result' not in st.session_state:
    st.info('Choose a date range and select **Run analysis**.')
    st.stop()


def _symbol_from_description(description: str) -> str:
    """
    Recover a ticker from a sell description when the API did not supply one.

    Older cached pulls have no Symbol column; descriptions look like
    ``SOLD 100 SHOP AT 65.20``. Take the first uppercase token that is not a
    keyword.
    """
    if not description:
        return ''
    for token in description.strip().split():
        cleaned = re.sub(r'[^A-Z]', '', token.upper())
        if 1 <= len(cleaned) <= 5 and cleaned not in ('SOLD', 'BOUGHT', 'AT', 'OF', 'FOR'):
            return cleaned
    return ''


if run:
    frame = transactions.copy()
    frame['Date'] = pd.to_datetime(frame['Date'], errors='coerce')
    sells = frame[
        (frame['Category'] == 'Trade')
        & (frame['Transaction Type'] == 'Sold')
        & (frame['Date'].dt.date >= start_date)
        & (frame['Date'].dt.date <= end_date)
    ].copy()

    if sells.empty:
        st.info('No sales in that date range.')
        st.session_state.pop('wih_result', None)
        st.stop()

    if 'Symbol' not in sells.columns:
        sells['Symbol'] = ''
    missing = sells['Symbol'].isna() | (sells['Symbol'] == '')
    sells.loc[missing, 'Symbol'] = sells.loc[missing, 'Security Name'].apply(
        _symbol_from_description)
    sells = sells[sells['Symbol'].fillna('') != '']

    if sells.empty:
        st.warning('Could not identify the ticker for any sale in this range.')
        st.stop()

    # E*TRADE records sells with a negative quantity; work in magnitudes.
    sells['Quantity'] = pd.to_numeric(sells['Quantity'], errors='coerce').abs()
    sells['Total Value'] = pd.to_numeric(sells['Total Value'], errors='coerce').abs()

    tickers = tuple(sorted(sells['Symbol'].unique()))
    with st.spinner(f'Fetching prices and splits for {len(tickers)} symbol(s)…'):
        prices = get_current_prices(tickers)
        splits = get_split_history(tickers)

    # 100 shares sold before a 10:1 split are 1,000 shares today. Without this the
    # "if held" value is understated by the split ratio.
    sells['_factor'] = sells.apply(
        lambda row: cumulative_split_factor(
            splits.get(row['Symbol'], pd.Series(dtype=float)),
            pd.Timestamp(row['Date']).date(),
        ), axis=1,
    )
    sells['_equivalent_shares'] = sells['Quantity'] * sells['_factor']

    aggregated = sells.groupby('Symbol').agg(
        Description=('Security Name', 'first'),
        Shares=('Quantity', 'sum'),
        Equivalent=('_equivalent_shares', 'sum'),
        Proceeds=('Total Value', 'sum'),
        Sales=('Date', 'count'),
        First=('Date', 'min'),
        Last=('Date', 'max'),
    ).reset_index()

    aggregated['Split factor'] = (aggregated['Equivalent'] / aggregated['Shares']).round(4)
    aggregated['Avg sale price'] = aggregated['Proceeds'] / aggregated['Shares']
    aggregated['Price now'] = aggregated['Symbol'].map(prices)
    aggregated['Value if held'] = aggregated['Equivalent'] * aggregated['Price now']

    # Decision value: positive means the sale beat holding.
    aggregated['Decision $'] = aggregated['Proceeds'] - aggregated['Value if held']
    aggregated['Decision %'] = aggregated['Decision $'] / aggregated['Proceeds'] * 100

    st.session_state['wih_result'] = aggregated

aggregated = st.session_state.get('wih_result')
if aggregated is None or aggregated.empty:
    st.stop()

priced = aggregated[aggregated['Price now'].notna()].copy()
unpriced = aggregated[aggregated['Price now'].isna()]
if priced.empty:
    st.warning('No current prices available for these symbols.')
    st.stop()

# ── Headline ──────────────────────────────────────────────────────────────────

proceeds = priced['Proceeds'].sum()
if_held = priced['Value if held'].sum()
decision_dollars = proceeds - if_held
decision_pct = decision_dollars / proceeds * 100 if proceeds else 0

st.markdown('---')

k1, k2, k3 = st.columns([2, 1, 1])
# No st.metric delta here: its arrow always points up for a text delta, which on a
# negative result contradicted the number right above it.
k1.metric('Selling vs holding', percent(decision_pct, signed=True))
k1.caption(
    f'**{"Selling came out ahead" if decision_dollars >= 0 else "Holding would have won"}** '
    f'by {money(abs(decision_dollars), 0)} across {len(priced)} symbol(s).'
)
k2.metric('Proceeds received', money(proceeds, 0))
k3.metric('Worth today if held', money(if_held, 0))

if not unpriced.empty:
    st.caption(
        f'No current price for {", ".join(unpriced["Symbol"])} — delisted, renamed, or '
        'not covered by the price source. Excluded from every figure above.'
    )

st.markdown('---')

# ── Chart ─────────────────────────────────────────────────────────────────────

st.markdown('##### By symbol')

# A 2% swing is noise on a position sold months ago; painting it green or red
# implies a signal that is not there.
NEUTRAL_BAND = 2.0

chart = priced.sort_values('Decision %').copy()
chart['Label'] = chart['Decision %'].map(lambda v: f'{v:+.1f}%')

fig = px.bar(chart, x='Symbol', y='Decision %', text='Label',
             custom_data=['Proceeds', 'Value if held', 'Decision $'])
fig.update_traces(
    marker_color=theme.signed_colours(chart['Decision %'], colours, NEUTRAL_BAND),
    marker_cornerradius=4,
    marker_line_color=colours['surface'], marker_line_width=2,
    textposition='outside', cliponaxis=False,
    hovertemplate=(
        '<b>%{x}</b><br>Sold for $%{customdata[0]:,.0f}<br>'
        'Worth $%{customdata[1]:,.0f} today<br>'
        'Difference $%{customdata[2]:,.0f} (%{y:+.1f}%)<extra></extra>'
    ),
)
theme.apply_layout(fig, colours, y_suffix='%')
fig.add_hline(y=0, line_width=1, line_color=colours['reference'])
span = max(chart['Decision %'].abs().max(), 1) * 1.25
fig.update_layout(height=380, xaxis_title=None, yaxis_title=None, showlegend=False)
fig.update_yaxes(range=[-span, span])
st.plotly_chart(fig, use_container_width=True)

st.caption(
    f'Above the line: selling was the better call. Below: holding would have won. '
    f'Grey marks are within ±{NEUTRAL_BAND:.0f}%, too close to call.'
)

st.markdown('---')

# ── Table ─────────────────────────────────────────────────────────────────────

st.markdown('##### Detail')

has_splits = (priced['Split factor'] != 1.0).any()
columns = ['Symbol', 'Decision %', 'Decision $', 'Shares']
if has_splits:
    columns += ['Split factor', 'Equivalent']
columns += ['Avg sale price', 'Proceeds', 'Price now', 'Value if held', 'Sales']

table = priced.sort_values('Decision %', ascending=False)[columns].rename(columns={
    'Decision %': 'Decision (%)', 'Decision $': 'Decision ($)',
    'Shares': 'Shares sold', 'Equivalent': 'Shares today',
    'Proceeds': 'Total proceeds', 'Value if held': 'Worth today',
})


def _percent_ink(value):
    """Match the chart: neutral inside the band, otherwise coloured by sign."""
    if pd.isna(value):
        return ''
    if abs(value) <= NEUTRAL_BAND:
        return f'color: {colours["neutral"]}'
    return f'color: {colours["positive"] if value > 0 else colours["negative"]}'


def _dollar_ink(value):
    """Dollars have no meaningful noise band — colour purely by sign."""
    if pd.isna(value) or value == 0:
        return ''
    return f'color: {colours["positive"] if value > 0 else colours["negative"]}'


st.dataframe(
    table.style
    .map(_percent_ink, subset=['Decision (%)'])
    .map(_dollar_ink, subset=['Decision ($)'])
    .format({
        'Decision (%)': '{:+.2f}%',
        'Decision ($)': lambda v: signed_money(v, 0),
        'Shares sold': '{:,.4g}', 'Shares today': '{:,.4g}', 'Split factor': '{:.4g}×',
        'Avg sale price': '${:,.2f}', 'Total proceeds': '${:,.0f}',
        'Price now': '${:,.2f}', 'Worth today': '${:,.0f}',
    }, na_rep='—'),
    use_container_width=True, hide_index=True,
)

if has_splits:
    split_note = ', '.join(
        f'{row["Symbol"]} ({row["Split factor"]:.4g}×)'
        for _, row in priced[priced['Split factor'] != 1.0].iterrows()
    )
    st.caption(
        f'⚡ Split-adjusted: {split_note}. Shares today reflects splits since the sale, '
        'so "worth today" compares like with like.'
    )
