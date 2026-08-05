"""
Market data helpers used by other pages.

Earnings data is now handled by lib/earnings_store.py.
"""

from datetime import datetime
import streamlit as st
import yfinance as yf
import pandas as pd


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


def cumulative_split_factor(splits_series, since_date) -> float:
    """
    Product of all split ratios that occurred AFTER since_date.
    Returns 1.0 when there are no splits.
    """
    if splits_series is None or (hasattr(splits_series, 'empty') and splits_series.empty):
        return 1.0
    try:
        since_ts = pd.Timestamp(since_date)
        idx = splits_series.index
        if idx.tz is not None:
            idx = idx.tz_localize(None)
        after = splits_series[idx > since_ts]
        return float(after.prod()) if not after.empty else 1.0
    except Exception:
        return 1.0
