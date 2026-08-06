"""
Persistent daily price / split / metadata store backed by yfinance.

Reconstructing historical portfolio value needs a close price for every symbol on
every day, which is far too much data to re-download on each page load. This
module keeps a JSON store on disk and only fetches the date ranges it is missing.

Prices are fetched with ``auto_adjust=False``, which means they are **adjusted
for splits but not for dividends** — that is what Yahoo returns, regardless of
the flag's name. NVDA's 2024-06-05 close comes back as $122.44, not the $1,224 it
actually traded at before the 10:1 split.

Both halves of that matter. Split-adjusted prices have to be paired with
split-adjusted share counts, which is why lib/portfolio_history.py scales
historical trade quantities to today's share basis. Leaving dividends
unadjusted is what we want: a dividend was paid out as cash and is picked up by
the cash reconstruction, so adjusting the price for it too would count it twice.

Split ratios are stored alongside the prices because reconstructing share counts
still needs them.
"""

import json
from datetime import date, timedelta

import pandas as pd
import yfinance as yf

from portfolio import paths

STORE_PATH = paths.DATA_DIR / 'price_store.json'

_BATCH = 20            # tickers per yfinance download call
_RETRY_MISSING_DAYS = 7  # how long before re-trying a symbol yfinance had no data for

# Keywords that reclassify an ETF/trust into a more useful asset bucket
_CRYPTO_HINTS = ('bitcoin', 'ethereum', 'crypto', 'solana', 'blockchain trust')
_COMMODITY_HINTS = ('gold', 'silver', 'oil fund', 'commodity', 'platinum', 'palladium')


# ── Disk I/O ──────────────────────────────────────────────────────────────────

def load() -> dict:
    if STORE_PATH.exists():
        try:
            store = json.loads(STORE_PATH.read_text())
        except Exception:
            store = {}
    else:
        store = {}
    store.setdefault('prices', {})
    store.setdefault('splits', {})
    store.setdefault('meta', {})
    store.setdefault('unavailable', {})
    return store


def save(store: dict):
    STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STORE_PATH.write_text(json.dumps(store, separators=(',', ':')))


def clear():
    if STORE_PATH.exists():
        STORE_PATH.unlink()


def coverage(store: dict) -> dict:
    """{symbol: (first_date, last_date, n_points)} for what is currently stored."""
    out = {}
    for sym, series in store.get('prices', {}).items():
        if not series:
            continue
        keys = sorted(series)
        out[sym] = (keys[0], keys[-1], len(keys))
    return out


# ── Fetching ──────────────────────────────────────────────────────────────────

def _normalise_index(df: pd.DataFrame) -> pd.DataFrame:
    idx = pd.DatetimeIndex(df.index)
    if idx.tz is not None:
        idx = idx.tz_localize(None)
    df = df.copy()
    df.index = idx.normalize()
    return df


def _download(symbols: list, start: date, end: date) -> dict:
    """
    Return {symbol: DataFrame with Close / Stock Splits}. Symbols yfinance has no
    data for are simply absent from the result.
    """
    out = {}
    if not symbols:
        return out

    raw = yf.download(
        tickers=symbols,
        start=start.isoformat(),
        end=(end + timedelta(days=1)).isoformat(),  # yfinance end is exclusive
        auto_adjust=False,
        actions=True,
        progress=False,
        group_by='ticker',
        threads=True,
    )
    if raw is None or raw.empty:
        return out

    raw = _normalise_index(raw)

    for sym in symbols:
        try:
            if isinstance(raw.columns, pd.MultiIndex):
                if sym not in raw.columns.get_level_values(0):
                    continue
                sub = raw[sym]
            else:
                sub = raw  # single-ticker download returns flat columns
            if 'Close' not in sub.columns:
                continue
            sub = sub[sub['Close'].notna()]
            if sub.empty:
                continue
            out[sym] = sub
        except Exception:
            continue
    return out


def _needed_range(store: dict, sym: str, start: date, end: date):
    """Return (fetch_start, fetch_end) or None when the store already covers it."""
    series = store['prices'].get(sym) or {}
    if not series:
        return start, end
    keys = sorted(series)
    have_start = date.fromisoformat(keys[0])
    have_end = date.fromisoformat(keys[-1])

    # Extend on whichever side is short. Refetching a slightly wider window than
    # strictly necessary is cheap and avoids off-by-one issues around holidays.
    fetch_start = min(start, have_start)
    fetch_end = max(end, have_end)
    if start >= have_start and end <= have_end:
        return None
    return fetch_start, fetch_end


def ensure(symbols, start: date, end: date, progress=None, force: bool = False) -> dict:
    """
    Make sure the store holds daily closes for every symbol over [start, end].

    Args:
        symbols: iterable of ticker strings.
        start, end: datetime.date bounds (inclusive).
        progress: optional callable(fraction, message) for UI feedback.
        force: refetch even when coverage looks complete.

    Returns the loaded store dict (already saved to disk).
    """
    symbols = [s for s in dict.fromkeys(symbols) if s]
    store = load()
    today = date.today()

    todo = []
    for sym in symbols:
        if force:
            todo.append((sym, start, end))
            continue
        last_miss = store['unavailable'].get(sym)
        if last_miss:
            try:
                if (today - date.fromisoformat(last_miss)).days < _RETRY_MISSING_DAYS:
                    continue
            except ValueError:
                pass
        rng = _needed_range(store, sym, start, end)
        if rng:
            todo.append((sym, rng[0], rng[1]))

    if not todo:
        return store

    # Group symbols that share an identical fetch window so they can be batched
    groups: dict = {}
    for sym, s, e in todo:
        groups.setdefault((s, e), []).append(sym)

    total = len(todo)
    done = 0
    for (s, e), syms in groups.items():
        for i in range(0, len(syms), _BATCH):
            batch = syms[i:i + _BATCH]
            if progress:
                progress(done / total, f'Fetching prices — {", ".join(batch[:4])}…')
            try:
                fetched = _download(batch, s, e)
            except Exception:
                fetched = {}

            for sym in batch:
                sub = fetched.get(sym)
                if sub is None:
                    store['unavailable'][sym] = today.isoformat()
                    continue
                store['unavailable'].pop(sym, None)

                closes = store['prices'].setdefault(sym, {})
                for ts, px in sub['Close'].items():
                    if pd.notna(px):
                        closes[ts.date().isoformat()] = round(float(px), 6)

                if 'Stock Splits' in sub.columns:
                    sp = sub['Stock Splits']
                    sp = sp[(sp.notna()) & (sp != 0)]
                    if not sp.empty:
                        stored = store['splits'].setdefault(sym, {})
                        for ts, ratio in sp.items():
                            stored[ts.date().isoformat()] = float(ratio)

            done += len(batch)

    if progress:
        progress(1.0, 'Prices up to date')
    save(store)
    return store


def ensure_metadata(symbols, progress=None) -> dict:
    """
    Fetch and cache per-symbol metadata (name, quote type, sector) used for the
    asset-type / sector breakdowns. Fetched once per symbol and then reused.
    """
    symbols = [s for s in dict.fromkeys(symbols) if s]
    store = load()
    todo = [s for s in symbols if s not in store['meta']]
    if not todo:
        return store

    for n, sym in enumerate(todo):
        if progress:
            progress(n / len(todo), f'Classifying {sym}…')
        info = {}
        try:
            info = yf.Ticker(sym).get_info() or {}
        except Exception:
            info = {}
        name = info.get('longName') or info.get('shortName') or ''
        quote_type = (info.get('quoteType') or '').upper()
        store['meta'][sym] = {
            'name': name,
            'quote_type': quote_type,
            'sector': info.get('sector') or '',
            'asset_type': _asset_type(quote_type, name),
        }

    if progress:
        progress(1.0, 'Classification complete')
    save(store)
    return store


def _asset_type(quote_type: str, name: str) -> str:
    lname = (name or '').lower()
    if quote_type in ('ETF', 'MUTUALFUND'):
        if any(h in lname for h in _CRYPTO_HINTS):
            return 'Crypto ETP'
        if any(h in lname for h in _COMMODITY_HINTS):
            return 'Commodity ETP'
        return 'ETF'
    if quote_type == 'EQUITY':
        return 'Stock'
    if quote_type in ('CRYPTOCURRENCY',):
        return 'Crypto'
    if any(h in lname for h in _CRYPTO_HINTS):
        return 'Crypto ETP'
    return 'Other'


# ── Reading back ──────────────────────────────────────────────────────────────

def price_frame(store: dict, symbols, index: pd.DatetimeIndex) -> pd.DataFrame:
    """
    Wide DataFrame of split-adjusted closes (index = supplied dates, columns =
    symbols).

    Values are forward-filled across weekends and holidays so a share count held
    on a non-trading day is still valued at the last traded close. Dates before a
    symbol's first available price stay NaN.
    """
    cols = {}
    for sym in symbols:
        series = store.get('prices', {}).get(sym) or {}
        if not series:
            cols[sym] = pd.Series(index=index, dtype=float)
            continue
        s = pd.Series(series)
        s.index = pd.to_datetime(s.index)
        s = s.sort_index()
        cols[sym] = s.reindex(s.index.union(index)).ffill().reindex(index)
    return pd.DataFrame(cols, index=index)


def split_frame(store: dict, symbols, index: pd.DatetimeIndex) -> pd.DataFrame:
    """Wide DataFrame of split ratios (1.0 where no split occurred)."""
    df = pd.DataFrame(1.0, index=index, columns=list(symbols))
    for sym in symbols:
        for day, ratio in (store.get('splits', {}).get(sym) or {}).items():
            ts = pd.Timestamp(day)
            if ts in df.index:
                df.at[ts, sym] = float(ratio)
    return df


def trading_days(store: dict, symbols, start: date, end: date) -> pd.DatetimeIndex:
    """
    Union of dates on which any of the given symbols traded — a practical stand-in
    for the market calendar, so charts don't plot flat weekend points.
    """
    days = set()
    for sym in symbols:
        for d in (store.get('prices', {}).get(sym) or {}):
            days.add(d)
    idx = pd.DatetimeIndex(sorted(pd.Timestamp(d) for d in days))
    if idx.empty:
        return pd.DatetimeIndex([])
    return idx[(idx >= pd.Timestamp(start)) & (idx <= pd.Timestamp(end))]
