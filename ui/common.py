"""
Shared UI helpers: auth gate, page chrome, and formatting.

Every page opens with :func:`page_header`, which handles the auth check and the
sidebar so a page body can start with its actual content.
"""

import os

import pandas as pd
import streamlit as st

STATUS_OPTIONS = ['Unreviewed', 'On Track', 'Watch', 'At Risk', 'Broken', 'Exited']

STATUS_EMOJI = {
    'On Track': '🟢', 'Watch': '🟡', 'At Risk': '🟠',
    'Broken': '🔴', 'Exited': '⚫', 'Unreviewed': '⬜',
}

STATUS_COLORS = {
    'On Track': 'green', 'Watch': 'orange', 'At Risk': 'orangered',
    'Broken': 'red', 'Exited': 'grey', 'Unreviewed': 'lightgrey',
}


def get_secret(key: str, default: str = '') -> str:
    """Read from Streamlit secrets when deployed, else the environment."""
    try:
        return st.secrets[key]
    except (KeyError, AttributeError, FileNotFoundError):
        return os.getenv(key, default)


def require_auth() -> None:
    """Stop the page unless the visitor has logged in (or demo mode is active)."""
    if st.session_state.get('_demo_mode'):
        return
    if not st.session_state.get('authenticated'):
        st.error('Please log in from the main page.')
        st.stop()


def is_guest() -> bool:
    """Guests see saved data and cannot refresh, edit or export."""
    return st.session_state.get('role') == 'guest'


def page_header(title: str, icon: str = '', caption: str = '') -> None:
    """Auth gate, sidebar status and page title — the opening line of every page."""
    require_auth()
    with st.sidebar:
        render_sidebar_status()
    st.title(f'{icon} {title}'.strip())
    if caption:
        st.caption(caption)


def render_sidebar_status() -> None:
    """One line saying where the numbers on screen came from."""
    if st.session_state.get('_demo_mode'):
        st.info('🎭 Demo mode — fictional data')
        return

    portfolio = st.session_state.get('portfolio') or {}
    fetched_at = portfolio.get('fetched_at', '')

    if st.session_state.get('etrade_connected'):
        st.success('🟢 Live — E\\*TRADE connected')
    elif st.session_state.get('_is_snapshot'):
        snapshot_date = portfolio.get('snapshot_date', fetched_at[:10])
        st.info(f'📅 Snapshot — {snapshot_date}')
    elif portfolio:
        st.warning(f'🟡 Cached — {fetched_at[:10] or "date unknown"}')
    else:
        st.error('🔴 No data — connect E\\*TRADE to load')


def require_portfolio(key: str | None = None):
    """
    Return the loaded portfolio, or stop the page with a useful message.

    Args:
        key: Optionally require one section (``'holdings'``, ``'transactions'``…)
            to be present and non-empty; that section is returned instead.
    """
    portfolio = st.session_state.get('portfolio')
    if not portfolio:
        st.warning('No data loaded yet. Open **Overview** and refresh from E\\*TRADE.')
        st.stop()
    if key is None:
        return portfolio

    section = portfolio.get(key)
    if section is None or (hasattr(section, 'empty') and section.empty):
        st.info(f'No {key.replace("_", " ")} available in the current data.')
        st.stop()
    return section


# ── Formatting ────────────────────────────────────────────────────────────────

def money(value, decimals: int = 2) -> str:
    """
    ``$1,234.56``, or an em dash when the value is missing.

    Negatives read ``-$1,234.56``, not ``$-1,234.56`` — the sign belongs in front
    of the whole quantity, and the naive f-string put it after the dollar sign.
    """
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return '—'
    try:
        number = float(value)
    except (TypeError, ValueError):
        return '—'
    sign = '-' if number < 0 else ''
    return f'{sign}${abs(number):,.{decimals}f}'


def signed_money(value, decimals: int = 2) -> str:
    """
    ``+$1,234.56`` / ``-$1,234.56`` for gains and losses.

    Use this as a Styler formatter instead of ``'${:+,.2f}'``: that format string
    renders a loss as ``$-1,234.56``, with the sign stranded after the currency
    symbol.
    """
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return '—'
    try:
        number = float(value)
    except (TypeError, ValueError):
        return '—'
    sign = '-' if number < 0 else '+'
    return f'{sign}${abs(number):,.{decimals}f}'


def percent(value, decimals: int = 2, signed: bool = False) -> str:
    """``12.34%``, or ``+12.34%`` when ``signed``. Em dash when missing."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return '—'
    try:
        return f'{float(value):{"+" if signed else ""}.{decimals}f}%'
    except (TypeError, ValueError):
        return '—'
