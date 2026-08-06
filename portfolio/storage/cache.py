"""
On-disk cache of the last successful refresh.

Lets the dashboard open with data instead of an empty page, and lets you browse
without re-authenticating to E*TRADE. Local only — this is real holdings data and
the repo is public.

The whole portfolio dict round-trips: DataFrames become records, numpy scalars
become Python numbers, and NaN becomes null. Anything that will not serialise
raises here rather than silently writing a half-file, so the caller can report it.
"""

import json
import logging

import numpy as np
import pandas as pd

from portfolio import paths

LOGGER = logging.getLogger(__name__)

CACHE_FILE = paths.DATA_DIR / 'portfolio_cache.json'

#: Portfolio keys that hold DataFrames and need reconstructing on load.
FRAME_KEYS = ('holdings', 'transactions', 'cash_flows', 'income')

#: Columns parsed back to datetimes. Everything else is coerced to numeric when
#: it will convert cleanly, and left as text when it will not.
_DATE_COLUMNS = {'Date', 'Date Acquired', 'First', 'Last'}

#: Columns that must stay text even though they look numeric. Account and REFID
#: digits would otherwise become floats and lose their leading zeros — and
#: ``'1607'`` becoming ``1607.0`` breaks every counterparty lookup.
_TEXT_COLUMNS = {'Symbol', 'Ref ID', 'Counterparty', 'Account', 'Category',
                 'Transaction Type', 'Classification Note'}


def save_portfolio(portfolio: dict) -> None:
    """Write the portfolio dict to disk, raising if it cannot be serialised."""
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(json.dumps(to_jsonable(portfolio), indent=2))
    LOGGER.debug('portfolio cached to %s', CACHE_FILE)


def load_portfolio() -> dict | None:
    """Read the cached portfolio, or None when there is no usable cache."""
    if not CACHE_FILE.exists():
        return None
    try:
        return _migrate(from_jsonable(json.loads(CACHE_FILE.read_text())))
    except (json.JSONDecodeError, OSError) as exc:
        LOGGER.warning('portfolio cache unreadable (%s); ignoring it', exc)
        return None


def _migrate(portfolio: dict) -> dict:
    """
    Accept caches written before frames were tagged with ``__frame__``.

    Those stored each frame as a bare list of records, which :func:`from_jsonable`
    leaves as a list. Rebuilding them here means an existing cache keeps working
    across the upgrade instead of the dashboard opening empty.
    """
    for key in FRAME_KEYS:
        value = portfolio.get(key)
        if isinstance(value, list):
            portfolio[key] = records_to_frame(value)
        elif value is None:
            portfolio[key] = pd.DataFrame()
    return portfolio


# ── Serialisation ─────────────────────────────────────────────────────────────

def to_jsonable(obj):
    """
    Convert a portfolio dict into something ``json.dumps`` accepts.

    numpy scalars need explicit handling: ``np.float64`` happens to subclass
    ``float`` and serialises by luck, but ``np.int64`` does not subclass ``int``
    and raises. Counting on that distinction is how a cache write starts failing
    the day an integer metric is added.
    """
    if isinstance(obj, pd.DataFrame):
        return {'__frame__': json.loads(obj.to_json(orient='records', date_format='iso'))}
    if isinstance(obj, dict):
        return {str(k): to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [to_jsonable(v) for v in obj]
    if isinstance(obj, (pd.Timestamp, np.datetime64)):
        return pd.Timestamp(obj).isoformat()
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, np.floating):
        value = float(obj)
        return None if pd.isna(value) else value
    if isinstance(obj, np.bool_):
        return bool(obj)
    if isinstance(obj, float) and pd.isna(obj):
        return None
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    return str(obj)


def from_jsonable(obj):
    """Reverse :func:`to_jsonable`, restoring DataFrames and their dtypes."""
    if isinstance(obj, dict):
        if set(obj) == {'__frame__'}:
            return records_to_frame(obj['__frame__'])
        return {k: from_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [from_jsonable(v) for v in obj]
    return obj


def records_to_frame(records) -> pd.DataFrame:
    """Rebuild a DataFrame from records, restoring dates and numeric columns."""
    if not records:
        return pd.DataFrame()
    frame = pd.DataFrame(records)
    for column in frame.columns:
        if column in _DATE_COLUMNS:
            frame[column] = pd.to_datetime(frame[column], errors='coerce')
        elif column in _TEXT_COLUMNS:
            frame[column] = frame[column].astype('object')
        else:
            converted = pd.to_numeric(frame[column], errors='coerce')
            # Only accept the conversion when it loses nothing; otherwise a
            # mostly-text column would be blanked into NaNs.
            if converted.notna().sum() >= frame[column].notna().sum():
                frame[column] = converted
    return frame
