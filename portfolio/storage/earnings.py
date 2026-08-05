"""
Persistent earnings store.

Stores past quarters (static — never change) and the upcoming date (refreshed
weekly). Data source: yfinance. Persistence is local disk only: this file holds
real holdings-derived data and the repo is public, so it is never pushed to a
remote. On an ephemeral host the store simply rebuilds on first page load.
"""

import json
import time
from datetime import date

import pandas as pd
import yfinance as yf

from portfolio import paths

STORE_PATH = paths.DATA_DIR / 'earnings_store.json'
_STALENESS_DAYS = 7  # refresh upcoming dates weekly


# ── Disk I/O ──────────────────────────────────────────────────────────────────

def load() -> dict:
    if STORE_PATH.exists():
        try:
            return json.loads(STORE_PATH.read_text())
        except Exception:
            return {}
    return {}


def _write_disk(store: dict):
    STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STORE_PATH.write_text(json.dumps(store, indent=2))


def save(store: dict):
    """Write the store to disk. Local only — never to a remote."""
    _write_disk(store)


# ── Price reaction helper ─────────────────────────────────────────────────────

def _price_reaction(ticker, earnings_date: date) -> float | None:
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


# ── Per-symbol fetch ──────────────────────────────────────────────────────────

def fetch_symbol(symbol: str, existing: dict | None = None) -> dict:
    """
    Fetch earnings for one symbol from yfinance.
    Merges with `existing` to preserve already-stored past quarters (they never change).
    Only marks last_updated when data is actually retrieved — failed fetches are
    left with last_updated='2000-01-01' so they are retried on next page load.
    """
    today = date.today()
    # Start with old last_updated so a failed fetch doesn't cache itself as fresh
    old_last_updated = (existing or {}).get('last_updated', '2000-01-01')
    result: dict = {'last_updated': old_last_updated, 'upcoming': None, 'recent': [], '_error': None}

    # Carry forward past quarters from existing store
    past_dates: set = set()
    if existing and existing.get('recent'):
        result['recent'] = list(existing['recent'])
        past_dates = {r['date'] for r in result['recent']}

    ticker = yf.Ticker(symbol)

    # ── earnings_dates (primary source) ──────────────────────────────────────
    try:
        dates_df = ticker.get_earnings_dates(limit=16)
        if dates_df is not None and not dates_df.empty:
            upcoming_candidates: list = []

            for dt_idx, row in dates_df.iterrows():
                try:
                    dt = dt_idx.date()  # timezone-safe: works for both tz-aware and naive
                except Exception:
                    continue

                eps_act = row.get('Reported EPS')
                eps_est = row.get('EPS Estimate')
                surprise = row.get('Surprise(%)')

                if dt >= today:
                    upcoming_candidates.append((dt, eps_est))
                elif dt.isoformat() not in past_dates:
                    if pd.isna(eps_act) and pd.isna(eps_est):
                        continue
                    surprise_val = float(surprise) if pd.notna(surprise) else None
                    if (surprise_val is None
                            and pd.notna(eps_act) and pd.notna(eps_est)
                            and float(eps_est) != 0):
                        surprise_val = round(
                            (float(eps_act) - float(eps_est)) / abs(float(eps_est)) * 100, 2
                        )
                    price_chg = _price_reaction(ticker, dt)
                    result['recent'].append({
                        'date': dt.isoformat(),
                        'eps_actual': float(eps_act) if pd.notna(eps_act) else None,
                        'eps_estimate': float(eps_est) if pd.notna(eps_est) else None,
                        'surprise_pct': surprise_val,
                        'price_chg_pct': price_chg,
                    })
                    past_dates.add(dt.isoformat())

            result['recent'].sort(key=lambda x: x['date'], reverse=True)
            result['recent'] = result['recent'][:8]

            if upcoming_candidates:
                upcoming_candidates.sort(key=lambda x: x[0])
                dt, eps_est = upcoming_candidates[0]
                result['upcoming'] = {
                    'date': dt.isoformat(),
                    'eps_estimate': float(eps_est) if pd.notna(eps_est) else None,
                }
    except Exception as exc:
        result['_error'] = f'get_earnings_dates: {exc}'

    # ── calendar fallback for upcoming ───────────────────────────────────────
    if result['upcoming'] is None:
        try:
            cal = ticker.calendar
            if cal is not None:
                # calendar can be a dict (new yfinance) or a DataFrame (old)
                if isinstance(cal, dict):
                    raw_dates = cal.get('Earnings Date') or []
                    if raw_dates:
                        raw = raw_dates[0] if isinstance(raw_dates, list) else raw_dates
                        dt = pd.Timestamp(raw).date() if not isinstance(raw, date) else raw
                        if dt >= today:
                            result['upcoming'] = {'date': dt.isoformat(), 'eps_estimate': None}
                else:
                    for key in ('Earnings Date', 'earnings_date'):
                        if hasattr(cal, 'index') and key in cal.index:
                            val = cal.loc[key]
                            raw = val.iloc[0] if hasattr(val, 'iloc') else val
                            if pd.notna(raw):
                                dt = pd.Timestamp(raw).date()
                                if dt >= today:
                                    result['upcoming'] = {
                                        'date': dt.isoformat(),
                                        'eps_estimate': None,
                                    }
                                    break
        except Exception as exc:
            if not result['_error']:
                result['_error'] = f'calendar: {exc}'

    # Only stamp last_updated when we actually got something useful
    if result['recent'] or result['upcoming']:
        result['last_updated'] = today.isoformat()

    return result


# ── Batch refresh ─────────────────────────────────────────────────────────────

def refresh(symbols: list, store: dict, force: bool = False) -> tuple:
    """
    Fetch any symbols that are missing or stale (> _STALENESS_DAYS old).
    Returns (updated_store, list_of_symbols_actually_fetched).
    """
    today = date.today()
    fetched = []
    for sym in symbols:
        existing = store.get(sym)
        if existing:
            last = date.fromisoformat(existing.get('last_updated', '2000-01-01'))
            stale = (today - last).days >= _STALENESS_DAYS
        else:
            stale = True
        if force or stale:
            store[sym] = fetch_symbol(sym, existing)
            fetched.append(sym)
            time.sleep(0.3)
    return store, fetched
