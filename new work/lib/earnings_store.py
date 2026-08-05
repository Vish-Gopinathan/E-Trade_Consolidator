"""
Persistent earnings store.

Stores past quarters (static — never change) and upcoming date (refreshed weekly).
Data source: yfinance. GitHub Contents API used for persistence across Streamlit
Cloud restarts when GITHUB_TOKEN / GITHUB_REPO secrets are configured.
"""

import base64
import json
import time
from datetime import date
from pathlib import Path

import pandas as pd
import yfinance as yf

STORE_PATH = Path(__file__).parent.parent / 'data' / 'earnings_store.json'
_GITHUB_PATH = 'new work/data/earnings_store.json'
_GITHUB_API = 'https://api.github.com'
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


# ── GitHub I/O ────────────────────────────────────────────────────────────────

def _gh_headers(token: str) -> dict:
    return {'Authorization': f'token {token}', 'Accept': 'application/vnd.github+json'}


def _gh_load(get_secret_fn) -> dict | None:
    import requests
    token = get_secret_fn('GITHUB_TOKEN', '')
    repo = get_secret_fn('GITHUB_REPO', '')
    if not token or not repo:
        return None
    try:
        owner, repo_name = repo.split('/', 1)
        url = f'{_GITHUB_API}/repos/{owner}/{repo_name}/contents/{_GITHUB_PATH}'
        resp = requests.get(url, headers=_gh_headers(token), timeout=10)
        if resp.status_code != 200:
            return None
        return json.loads(base64.b64decode(resp.json()['content']).decode())
    except Exception:
        return None


def _gh_save(store: dict, get_secret_fn):
    import requests
    token = get_secret_fn('GITHUB_TOKEN', '')
    repo = get_secret_fn('GITHUB_REPO', '')
    if not token or not repo:
        return
    try:
        owner, repo_name = repo.split('/', 1)
        url = f'{_GITHUB_API}/repos/{owner}/{repo_name}/contents/{_GITHUB_PATH}'
        headers = _gh_headers(token)
        content_b64 = base64.b64encode(json.dumps(store, indent=2).encode()).decode()
        existing = requests.get(url, headers=headers, timeout=10)
        payload: dict = {'message': 'chore: update earnings store', 'content': content_b64}
        if existing.status_code == 200:
            payload['sha'] = existing.json()['sha']
        requests.put(url, headers=headers, json=payload, timeout=15)
    except Exception:
        pass


def save(store: dict, get_secret_fn=None):
    _write_disk(store)
    if get_secret_fn:
        _gh_save(store, get_secret_fn)


def load_with_github_fallback(get_secret_fn) -> dict:
    """Load from disk; fall back to GitHub on cold start (empty disk)."""
    store = load()
    if not store and get_secret_fn:
        gh = _gh_load(get_secret_fn)
        if gh:
            store = gh
            _write_disk(store)  # cache to disk for this session
    return store


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
    """
    today = date.today()
    result: dict = {'last_updated': today.isoformat(), 'upcoming': None, 'recent': []}

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
    except Exception:
        pass

    # ── calendar fallback for upcoming ───────────────────────────────────────
    if result['upcoming'] is None:
        try:
            cal = ticker.calendar
            if cal is not None:
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
        except Exception:
            pass

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
