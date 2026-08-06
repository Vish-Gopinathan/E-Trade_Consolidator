"""
Persistent earnings store: past quarters and the next expected report date.

Past quarters never change once reported, so they are written once and kept.
Only the upcoming date is re-fetched, weekly. Persistence is local disk only —
this file is derived from real holdings and the repo is public, so it is never
pushed to a remote. On an ephemeral host the store rebuilds on first page load.

**Why the data sources are ordered the way they are.** ``Ticker.earnings_history``
and ``Ticker.calendar`` are JSON endpoints; ``Ticker.get_earnings_dates`` scrapes
an HTML table through ``pandas.read_html`` and raises ``ImportError`` when lxml is
missing. That import error was caught into ``_error`` and every symbol stored
empty earnings — which is why EPS never populated and history was always blank.
The JSON endpoints are now primary and always sufficient; ``get_earnings_dates``
is a bonus that supplies true announcement dates when lxml is installed.

**Quarter end is not report date.** ``earnings_history`` is indexed by fiscal
quarter end (NVDA's quarter ending 2026-04-30 was reported on 2026-05-27, four
weeks later). Rows carry ``date_is_report_date`` so the UI can label the column
honestly, and the price reaction is computed only when a real announcement date is
known — measuring a one-day move around the wrong day is worse than showing
nothing.
"""

import json
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import date

import pandas as pd
import yfinance as yf

from portfolio import paths

LOGGER = logging.getLogger(__name__)

# ETFs and trusts have no fundamentals, so yfinance logs a 404 for each one on
# every refresh. That is an expected outcome here, not a fault — the symbol is
# simply reported as having no earnings — so keep it out of the console.
logging.getLogger('yfinance').setLevel(logging.CRITICAL)

STORE_PATH = paths.DATA_DIR / 'earnings_store.json'

_STALENESS_DAYS = 7   # upcoming dates drift; past quarters never do
_MAX_QUARTERS = 8     # kept per symbol
_MAX_WORKERS = 6      # yfinance tolerates this comfortably; higher starts throttling
#: Sentinel ``last_updated`` for a symbol that has never returned data, so it is
#: always considered stale and retried. Callers must exclude it when reporting
#: when data was last refreshed.
NEVER = '2000-01-01'


# ── Disk I/O ──────────────────────────────────────────────────────────────────

def load() -> dict:
    """Return the stored data, or an empty dict when there is none."""
    if not STORE_PATH.exists():
        return {}
    try:
        return json.loads(STORE_PATH.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        LOGGER.warning('earnings store unreadable (%s); starting fresh', exc)
        return {}


def save(store: dict) -> None:
    """Write the store to disk. Local only — never to a remote."""
    STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STORE_PATH.write_text(json.dumps(store, indent=2, sort_keys=True))


# ── Per-symbol fetch ──────────────────────────────────────────────────────────

def fetch_symbol(symbol: str, existing: dict | None = None) -> dict:
    """
    Fetch one symbol's earnings, merged with what is already stored.

    Already-known quarters are preserved rather than re-fetched. ``last_updated``
    advances only when something was actually retrieved, so a failed fetch is
    retried on the next page load instead of caching itself as fresh.
    """
    existing = existing or {}
    result = {
        'last_updated': existing.get('last_updated', NEVER),
        'upcoming': None,
        'recent': list(existing.get('recent', [])),
        '_error': None,
    }
    known_quarters = {row.get('quarter') or row.get('date') for row in result['recent']}

    ticker = yf.Ticker(symbol)
    errors = []

    # ── Past quarters: JSON, always available ─────────────────────────────────
    try:
        for row in _quarters_from_history(ticker):
            if row['quarter'] not in known_quarters:
                result['recent'].append(row)
                known_quarters.add(row['quarter'])
    except Exception as exc:
        errors.append(f'earnings_history: {exc}')

    # ── Announcement dates: HTML scrape, needs lxml. Enrichment only ──────────
    try:
        _apply_announcement_dates(ticker, result['recent'])
    except ImportError:
        LOGGER.debug('%s: lxml missing, using quarter-end dates', symbol)
    except Exception as exc:
        LOGGER.debug('%s: earnings dates unavailable (%s)', symbol, exc)

    result['recent'].sort(key=lambda row: row['date'], reverse=True)
    result['recent'] = result['recent'][:_MAX_QUARTERS]

    # ── Price reaction: one price history for every quarter at once ───────────
    try:
        _apply_price_reactions(ticker, result['recent'])
    except Exception as exc:
        LOGGER.debug('%s: price reactions unavailable (%s)', symbol, exc)

    # ── Upcoming ──────────────────────────────────────────────────────────────
    try:
        result['upcoming'] = _upcoming_from_calendar(ticker)
    except Exception as exc:
        errors.append(f'calendar: {exc}')

    if result['recent'] or result['upcoming']:
        result['last_updated'] = date.today().isoformat()
    if errors:
        result['_error'] = '; '.join(errors)
    return result


def _quarters_from_history(ticker) -> list:
    """
    Past quarters from ``Ticker.earnings_history``.

    Columns are ``epsActual``, ``epsEstimate``, ``epsDifference`` and
    ``surprisePercent``. **surprisePercent is a fraction**: 0.0410 means +4.10%,
    so it is scaled here rather than displayed as 0.04%.
    """
    history = ticker.earnings_history
    if history is None or history.empty:
        return []

    rows = []
    for index, record in history.iterrows():
        try:
            quarter_end = pd.Timestamp(index).date()
        except (TypeError, ValueError):
            continue

        actual = _number(record.get('epsActual'))
        estimate = _number(record.get('epsEstimate'))
        if actual is None and estimate is None:
            continue

        surprise = _number(record.get('surprisePercent'))
        if surprise is not None:
            surprise = round(surprise * 100, 2)
        elif actual is not None and estimate:
            surprise = round((actual - estimate) / abs(estimate) * 100, 2)

        rows.append({
            'quarter': quarter_end.isoformat(),
            'date': quarter_end.isoformat(),   # replaced if a report date is found
            'date_is_report_date': False,
            'eps_actual': actual,
            'eps_estimate': estimate,
            'surprise_pct': surprise,
            'price_chg_pct': None,
        })
    return rows


def _apply_announcement_dates(ticker, quarters: list) -> None:
    """
    Upgrade quarter-end dates to real announcement dates where possible.

    Needs lxml. Each announcement is matched to the quarter it reports on: the
    report follows quarter end by roughly a month, so the nearest quarter within
    100 days before the announcement is the right one.
    """
    if not quarters:
        return
    reported = ticker.get_earnings_dates(limit=16)
    if reported is None or reported.empty:
        return

    announcements = []
    for index, _ in reported.iterrows():
        try:
            announcements.append(pd.Timestamp(index).date())
        except (TypeError, ValueError):
            continue

    for row in quarters:
        quarter_end = date.fromisoformat(row['quarter'])
        candidates = [
            announced for announced in announcements
            if 0 <= (announced - quarter_end).days <= 100
        ]
        if candidates:
            row['date'] = min(candidates).isoformat()
            row['date_is_report_date'] = True


def _apply_price_reactions(ticker, quarters: list) -> None:
    """
    Fill in the one-day move around each report.

    One ``history`` call covers every quarter. The old code fetched a two-week
    window per quarter per symbol — roughly 170 requests for a 21-symbol
    portfolio, which is what made the page slow.

    Skipped for rows whose date is a quarter end rather than an announcement:
    the price move around the wrong day is a misleading number, not a rough one.
    """
    datable = [row for row in quarters if row.get('date_is_report_date')]
    if not datable:
        return

    earliest = min(date.fromisoformat(row['date']) for row in datable)
    prices = ticker.history(
        start=pd.Timestamp(earliest) - pd.Timedelta(days=10),
        end=pd.Timestamp(date.today()) + pd.Timedelta(days=1),
        auto_adjust=True,
    )
    if prices.empty:
        return
    if prices.index.tz is not None:
        prices.index = prices.index.tz_localize(None)
    closes = prices['Close']

    for row in datable:
        stamp = pd.Timestamp(date.fromisoformat(row['date']))
        before = closes[closes.index <= stamp]
        after = closes[closes.index > stamp]
        if before.empty or after.empty:
            continue
        opening, closing = float(before.iloc[-1]), float(after.iloc[0])
        if opening:
            row['price_chg_pct'] = round((closing - opening) / opening * 100, 2)


def _upcoming_from_calendar(ticker) -> dict | None:
    """
    Next expected report date, with the consensus EPS estimate.

    ``calendar['Earnings Average']`` is the analyst consensus. The previous
    version hardcoded the estimate to None, which is why the Upcoming table's EPS
    column was always empty even when the date was right.
    """
    calendar = ticker.calendar
    if not calendar:
        return None

    if isinstance(calendar, dict):
        raw_dates = calendar.get('Earnings Date') or []
        raw = raw_dates[0] if isinstance(raw_dates, list) and raw_dates else raw_dates
        estimate = _number(calendar.get('Earnings Average'))
    else:  # older yfinance returned a DataFrame
        if not hasattr(calendar, 'index') or 'Earnings Date' not in calendar.index:
            return None
        value = calendar.loc['Earnings Date']
        raw = value.iloc[0] if hasattr(value, 'iloc') else value
        estimate = None

    if raw is None or (isinstance(raw, list) and not raw):
        return None
    try:
        upcoming = raw if isinstance(raw, date) else pd.Timestamp(raw).date()
    except (TypeError, ValueError):
        return None

    if upcoming < date.today():
        return None
    return {'date': upcoming.isoformat(), 'eps_estimate': estimate}


def _number(value) -> float | None:
    """Coerce a yfinance cell to float, treating NaN and blanks as missing."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return None if pd.isna(number) else number


# ── Batch refresh ─────────────────────────────────────────────────────────────

def stale_symbols(symbols, store: dict) -> list:
    """Symbols missing from the store or older than the staleness window."""
    today = date.today()
    outdated = []
    for symbol in symbols:
        entry = store.get(symbol)
        if not entry:
            outdated.append(symbol)
            continue
        try:
            last = date.fromisoformat(entry.get('last_updated', NEVER))
        except ValueError:
            last = date.fromisoformat(NEVER)
        if (today - last).days >= _STALENESS_DAYS:
            outdated.append(symbol)
    return outdated


def refresh(symbols, store: dict, force: bool = False, on_progress=None) -> tuple:
    """
    Fetch missing or stale symbols in parallel and persist as results arrive.

    Writing after every symbol matters: the first run for a 21-symbol portfolio
    takes long enough that a user may navigate away, and an all-or-nothing write
    would mean starting over.

    Args:
        on_progress: Called as ``(done, total, symbol)`` after each symbol, for
            a progress bar.

    Returns:
        ``(store, fetched_symbols)``.
    """
    targets = list(symbols) if force else stale_symbols(symbols, store)
    if not targets:
        return store, []

    fetched = []
    with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as pool:
        results = pool.map(lambda s: (s, _safe_fetch(s, store.get(s))), targets)
        for done, (symbol, entry) in enumerate(results, start=1):
            store[symbol] = entry
            fetched.append(symbol)
            save(store)
            if on_progress:
                on_progress(done, len(targets), symbol)

    return store, fetched


def _safe_fetch(symbol: str, existing: dict | None) -> dict:
    """Never let one bad symbol abort the batch; record the error on the entry."""
    try:
        return fetch_symbol(symbol, existing)
    except Exception as exc:
        LOGGER.warning('earnings fetch failed for %s: %s', symbol, exc)
        entry = dict(existing or {})
        entry.setdefault('recent', [])
        entry.setdefault('upcoming', None)
        entry['last_updated'] = entry.get('last_updated', NEVER)
        entry['_error'] = str(exc)
        return entry
