"""
Portfolio Dashboard — DEMO MODE
Fictional data modelled on a well-known value investor's public holdings.
News and earnings sections use live market data.
Deploy separately on Streamlit Cloud pointing this file as the main script.
"""

import datetime
import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).parent))

st.set_page_config(
    page_title='Portfolio Dashboard (Demo)',
    page_icon='🎭',
    layout='wide',
    initial_sidebar_state='expanded',
)

# ── Demo mode flags (must be set before pages read session_state) ─────────────
st.session_state.authenticated = True
st.session_state._demo_mode = True

# ── Build portfolio once per session ─────────────────────────────────────────
if not st.session_state.get('_demo_data_loaded'):
    with st.spinner('Building demo portfolio with live prices…'):
        from demo_data import build_demo_portfolio, DEMO_THESIS
        portfolio = build_demo_portfolio()
        st.session_state.portfolio = portfolio
        st.session_state._demo_data_loaded = True

        # Write demo thesis data so the Thesis Tracker page renders with content
        try:
            from lib import thesis_store
            thesis_store.save_all(DEMO_THESIS)
        except Exception:
            pass

# ── Sidebar ───────────────────────────────────────────────────────────────────

with st.sidebar:
    st.title('📈 Portfolio (Demo)')
    st.warning(
        '🎭 **Demo Mode**\n\n'
        'Holdings and transactions are fictional, modelled on a well-known '
        'value investor\'s public disclosures. News and earnings are live.',
        icon='⚠️',
    )

    portfolio = st.session_state.get('portfolio')
    if portfolio:
        fetched = portfolio.get('fetched_at', '')[:19].replace('T', ' ')
        st.caption(f'Prices as of: {fetched}')

    if st.button('🔄 Refresh Live Prices'):
        # Clear the price cache and rebuild
        from demo_data import _fetch_live_prices, build_demo_portfolio, DEMO_THESIS
        _fetch_live_prices.clear()
        with st.spinner('Refreshing prices…'):
            portfolio = build_demo_portfolio()
            st.session_state.portfolio = portfolio
            try:
                from lib import thesis_store
                thesis_store.save_all(DEMO_THESIS)
            except Exception:
                pass
        st.rerun()

# ── Home page ─────────────────────────────────────────────────────────────────

st.title('📈 Portfolio Dashboard')
st.info(
    '**Demo Mode** — All holdings, transactions, income, and analytics shown here are '
    'fictional data modelled on Berkshire Hathaway\'s publicly disclosed equity portfolio, '
    'scaled to a personal account. News and earnings calendar use live market data.',
    icon='🎭',
)

portfolio = st.session_state.get('portfolio')
if not portfolio:
    st.warning('Loading portfolio data…')
    st.stop()

summary = portfolio.get('summary') or {}
analytics_report = portfolio.get('analytics_report') or {}
perf = analytics_report.get('Performance Metrics') or {}

fetched_at = portfolio.get('fetched_at', '')
if fetched_at:
    st.caption(f'Live prices as of {fetched_at[:19].replace("T", " ")}')

# KPI row
total_value = summary.get('Total Portfolio Value', 0)
total_gain = summary.get('Total Unrealized Gain', 0)
gain_pct = summary.get('Total Unrealized Gain %', 0)
cash_pct = summary.get('Cash Percentage', 0)
dietz = perf.get('Modified Dietz Return (%)')

col1, col2, col3, col4, col5 = st.columns(5)
col1.metric('Portfolio Value', f'${total_value:,.2f}')
col2.metric('Unrealized Gain', f'${total_gain:,.2f}', f'{gain_pct:.2f}%')
col3.metric('Cash', f'{cash_pct:.1f}%')
col4.metric('Holdings', summary.get('Total Stocks', '—'))
if dietz is not None:
    col5.metric('Deposit-Adj. Return', f'{dietz:.2f}%')

st.markdown('---')

# Top holdings
holdings_df = portfolio.get('holdings')
if holdings_df is not None and not holdings_df.empty:
    st.subheader('Top Holdings')
    display_cols = [c for c in [
        'Symbol', 'Symbol Description', 'Market Value', 'Total Gain', 'Total Gain %', 'Percent of Portfolio'
    ] if c in holdings_df.columns]
    display = holdings_df[holdings_df['Symbol'] != 'CASH'][display_cols].sort_values(
        'Market Value', ascending=False
    ).head(10)
    st.dataframe(display, use_container_width=True, hide_index=True)

# Analytics snapshot
if analytics_report:
    st.markdown('---')
    st.subheader('Analytics Snapshot')
    cols = st.columns(3)
    conc = analytics_report.get('Concentration Analysis') or {}
    risk = analytics_report.get('Risk Metrics') or {}

    with cols[0]:
        st.markdown('**Concentration**')
        hhi = conc.get('HHI Score')
        if hhi:
            st.metric('HHI Score', f'{hhi:.1f}')
        top5 = conc.get('Top 5 Concentration (%)')
        if top5:
            st.metric('Top 5 Weight', f'{top5:.1f}%')

    with cols[1]:
        st.markdown('**Risk / Performance**')
        sharpe = risk.get('Sharpe Ratio')
        if sharpe is not None:
            st.metric('Sharpe Ratio', f'{sharpe:.2f}')
        win_rate = risk.get('Win Rate (%)')
        if win_rate is not None:
            st.metric('Win Rate', f'{win_rate:.1f}%')

    with cols[2]:
        st.markdown('**Income**')
        inc_summary = analytics_report.get('Income Summary') or {}
        total_income = inc_summary.get('Total Income')
        if total_income:
            st.metric('Total Dividend Income', f'${total_income:,.2f}')
        n_inc = inc_summary.get('Number of Income Transactions')
        if n_inc:
            st.metric('Income Transactions', n_inc)

st.markdown('---')
st.caption(
    'Navigate using the sidebar pages to explore Holdings, Analytics, Transactions, '
    'Cash Flows, News & Earnings, Investment Theses, and What-If Hold Analysis.'
)
