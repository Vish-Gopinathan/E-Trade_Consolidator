from datetime import datetime, date
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
import requests
import streamlit as st
import yfinance as yf

_EQUITY_TYPES = {'equity', 'stock', 'reit'}
_FMP_BASE = 'https://financialmodelingprep.com/api/v3'


# ── Quote-type / ETF detection ────────────────────────────────────────────────

def _quote_type(ticker) -> str:
    try:
        qt = getattr(ticker.fast_info, 'quote_type', None)
        if qt:
            return qt.lower()
    except Exception:
        pass
    try:
        return ticker.info.get('quoteType', 'equity').lower()
    except Exception:
        return 'equity'


# ── Price reaction helper ─────────────────────────────────────────────────────

def _price_reaction(ticker, earnings_date: date):
    """Return % change from close before earnings to close the next trading day."""
    try:
        start = pd.Timestamp(earnings_date) - pd.Timedelta(days=7)
        end = pd.Timestamp(earnings_date) + pd.Timedelta(days=7)
        hist = ticker.history(start=start, end=end)
        if hist.empty:
            return None
        if hist.index.tz is not None:
            hist.index = hist.index.tz_localize(None)
        ts = pd.Timestamp(earnings_date)
        before = hist[hist.index <= ts]
        after = hist[hist.index > ts]
        if before.empty or after.empty:
            return None
        p0 = float(before['Close'].iloc[-1])
        p1 = float(after['Close'].iloc[0])
        return round((p1 - p0) / p0 * 100, 2) if p0 else None
    except Exception:
        return None


# ── FMP path ──────────────────────────────────────────────────────────────────

def _fmp_fetch_one(symbol: str, api_key: str, ticker) -> dict:
    out = {'is_equity': True, 'name': symbol, 'upcoming': None, 'recent': []}
    qt = _quote_type(ticker)
    out['is_equity'] = qt in _EQUITY_TYPES
    if not out['is_equity']:
        return out

    try:
        resp = requests.get(
            f'{_FMP_BASE}/historical/earning_calendar/{symbol}',
            params={'apikey': api_key},
            timeout=8,
        )
        rows = resp.json() if resp.status_code == 200 else []
    except Exception:
        rows = []

    today = date.today()
    for row in rows:
        try:
            dt = date.fromisoformat(row['date'])
        except Exception:
            continue
        eps_act = row.get('eps')
        eps_est = row.get('epsEstimated')
        rev_act = row.get('revenue')
        rev_est = row.get('revenueEstimated')

        if dt > today:
            if out['upcoming'] is None:
                out['upcoming'] = {
                    'date': dt.isoformat(),
                    'eps_estimate': float(eps_est) if eps_est is not None else None,
                    'revenue_estimate': float(rev_est) if rev_est is not None else None,
                    'time': row.get('time', ''),
                }
        elif eps_act is not None and len(out['recent']) < 4:
            surprise = None
            if eps_est and eps_est != 0:
                surprise = round((eps_act - eps_est) / abs(eps_est) * 100, 2)
            out['recent'].append({
                'date': dt.isoformat(),
                'eps_actual': float(eps_act),
                'eps_estimate': float(eps_est) if eps_est is not None else None,
                'surprise_pct': surprise,
                'revenue_actual': float(rev_act) if rev_act else None,
                'revenue_estimate': float(rev_est) if rev_est else None,
                'price_chg_pct': _price_reaction(ticker, dt),
            })

    return out


# ── yfinance path ─────────────────────────────────────────────────────────────

def _yf_calendar_upcoming(ticker) -> 'date | None':
    try:
        cal = ticker.calendar
        if cal is None or (hasattr(cal, 'empty') and cal.empty):
            return None
        now = pd.Timestamp.now()
        for key in ('Earnings Date', 'earnings_date'):
            if hasattr(cal, 'index') and key in cal.index:
                val = cal.loc[key]
                dt = val.iloc[0] if hasattr(val, 'iloc') else val
                if pd.notna(dt):
                    ts = pd.Timestamp(dt)
                    if ts > now:
                        return ts.date()
    except Exception:
        pass
    return None


def _yf_fetch_one(symbol: str, ticker) -> dict:
    out = {'is_equity': True, 'name': symbol, 'upcoming': None, 'recent': []}
    qt = _quote_type(ticker)
    out['is_equity'] = qt in _EQUITY_TYPES
    if not out['is_equity']:
        return out

    now_utc = pd.Timestamp.now(tz='UTC')
    dates_df = None
    try:
        dates_df = ticker.get_earnings_dates(limit=16)
    except Exception:
        pass

    if dates_df is not None and not dates_df.empty:
        future = dates_df[dates_df.index > now_utc]
        if not future.empty:
            row = future.iloc[-1]  # df sorted desc; last = nearest upcoming
            eps_est = row.get('EPS Estimate')
            out['upcoming'] = {
                'date': row.name.date().isoformat(),
                'eps_estimate': float(eps_est) if pd.notna(eps_est) else None,
                'revenue_estimate': None,
                'time': '',
            }

        past = dates_df[dates_df.index <= now_utc]
        for dt_idx, row in past.iterrows():
            if len(out['recent']) >= 4:
                break
            eps_act = row.get('Reported EPS')
            eps_est = row.get('EPS Estimate')
            surprise = row.get('Surprise(%)')
            if pd.isna(eps_act) and pd.isna(eps_est):
                continue
            earnings_date = dt_idx.date()
            out['recent'].append({
                'date': earnings_date.isoformat(),
                'eps_actual': float(eps_act) if pd.notna(eps_act) else None,
                'eps_estimate': float(eps_est) if pd.notna(eps_est) else None,
                'surprise_pct': float(surprise) if pd.notna(surprise) else None,
                'revenue_actual': None,
                'revenue_estimate': None,
                'price_chg_pct': _price_reaction(ticker, earnings_date),
            })

    if out['upcoming'] is None:
        fallback = _yf_calendar_upcoming(ticker)
        if fallback:
            out['upcoming'] = {
                'date': fallback.isoformat(),
                'eps_estimate': None,
                'revenue_estimate': None,
                'time': '',
            }

    return out


# ── Dispatcher ────────────────────────────────────────────────────────────────

def _fetch_one(symbol: str, fmp_key: str) -> dict:
    ticker = yf.Ticker(symbol)
    if fmp_key:
        return _fmp_fetch_one(symbol, fmp_key, ticker)
    return _yf_fetch_one(symbol, ticker)


# ── Public API ────────────────────────────────────────────────────────────────

@st.cache_data(ttl=14400)
def get_full_earnings_data(symbols: tuple, fmp_key: str = '') -> dict:
    """
    Return {symbol: data} for all equity symbols.

    data = {
      'is_equity': bool,
      'name': str,
      'upcoming': {'date': str, 'eps_estimate': float|None,
                   'revenue_estimate': float|None, 'time': str} | None,
      'recent': [{'date': str, 'eps_actual': float|None,
                  'eps_estimate': float|None, 'surprise_pct': float|None,
                  'revenue_actual': float|None, 'revenue_estimate': float|None,
                  'price_chg_pct': float|None}]  # up to 4 quarters
    }

    Uses FMP API when fmp_key is provided (better coverage + revenue data),
    otherwise falls back to yfinance get_earnings_dates(). Results cached 4 hours.
    Fetches all symbols in parallel.
    """
    results = {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(_fetch_one, sym, fmp_key): sym for sym in symbols}
        for fut in as_completed(futures):
            sym = futures[fut]
            try:
                results[sym] = fut.result(timeout=25)
            except Exception:
                results[sym] = {
                    'is_equity': True, 'name': sym,
                    'upcoming': None, 'recent': [],
                }
    return results


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


@st.cache_data(ttl=3600)
def get_news(symbol: str, limit: int = 10) -> list:
    """Return up to `limit` recent news articles for `symbol`. Cached 1 hour."""
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
