"""
Demo entry point — the dashboard driven by fictional holdings.

    streamlit run demo.py

Runs the same pages as ``app.py`` against generated data, with live prices so the
charts move. Useful for showing the app without exposing a real account.

Thesis notes are written to a **separate** file in demo mode. The demo used to
call ``save_all(DEMO_THESIS)`` against the same path the real app uses, so
launching the demo locally destroyed hand-written notes about real positions.
"""

import streamlit as st

st.set_page_config(
    page_title='Portfolio Dashboard (Demo)',
    page_icon='🎭',
    layout='wide',
    initial_sidebar_state='expanded',
)

# Must be set before any page reads session_state: `_demo_mode` bypasses the login
# gate and redirects the thesis store to its demo file.
st.session_state.authenticated = True
st.session_state.role = 'admin'
st.session_state._demo_mode = True

if not st.session_state.get('_demo_data_loaded'):
    with st.spinner('Building demo portfolio with live prices…'):
        from portfolio.storage import thesis as thesis_store
        from ui.demo_data import DEMO_THESIS, build_demo_portfolio

        st.session_state.portfolio = build_demo_portfolio()
        st.session_state._demo_data_loaded = True
        thesis_store.save_all(DEMO_THESIS, demo=True)

with st.sidebar:
    st.title('📈 Portfolio (Demo)')
    st.warning(
        '🎭 **Demo mode**\n\nHoldings and transactions are fictional. '
        'Prices, earnings and splits are live market data.',
        icon='⚠️',
    )
    portfolio = st.session_state.get('portfolio')
    if portfolio:
        st.caption(f'Prices as of {portfolio.get("fetched_at", "")[:19].replace("T", " ")}')

    if st.button('🔄 Refresh live prices', use_container_width=True):
        from ui.demo_data import _fetch_live_prices, build_demo_portfolio

        _fetch_live_prices.clear()
        with st.spinner('Refreshing prices…'):
            st.session_state.portfolio = build_demo_portfolio()
        st.rerun()

navigation = st.navigation({
    'Portfolio': [
        st.Page('ui/overview.py', title='Overview', icon='📈', default=True),
        st.Page('ui/holdings.py', title='Holdings', icon='📊'),
        st.Page('ui/history.py', title='Value Over Time', icon='📉'),
        st.Page('ui/performance.py', title='Performance', icon='🎯'),
    ],
    'Money': [
        st.Page('ui/cash_flows.py', title='Cash Flows & Income', icon='💵'),
        st.Page('ui/transactions.py', title='Transactions', icon='🔄'),
    ],
    'Research': [
        st.Page('ui/earnings.py', title='Earnings', icon='📅'),
        st.Page('ui/thesis.py', title='Thesis Tracker', icon='🧠'),
        st.Page('ui/what_if_hold.py', title='What-If: Hold', icon='🔮'),
    ],
})
navigation.run()
