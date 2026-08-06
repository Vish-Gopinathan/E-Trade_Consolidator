"""
Earnings: the next report date for each holding, and how recent ones landed.

Data is cached in ``data/earnings_store.json``. Past quarters never change, so
they are fetched once; only upcoming dates are re-checked, weekly.

The Reported column is labelled honestly. yfinance's EPS history is indexed by
fiscal **quarter end**, not the announcement date, and the two can be a month
apart. Where the real announcement date is available the column shows it; where
it is not, the quarter end is shown and marked, and the one-day price reaction is
left blank rather than measured against the wrong day.
"""

from datetime import date

import pandas as pd
import streamlit as st

from portfolio.storage import earnings as earnings_store
from ui import theme
from ui.common import is_guest, page_header, require_portfolio

page_header('Earnings', '📅')

holdings = require_portfolio('holdings')
colours = theme.palette()
symbols = sorted(holdings[holdings['Symbol'] != 'CASH']['Symbol'].dropna().unique().tolist())
today = date.today()

# ── Load, fetching what is missing or stale ───────────────────────────────────

if 'earnings_store' not in st.session_state:
    st.session_state.earnings_store = earnings_store.load()
store = st.session_state.earnings_store

outdated = earnings_store.stale_symbols(symbols, store)
if outdated:
    progress = st.progress(0.0, text=f'Fetching earnings for {len(outdated)} symbol(s)…')
    store, _ = earnings_store.refresh(
        outdated, store,
        on_progress=lambda done, total, symbol: progress.progress(
            done / total, text=f'{symbol} — {done} of {total}'),
    )
    progress.empty()
    st.session_state.earnings_store = store

header_left, header_right = st.columns([4, 1])
# Symbols that returned nothing keep the '2000-01-01' sentinel so they are retried
# on the next load; including it here reported "Last refreshed Jan 01, 2000".
updates = [
    date.fromisoformat(store[s]['last_updated'])
    for s in symbols
    if s in store and store[s].get('last_updated', earnings_store.NEVER) != earnings_store.NEVER
]
with header_left:
    if updates:
        st.caption(
            f'Last refreshed {min(updates):%b %d, %Y} · upcoming dates re-checked weekly, '
            'past quarters kept permanently'
        )
with header_right:
    if not is_guest() and st.button('🔄 Refresh all', use_container_width=True):
        progress = st.progress(0.0, text='Refreshing…')
        store, _ = earnings_store.refresh(
            symbols, store, force=True,
            on_progress=lambda done, total, symbol: progress.progress(
                done / total, text=f'{symbol} — {done} of {total}'),
        )
        st.session_state.earnings_store = store
        st.rerun()

# ── Upcoming ──────────────────────────────────────────────────────────────────

st.subheader('Upcoming')

upcoming = []
for symbol in symbols:
    entry = (store.get(symbol) or {}).get('upcoming')
    if not entry:
        continue
    try:
        when = date.fromisoformat(entry['date'])
    except (KeyError, ValueError):
        continue
    days_away = (when - today).days
    if days_away < -3:   # estimates lag; drop anything clearly stale
        continue
    upcoming.append({
        'Symbol': symbol, 'Expected': when, 'Days away': days_away,
        'EPS estimate': entry.get('eps_estimate'),
    })

if upcoming:
    frame = pd.DataFrame(upcoming).sort_values('Days away')

    def _proximity(row):
        """Tint the row by how soon it is. The Days away column carries it too."""
        if row['Days away'] <= 7:
            tint = 'rgba(227,73,72,0.16)'
        elif row['Days away'] <= 30:
            tint = 'rgba(237,161,0,0.13)'
        else:
            tint = ''
        return [f'background-color: {tint}' if tint else ''] * len(row)

    st.dataframe(
        frame.style.apply(_proximity, axis=1).format({
            'Expected': '{:%b %d, %Y}', 'EPS estimate': '${:,.2f}', 'Days away': '{:,.0f}',
        }, na_rep='—'),
        use_container_width=True, hide_index=True,
    )
    st.caption('Company estimates — confirm with your broker before trading around a date.')
else:
    st.info('No upcoming earnings dates found for these holdings.')

st.markdown('---')

# ── History ───────────────────────────────────────────────────────────────────

st.subheader('Recent results')

rows = []
for symbol in symbols:
    for quarter in (store.get(symbol) or {}).get('recent', []):
        try:
            when = date.fromisoformat(quarter['date'])
        except (KeyError, ValueError):
            continue
        actual, estimate = quarter.get('eps_actual'), quarter.get('eps_estimate')
        surprise = quarter.get('surprise_pct')

        if surprise is not None:
            verdict = '✅ Beat' if surprise >= 0 else '❌ Miss'
        elif actual is not None and estimate is not None:
            verdict = '✅ Beat' if actual >= estimate else '❌ Miss'
        else:
            verdict = '—'

        rows.append({
            'Symbol': symbol,
            'Reported': when,
            'Exact date': bool(quarter.get('date_is_report_date')),
            'EPS': actual,
            'Estimate': estimate,
            'Surprise %': surprise,
            'Result': verdict,
            'Next-day move': quarter.get('price_chg_pct'),
        })

if rows:
    frame = pd.DataFrame(rows).sort_values('Reported', ascending=False)
    approximate = int((~frame['Exact date']).sum())
    frame = frame.drop(columns='Exact date')

    def _signed_ink(value):
        if value is None or pd.isna(value):
            return ''
        return f'color: {colours["positive"] if value >= 0 else colours["negative"]}'

    st.dataframe(
        frame.style
        .map(_signed_ink, subset=['Surprise %', 'Next-day move'])
        .format({
            'Reported': '{:%b %d, %Y}', 'EPS': '${:,.2f}', 'Estimate': '${:,.2f}',
            'Surprise %': '{:+.1f}%', 'Next-day move': '{:+.2f}%',
        }, na_rep='—'),
        use_container_width=True, hide_index=True,
    )
    if approximate:
        st.caption(
            f'{approximate} row(s) show the fiscal quarter end rather than the '
            'announcement date, which is all the data source offered for them. '
            'Next-day move is left blank there rather than measured against the wrong day.'
        )
else:
    st.info('No earnings history yet. Use **Refresh all** if this persists.')

# ── Coverage ──────────────────────────────────────────────────────────────────

no_data = [
    s for s in symbols
    if not (store.get(s) or {}).get('recent') and not (store.get(s) or {}).get('upcoming')
]
if no_data:
    st.caption(
        f'No earnings data for {", ".join(no_data)}. Expected for ETFs, trusts and '
        'funds, which do not report earnings; for an operating company it usually '
        'means the ticker has changed or the data source has no coverage.'
    )

errors = {s: store[s]['_error'] for s in symbols if (store.get(s) or {}).get('_error')}
if errors:
    with st.expander(f'⚠️ {len(errors)} symbol(s) had fetch errors'):
        for symbol, message in errors.items():
            st.code(f'{symbol}: {message}')
