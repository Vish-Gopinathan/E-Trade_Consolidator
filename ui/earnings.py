from datetime import date

import pandas as pd
import streamlit as st
from ui.common import require_auth, render_sidebar_status, is_guest
from portfolio.storage import earnings as es

st.set_page_config(page_title='Earnings', page_icon='📅', layout='wide')
require_auth()

with st.sidebar:
    st.title('📈 Portfolio')
    render_sidebar_status()

st.title('📅 Earnings Calendar')

portfolio = st.session_state.get('portfolio')
if not portfolio:
    st.warning('No data loaded. Go to the home page and refresh.')
    st.stop()

holdings_df = portfolio.get('holdings')
if holdings_df is None or (hasattr(holdings_df, 'empty') and holdings_df.empty):
    st.info('No holdings data available.')
    st.stop()

all_symbols = sorted(
    holdings_df[holdings_df['Symbol'] != 'CASH']['Symbol'].unique().tolist()
)

# ── Load store ────────────────────────────────────────────────────────────────

if 'earnings_store' not in st.session_state:
    with st.spinner('Loading earnings data…'):
        store = es.load()
    st.session_state.earnings_store = store
else:
    store = st.session_state.earnings_store

# Auto-fetch symbols that are missing or stale (first visit / new holdings)
today = date.today()
need_fetch = [
    s for s in all_symbols
    if s not in store
    or (today - date.fromisoformat(store[s].get('last_updated', '2000-01-01'))).days >= 7
]

if need_fetch:
    with st.spinner(
        f'Fetching earnings for {len(need_fetch)} symbol(s): {", ".join(need_fetch)} '
        f'— this takes ~{len(need_fetch) * 2}s on first run…'
    ):
        store, _ = es.refresh(need_fetch, store, force=True)
        es.save(store)
        st.session_state.earnings_store = store

# ── Header controls ───────────────────────────────────────────────────────────

hdr_left, hdr_right = st.columns([4, 1])

last_updates = [
    date.fromisoformat(store[s]['last_updated'])
    for s in all_symbols if s in store
]
if last_updates:
    with hdr_left:
        st.caption(
            f'Data last refreshed: **{min(last_updates).strftime("%b %d, %Y")}** '
            '· refreshed automatically when > 7 days old'
        )

if not is_guest():
    with hdr_right:
        if st.button('🔄 Refresh All'):
            with st.spinner(f'Refreshing {len(all_symbols)} symbols…'):
                store, _ = es.refresh(all_symbols, store, force=True)
                es.save(store)
                st.session_state.earnings_store = store
            st.rerun()

# ── Upcoming Earnings ─────────────────────────────────────────────────────────

st.subheader('Upcoming Earnings')
st.caption('Dates are company estimates — verify with your broker before trading.')

upcoming_rows = []
for sym in all_symbols:
    data = store.get(sym) or {}
    u = data.get('upcoming')
    if not u:
        continue
    try:
        dt = date.fromisoformat(u['date'])
    except Exception:
        continue
    days = (dt - today).days
    if days < -3:   # skip if more than 3 days in the past (estimate lag)
        continue
    upcoming_rows.append({
        'Symbol': sym,
        'Date': dt,
        'Days Away': days,
        'EPS Estimate': u.get('eps_estimate'),
    })

if upcoming_rows:
    up_df = pd.DataFrame(upcoming_rows).sort_values('Days Away')

    def _up_row_style(row):
        d = row['Days Away']
        if d <= 7:
            return ['background-color: rgba(220,50,50,0.15)'] * len(row)
        if d <= 30:
            return ['background-color: rgba(255,200,0,0.12)'] * len(row)
        return [''] * len(row)

    fmt_up = {
        'Date': lambda d: d.strftime('%b %d, %Y'),
        'EPS Estimate': lambda v: f'${v:.2f}' if v is not None and pd.notna(v) else '—',
    }
    st.dataframe(
        up_df.style.apply(_up_row_style, axis=1).format(fmt_up, na_rep='—'),
        use_container_width=True,
        hide_index=True,
    )
else:
    st.info('No upcoming earnings found for current holdings.')

# ── Recent Earnings History ────────────────────────────────────────────────────

st.markdown('---')
st.subheader('Recent Earnings History')

recent_rows = []
for sym in all_symbols:
    data = store.get(sym) or {}
    for r in data.get('recent', []):
        try:
            dt = date.fromisoformat(r['date'])
        except Exception:
            continue
        eps_act = r.get('eps_actual')
        eps_est = r.get('eps_estimate')
        surprise = r.get('surprise_pct')
        price_chg = r.get('price_chg_pct')

        if surprise is not None:
            beat = '✅ Beat' if surprise >= 0 else '❌ Miss'
        elif eps_act is not None and eps_est is not None:
            beat = '✅ Beat' if eps_act >= eps_est else '❌ Miss'
        else:
            beat = '—'

        recent_rows.append({
            'Symbol': sym,
            'Date': dt,
            'Actual EPS': eps_act,
            'Est EPS': eps_est,
            'Surprise %': surprise,
            'Beat/Miss': beat,
            'Stock Reaction': price_chg,
        })

if recent_rows:
    rec_df = pd.DataFrame(recent_rows).sort_values('Date', ascending=False)

    def _color_signed(val):
        if val is None or pd.isna(val):
            return ''
        return 'color: #27ae60' if val >= 0 else 'color: #c0392b'

    fmt_rec = {
        'Date': lambda d: d.strftime('%b %d, %Y'),
        'Actual EPS': lambda v: f'${v:.2f}' if v is not None and pd.notna(v) else '—',
        'Est EPS': lambda v: f'${v:.2f}' if v is not None and pd.notna(v) else '—',
        'Surprise %': lambda v: f'{v:+.1f}%' if v is not None and pd.notna(v) else '—',
        'Stock Reaction': lambda v: f'{v:+.2f}%' if v is not None and pd.notna(v) else '—',
    }
    styled = (
        rec_df.style
        .format(fmt_rec, na_rep='—')
        .map(_color_signed, subset=['Surprise %'])
        .map(_color_signed, subset=['Stock Reaction'])
    )
    st.dataframe(styled, use_container_width=True, hide_index=True)
else:
    st.info('No recent earnings history in store yet.')

# ── Coverage summary ──────────────────────────────────────────────────────────

no_data = [
    s for s in all_symbols
    if not (store.get(s) or {}).get('recent')
    and not (store.get(s) or {}).get('upcoming')
]
if no_data:
    st.caption(
        f'No earnings data found for: {", ".join(no_data)} '
        '— may be ETFs, funds, or recently listed.'
    )

# ── Debug expander ────────────────────────────────────────────────────────────

with st.expander('🔍 Debug — click to inspect store and errors'):
    errors = {s: store[s].get('_error') for s in all_symbols if store.get(s) and store[s].get('_error')}
    if errors:
        st.markdown('**Fetch errors:**')
        for sym, err in errors.items():
            st.code(f'{sym}: {err}')
    else:
        st.caption('No errors recorded.')

    st.markdown('**Store snapshot (first 3 symbols):**')
    for sym in all_symbols[:3]:
        entry = store.get(sym, 'NOT IN STORE')
        st.write(f'**{sym}**:', entry)
