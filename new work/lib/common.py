import os
import streamlit as st


def get_secret(key: str, default: str = '') -> str:
    """Read from st.secrets first (Streamlit Cloud), fall back to env var (local dev)."""
    try:
        return st.secrets[key]
    except (KeyError, AttributeError, FileNotFoundError):
        return os.getenv(key, default)


STATUS_COLORS = {
    'On Track': 'green',
    'Watch': 'orange',
    'At Risk': 'orangered',
    'Broken': 'red',
    'Exited': 'grey',
    'Unreviewed': 'lightgrey',
}

STATUS_OPTIONS = ['Unreviewed', 'On Track', 'Watch', 'At Risk', 'Broken', 'Exited']

STATUS_EMOJI = {
    'On Track': '🟢',
    'Watch': '🟡',
    'At Risk': '🟠',
    'Broken': '🔴',
    'Exited': '⚫',
    'Unreviewed': '⬜',
}


def require_auth():
    if st.session_state.get('_demo_mode'):
        return
    if not st.session_state.get('authenticated'):
        st.error('Please log in from the main page.')
        st.stop()


def is_guest() -> bool:
    return st.session_state.get('role') == 'guest'


def render_sidebar_status():
    st.sidebar.markdown('---')
    if st.session_state.get('_demo_mode'):
        st.sidebar.info('🎭 Demo mode — fictional data')
    elif st.session_state.get('etrade_connected'):
        st.sidebar.success('🟢 Live — E-Trade connected')
    elif st.session_state.get('_is_snapshot'):
        portfolio = st.session_state.get('portfolio') or {}
        snap_date = portfolio.get('snapshot_date', portfolio.get('fetched_at', '')[:10])
        st.sidebar.info(f'📅 Month-end snapshot — {snap_date}')
    elif st.session_state.get('portfolio'):
        fetched_at = st.session_state.portfolio.get('fetched_at', '')
        st.sidebar.warning(f'🟡 Cached — last updated {fetched_at[:10] if fetched_at else "unknown"}')
    else:
        st.sidebar.error('🔴 No data — connect E-Trade or load cache')
