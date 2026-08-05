import datetime
import streamlit as st
import pandas as pd
from ui.common import require_auth, render_sidebar_status

st.set_page_config(page_title='Transactions', page_icon='🔄', layout='wide')
require_auth()

with st.sidebar:
    st.title('📈 Portfolio')
    render_sidebar_status()

st.title('🔄 Transaction History')

portfolio = st.session_state.get('portfolio')
if not portfolio:
    st.warning('No data loaded. Go to the home page and refresh.')
    st.stop()

txns = portfolio.get('transactions')
if txns is None or (hasattr(txns, 'empty') and txns.empty):
    st.info('No transaction data available.')
    st.stop()

df = txns.copy()
if 'Date' in df.columns:
    df['Date'] = pd.to_datetime(df['Date'], errors='coerce')

# ── Filters ───────────────────────────────────────────────────────────────────
with st.expander('Filters', expanded=True):
    fc1, fc2, fc3 = st.columns(3)

    with fc1:
        if 'Date' in df.columns and df['Date'].notna().any():
            min_date = df['Date'].min().date()
            max_date = df['Date'].max().date()
            date_range = st.date_input(
                'Date range',
                value=(min_date, max_date),
                min_value=datetime.date(1990, 1, 1),
                max_value=datetime.date.today(),
            )
        else:
            date_range = None

    with fc2:
        categories = ['All'] + sorted(df['Category'].dropna().unique().tolist()) if 'Category' in df.columns else ['All']
        selected_cats = st.multiselect('Category', options=categories[1:], default=[])

    with fc3:
        search = st.text_input('Search (security name)', '')

# Apply filters
filtered = df.copy()
if date_range and len(date_range) == 2 and 'Date' in filtered.columns:
    start, end = pd.Timestamp(date_range[0]), pd.Timestamp(date_range[1])
    filtered = filtered[(filtered['Date'] >= start) & (filtered['Date'] <= end)]

if selected_cats and 'Category' in filtered.columns:
    filtered = filtered[filtered['Category'].isin(selected_cats)]

if search and 'Security Name' in filtered.columns:
    filtered = filtered[filtered['Security Name'].str.contains(search, case=False, na=False)]

# ── Table ─────────────────────────────────────────────────────────────────────
st.markdown(f'**{len(filtered):,} transaction(s)**')

display_cols = [c for c in [
    'Date', 'Security Name', 'Transaction Type', 'Category',
    'Quantity', 'Price', 'Total Value',
] if c in filtered.columns]

page_size = st.select_slider('Rows per page', options=[25, 50, 100, 200], value=50)
total_pages = max(1, (len(filtered) + page_size - 1) // page_size)
page = st.number_input('Page', min_value=1, max_value=total_pages, value=1, step=1)

start_idx = (page - 1) * page_size
end_idx = start_idx + page_size
page_df = filtered[display_cols].iloc[start_idx:end_idx]

page_df = page_df.copy()
for col in ('Total Value', 'Price', 'Quantity'):
    if col in page_df.columns:
        page_df[col] = pd.to_numeric(page_df[col], errors='coerce')

fmt = {}
if 'Total Value' in page_df.columns:
    fmt['Total Value'] = '${:,.2f}'
if 'Price' in page_df.columns:
    fmt['Price'] = '${:,.4f}'
if 'Quantity' in page_df.columns:
    fmt['Quantity'] = '{:,.4f}'

st.dataframe(page_df.style.format(fmt, na_rep='—'), use_container_width=True, hide_index=True)
st.caption(f'Page {page} of {total_pages}')
