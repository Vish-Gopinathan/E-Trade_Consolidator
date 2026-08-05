import streamlit as st

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
    if not st.session_state.get('authenticated'):
        st.error('Please log in from the main page.')
        st.stop()


def render_sidebar_status():
    st.sidebar.markdown('---')
    if st.session_state.get('etrade_connected'):
        st.sidebar.success('🟢 Live — E-Trade connected')
    elif st.session_state.get('portfolio'):
        fetched_at = st.session_state.portfolio.get('fetched_at', '')
        st.sidebar.warning(f'🟡 Cached — last updated {fetched_at[:10] if fetched_at else "unknown"}')
    else:
        st.sidebar.error('🔴 No data — connect E-Trade or load cache')
