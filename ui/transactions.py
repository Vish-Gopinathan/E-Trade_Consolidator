"""
Transactions: the full history, filterable, with how each row was classified.

Internal transfers are hidden by default — they are real records but they net to
zero and would otherwise bury the trades. The Category filter surfaces them.
"""

import datetime

import pandas as pd
import streamlit as st

from portfolio import classify
from ui.common import page_header, require_portfolio

page_header('Transaction History', '🔄')

transactions = require_portfolio('transactions')

frame = transactions.copy()
if 'Date' in frame.columns:
    frame['Date'] = pd.to_datetime(frame['Date'], errors='coerce')

# ── Filters ───────────────────────────────────────────────────────────────────

with st.expander('Filters', expanded=True):
    f1, f2, f3 = st.columns([2, 2, 3])

    with f1:
        if frame['Date'].notna().any():
            date_range = st.date_input(
                'Date range',
                value=(frame['Date'].min().date(), frame['Date'].max().date()),
                min_value=datetime.date(1990, 1, 1), max_value=datetime.date.today(),
            )
        else:
            date_range = None

    with f2:
        available = sorted(frame['Category'].dropna().unique().tolist()) \
            if 'Category' in frame.columns else []
        default = [c for c in available if c != classify.INTERNAL]
        selected = st.multiselect('Category', options=available, default=default)

    with f3:
        search = st.text_input('Search description or symbol', '')

filtered = frame
if date_range and len(date_range) == 2:
    start, end = pd.Timestamp(date_range[0]), pd.Timestamp(date_range[1]) + pd.Timedelta(days=1)
    filtered = filtered[(filtered['Date'] >= start) & (filtered['Date'] < end)]

if selected and 'Category' in filtered.columns:
    filtered = filtered[filtered['Category'].isin(selected)]

if search:
    haystack = filtered['Security Name'].fillna('')
    if 'Symbol' in filtered.columns:
        haystack = haystack + ' ' + filtered['Symbol'].fillna('')
    filtered = filtered[haystack.str.contains(search, case=False, na=False)]

# ── Table ─────────────────────────────────────────────────────────────────────

st.markdown(f'**{len(filtered):,}** of {len(frame):,} transactions')

columns = [c for c in [
    'Date', 'Account', 'Symbol', 'Security Name', 'Transaction Type', 'Category',
    'Quantity', 'Price', 'Total Value',
] if c in filtered.columns]

page_size = st.select_slider('Rows per page', options=[25, 50, 100, 250], value=50)
pages = max(1, (len(filtered) + page_size - 1) // page_size)
page = st.number_input('Page', min_value=1, max_value=pages, value=1, step=1)

view = filtered[columns].iloc[(page - 1) * page_size: page * page_size].copy()
for column in ('Total Value', 'Price', 'Quantity'):
    if column in view.columns:
        view[column] = pd.to_numeric(view[column], errors='coerce')

st.dataframe(
    view.style.format({
        'Date': '{:%b %d, %Y}', 'Total Value': '${:,.2f}',
        'Price': '${:,.4f}', 'Quantity': '{:,.4g}',
    }, na_rep='—'),
    use_container_width=True, hide_index=True,
)
st.caption(f'Page {page} of {pages}')

# ── Classification detail ─────────────────────────────────────────────────────

if 'Classification Note' in filtered.columns:
    explained = filtered[filtered['Classification Note'].fillna('') != '']
    if not explained.empty:
        with st.expander(f'How {len(explained)} transfer(s) were classified'):
            st.caption(
                'Transfers cannot be classified from a single row — the app pairs legs '
                'that share a reference number, then checks the counterparty account. '
                'Each decision and its reason is below.'
            )
            note_columns = [c for c in ['Date', 'Security Name', 'Total Value',
                                        'Category', 'Classification Note']
                            if c in explained.columns]
            st.dataframe(
                explained[note_columns].sort_values('Date', ascending=False)
                .rename(columns={'Security Name': 'Description',
                                 'Classification Note': 'Reason'})
                .style.format({'Total Value': '${:,.2f}', 'Date': '{:%b %d, %Y}'}, na_rep='—'),
                use_container_width=True, hide_index=True,
            )

unclassified = filtered[filtered['Category'] == classify.OTHER] \
    if 'Category' in filtered.columns else pd.DataFrame()
if not unclassified.empty:
    st.caption(
        f'{len(unclassified)} row(s) could not be classified and are excluded from all '
        'totals. Filter to **Other** above to see them.'
    )
