from datetime import datetime, date
import pandas as pd
import streamlit as st
import yfinance as yf


@st.cache_data(ttl=3600)
def get_earnings_dates(symbols: tuple) -> dict:
    """
    Return {symbol: next_earnings_date_or_None} for all symbols.
    Tries ticker.calendar first (most reliable for upcoming dates),
    falls back to ticker.earnings_dates.
    Results are cached for 1 hour.
    """
    results = {}
    for symbol in symbols:
        results[symbol] = _next_earnings(symbol)
    return results


@st.cache_data(ttl=3600)
def get_news(symbol: str, limit: int = 10) -> list:
    """
    Return up to `limit` recent news articles for `symbol`.
    Each article: {title, link, publisher, published_at}.
    Results are cached for 1 hour.
    """
    try:
        raw = yf.Ticker(symbol).news or []
        articles = []
        for item in raw[:limit]:
            ts = item.get('providerPublishTime') or item.get('publishTime')
            articles.append({
                'title': item.get('title', ''),
                'link': item.get('link', ''),
                'publisher': item.get('publisher', ''),
                'published_at': datetime.fromtimestamp(ts) if ts else None,
            })
        return articles
    except Exception:
        return []


def _next_earnings(symbol: str):
    ticker = yf.Ticker(symbol)

    # Try calendar property (has upcoming quarter's date)
    try:
        cal = ticker.calendar
        if cal is not None and not cal.empty:
            for key in ('Earnings Date', 'earnings_date'):
                if key in cal.index:
                    val = cal.loc[key]
                    dt = val.iloc[0] if hasattr(val, 'iloc') else val
                    if pd.notna(dt):
                        return pd.Timestamp(dt).date()
    except Exception:
        pass

    # Fall back to earnings_dates (sorted descending, may include historical)
    try:
        dates_df = ticker.earnings_dates
        if dates_df is not None and not dates_df.empty:
            future = dates_df[dates_df.index > pd.Timestamp.now(tz='UTC')]
            if not future.empty:
                return future.index[-1].date()
    except Exception:
        pass

    return None
