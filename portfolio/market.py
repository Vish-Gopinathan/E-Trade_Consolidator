"""
Market data helpers used by other pages.

Earnings data is handled by portfolio/storage/earnings.py.
"""

import streamlit as st
import yfinance as yf
import pandas as pd

from portfolio import realized


@st.cache_data(ttl=300)
def get_current_prices(symbols: tuple) -> dict:
    """Return {symbol: current_price_or_None}. Cached 5 minutes."""
    results = {}
    for symbol in symbols:
        try:
            ticker = yf.Ticker(symbol)
            price = None
            try:
                fi = ticker.fast_info
                price = getattr(fi, 'last_price', None)
            except Exception:
                pass
            if not price:
                hist = ticker.history(period='5d')
                if not hist.empty:
                    price = float(hist['Close'].iloc[-1])
            results[symbol] = float(price) if price else None
        except Exception:
            results[symbol] = None
    return results


@st.cache_data(ttl=86400, show_spinner=False)
def get_split_history(symbols: tuple) -> dict:
    """
    Return {symbol: pd.Series} with split dates and ratios.
    Cached 24 hours.
    """
    result = {}
    for sym in symbols:
        try:
            result[sym] = yf.Ticker(sym).splits
        except Exception:
            result[sym] = pd.Series(dtype=float)
    return result


#: Re-exported from :mod:`portfolio.realized`, which owns the implementation.
#: The logic layer must not import streamlit, so the pure split arithmetic lives
#: there and this module — which is cached by Streamlit — borrows it, rather
#: than the two drifting apart with a copy each.
cumulative_split_factor = realized.cumulative_split_factor
