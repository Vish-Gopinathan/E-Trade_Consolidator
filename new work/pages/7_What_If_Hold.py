import re
import sys
import datetime
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st

sys.path.insert(0, str(Path(__file__).parent.parent))
from lib.common import require_auth, render_sidebar_status
from lib.news_fetcher import get_current_prices

st.set_page_config(page_title='What-If: Hold Analysis', page_icon='🔮', layout='wide')
require_auth()

with st.sidebar:
    st.title('📈 Portfolio')
    render_sidebar_status()

st.title('🔮 What-If: Hold Analysis')
st.caption(
    'See what your sold positions would be worth today if you had held them. '
    'Positive difference = you left gains on the table. Negative = good timing.'
)

portfolio = st.session_state.get('portfolio')
if not portfolio:
    st.warning('No data loaded. Go to the home page and refresh.')
    st.stop()

transactions_df = portfolio.get('transactions')
if transactions_df is None or transactions_df.empty:
    st.info('No transaction data available. Refresh data from the home page.')
    st.stop()

# ── Date range ─────────────────────────────────────────────────────────────────

st.markdown('### Date Range')
col1, col2, col3 = st.columns([2, 2, 1])
with col1:
    default_start = datetime.date(datetime.date.today().year, 1, 1)
    start_date = st.date_input('From', value=default_start, key='wih_start')
with col2:
    end_date = st.date_input('To', value=datetime.date.today(), key='wih_end')
with col3:
    st.markdown('<div style="margin-top:28px"></div>', unsafe_allow_html=True)
    run = st.button('Run Analysis', type='primary')

if not run and 'wih_result' not in st.session_state:
    st.info('Choose a date range and click **Run Analysis**.')
    st.stop()

# ── Filter sold trades ─────────────────────────────────────────────────────────

if run:
    # Normalise date column
    df = transactions_df.copy()
    if not pd.api.types.is_datetime64_any_dtype(df['Date']):
        df['Date'] = pd.to_datetime(df['Date'], errors='coerce')

    mask = (
        (df['Category'] == 'Trade') &
        (df['Transaction Type'] == 'Sold') &
        (df['Date'].dt.date >= start_date) &
        (df['Date'].dt.date <= end_date)
    )
    sells = df[mask].copy()

    if sells.empty:
        st.info('No sell transactions found in the selected date range.')
        st.session_state.pop('wih_result', None)
        st.stop()

    # ── Resolve ticker symbols ─────────────────────────────────────────────────

    def _parse_symbol_from_description(desc: str) -> str:
        """
        Fallback: E*TRADE sell descriptions are typically
        'SOLD {qty} {TICKER} AT {price}' or 'SOLD {qty} {TICKER} @ {price}'.
        Take the third whitespace-separated token if it looks like a ticker.
        """
        if not desc:
            return ''
        tokens = desc.strip().split()
        candidates = []
        for tok in tokens:
            # Strip trailing punctuation, keep only uppercase letters
            clean = re.sub(r'[^A-Z]', '', tok.upper())
            if 1 <= len(clean) <= 5 and clean not in ('SOLD', 'BOUGHT', 'AT', 'OF', 'FOR'):
                candidates.append(clean)
        # The first candidate after filtering keywords is usually the ticker
        return candidates[0] if candidates else ''

    if 'Symbol' not in sells.columns:
        sells['Symbol'] = ''

    missing_symbol = sells['Symbol'].isna() | (sells['Symbol'] == '')
    if missing_symbol.any():
        sells.loc[missing_symbol, 'Symbol'] = sells.loc[missing_symbol, 'Security Name'].apply(
            _parse_symbol_from_description
        )

    # Drop rows where we still can't determine the symbol
    sells = sells[sells['Symbol'].notna() & (sells['Symbol'] != '')].copy()

    if sells.empty:
        st.warning('Could not determine ticker symbols for any sell transactions in this range.')
        st.stop()

    # ── Aggregate by symbol ────────────────────────────────────────────────────

    sells['Quantity'] = pd.to_numeric(sells['Quantity'], errors='coerce')
    sells['Total Value'] = pd.to_numeric(sells['Total Value'], errors='coerce')

    agg = sells.groupby('Symbol').agg(
        Description=('Security Name', 'first'),
        Shares_Sold=('Quantity', 'sum'),
        Total_Proceeds=('Total Value', 'sum'),
        First_Sell=('Date', 'min'),
        Last_Sell=('Date', 'max'),
        Num_Transactions=('Date', 'count'),
    ).reset_index()

    agg['Avg_Sale_Price'] = agg['Total_Proceeds'] / agg['Shares_Sold']

    # ── Fetch current prices ───────────────────────────────────────────────────

    symbols_tuple = tuple(sorted(agg['Symbol'].tolist()))
    with st.spinner(f'Fetching current prices for {len(symbols_tuple)} symbol(s)…'):
        prices = get_current_prices(symbols_tuple)

    agg['Current_Price'] = agg['Symbol'].map(prices)
    agg['Current_Value'] = agg['Shares_Sold'] * agg['Current_Price']
    agg['Difference'] = agg['Current_Value'] - agg['Total_Proceeds']
    agg['Difference_Pct'] = (agg['Difference'] / agg['Total_Proceeds']) * 100

    st.session_state['wih_result'] = agg

# ── Display ────────────────────────────────────────────────────────────────────

agg = st.session_state.get('wih_result')
if agg is None or agg.empty:
    st.stop()

valid = agg[agg['Current_Price'].notna()]
unavailable = agg[agg['Current_Price'].isna()]

# Summary KPIs
total_proceeds = valid['Total_Proceeds'].sum()
total_current = valid['Current_Value'].sum()
net_diff = valid['Difference'].sum()
net_diff_pct = (net_diff / total_proceeds * 100) if total_proceeds else 0

st.markdown('---')
st.markdown('### Summary')

k1, k2, k3, k4 = st.columns(4)
k1.metric('Total Proceeds (sold)', f'${total_proceeds:,.2f}')
k2.metric('Current Value if Held', f'${total_current:,.2f}')
k3.metric(
    'Net Difference',
    f'${net_diff:,.2f}',
    delta=f'{net_diff_pct:+.2f}%',
    delta_color='inverse',  # positive diff (left money on table) shows red
)
k4.metric('Symbols Analysed', len(valid))

if not unavailable.empty:
    st.caption(
        f"⚠️ No current price available for: {', '.join(unavailable['Symbol'].tolist())} "
        f"(delisted or ticker changed) — excluded from totals."
    )

st.markdown('---')
st.markdown('### Position Detail')

display = valid[[
    'Symbol', 'Description', 'Shares_Sold', 'Avg_Sale_Price',
    'Total_Proceeds', 'Current_Price', 'Current_Value',
    'Difference', 'Difference_Pct',
]].copy()

display.columns = [
    'Symbol', 'Description', 'Shares Sold', 'Avg Sale Price',
    'Total Proceeds', 'Current Price', 'Current Value if Held',
    'Difference ($)', 'Difference (%)',
]


def _color_diff(val):
    if val > 0:
        return 'color: #c0392b'   # red — you left gains on the table
    if val < 0:
        return 'color: #27ae60'   # green — good timing
    return ''


styled = display.style.applymap(
    _color_diff, subset=['Difference ($)', 'Difference (%)']
).format({
    'Shares Sold': '{:,.4g}',
    'Avg Sale Price': '${:,.2f}',
    'Total Proceeds': '${:,.2f}',
    'Current Price': '${:,.2f}',
    'Current Value if Held': '${:,.2f}',
    'Difference ($)': '${:+,.2f}',
    'Difference (%)': '{:+.2f}%',
}, na_rep='N/A')

st.dataframe(styled, use_container_width=True, hide_index=True)

# ── Bar chart ──────────────────────────────────────────────────────────────────

st.markdown('---')
st.markdown('### By Symbol')

chart_df = valid[['Symbol', 'Difference']].sort_values('Difference')
chart_df['Color'] = chart_df['Difference'].apply(
    lambda v: 'Left on table' if v > 0 else 'Good timing'
)

fig = px.bar(
    chart_df,
    x='Symbol',
    y='Difference',
    color='Color',
    color_discrete_map={'Left on table': '#e74c3c', 'Good timing': '#2ecc71'},
    labels={'Difference': 'Difference ($)', 'Symbol': ''},
    text=chart_df['Difference'].apply(lambda v: f'${v:+,.0f}'),
)
fig.update_traces(textposition='outside')
fig.add_hline(y=0, line_dash='dash', line_color='grey', line_width=1)
fig.update_layout(
    showlegend=True,
    legend_title_text='',
    margin=dict(t=20, b=20),
    yaxis_tickprefix='$',
    yaxis_tickformat=',.0f',
)
st.plotly_chart(fig, use_container_width=True)

# ── Sell transaction detail ────────────────────────────────────────────────────

with st.expander('View individual sell transactions'):
    raw_sells = st.session_state.get('portfolio', {}).get('transactions', pd.DataFrame())
    if not raw_sells.empty:
        df_raw = raw_sells.copy()
        if not pd.api.types.is_datetime64_any_dtype(df_raw['Date']):
            df_raw['Date'] = pd.to_datetime(df_raw['Date'], errors='coerce')
        mask = (
            (df_raw['Category'] == 'Trade') &
            (df_raw['Transaction Type'] == 'Sold') &
            (df_raw['Date'].dt.date >= start_date) &
            (df_raw['Date'].dt.date <= end_date)
        )
        detail = df_raw[mask][['Date', 'Security Name', 'Quantity', 'Price', 'Total Value']].copy()
        detail['Date'] = detail['Date'].dt.date
        st.dataframe(detail, use_container_width=True, hide_index=True)
