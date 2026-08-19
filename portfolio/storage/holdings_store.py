"""
A durable copy of today's positions.

Holdings are the one thing the app cannot rebuild for itself: prices come from
Yahoo, transactions accumulate in the ledger, but quantity and cost basis come
only from E*TRADE — and only through an OAuth flow that means clicking out to
the broker, authorising, and pasting a verifier back.

That is tolerable on a laptop where ``data/`` persists. It is not tolerable on a
host that hands every restart a fresh filesystem, where the alternative is
opening the dashboard on a phone to an empty page and a login prompt.

**Snapshot semantics, unlike the ledger.** Holdings describe *today*: the whole
table is replaced on each save. Only transactions have to accumulate, because
only transactions describe events that happened once and can fall out of the
broker's window. Conflating the two is the mistake this package already made
once, in the other direction.
"""

import json
import logging

import pandas as pd

from portfolio.storage import db

LOGGER = logging.getLogger(__name__)

#: DataFrame column -> table column. The frame's names carry spaces and a '%',
#: which are legal in neither SQL identifiers nor a sane life.
#: Declared in the order `etrade.consolidate_holdings` produces, so a saved and
#: reloaded frame is identical rather than merely equivalent — some tables render
#: columns in frame order.
_COLUMNS = {
    'Symbol': 'symbol',
    'Symbol Description': 'description',
    'Current Price': 'current_price',
    'Quantity': 'quantity',
    'Date Acquired': 'date_acquired',
    'Price Paid': 'price_paid',
    'Total Cost': 'total_cost',
    'Market Value': 'market_value',
    'Total Gain': 'total_gain',
    'Total Gain %': 'total_gain_pct',
    'Percent of Portfolio': 'percent_of_portfolio',
}
_REVERSE = {v: k for k, v in _COLUMNS.items()}

#: Keys held in the `meta` table beside the positions themselves.
_META_FETCHED = 'holdings_fetched_at'
_META_REPORTED = 'holdings_reported_total'
_META_BALANCES = 'holdings_account_balances'


def save(holdings, fetched_at=None, reported_total=None, account_balances=()) -> int:
    """
    Replace the stored positions with this set. Returns the row count written.

    The delete and the insert share one transaction, so a failure mid-write
    leaves the previous snapshot intact rather than an empty table — losing
    holdings would mean an E*TRADE round trip to get them back.
    """
    from portfolio.storage import ledger

    if holdings is None or (hasattr(holdings, 'empty') and holdings.empty):
        return 0

    frame = holdings.rename(columns=_COLUMNS)
    columns = [c for c in _COLUMNS.values() if c in frame.columns]
    payload = [
        tuple(_value(row.get(column)) for column in columns)
        for row in frame[columns].to_dict('records')
    ]

    placeholders = ','.join('?' for _ in columns)
    with ledger._connect() as connection:
        connection.execute(db.q('DELETE FROM holdings'))
        db.executemany(
            connection,
            db.q(f'INSERT INTO holdings ({",".join(columns)}) VALUES ({placeholders})'),
            payload,
        )
        _set_meta(connection, _META_FETCHED, fetched_at)
        _set_meta(connection, _META_REPORTED, reported_total)
        _set_meta(connection, _META_BALANCES, list(account_balances))
    LOGGER.info('holdings stored: %d position(s)', len(payload))
    return len(payload)


def load() -> dict | None:
    """
    The stored positions and the balances that go with them, or None.

    Returns the same shape the refresh produces, so a caller can hand it
    straight to :func:`portfolio.build.build_portfolio`.
    """
    from portfolio.storage import ledger

    with ledger._connect() as connection:
        rows = connection.execute('SELECT * FROM holdings').fetchall()
        meta = {
            key: _get_meta(connection, key)
            for key in (_META_FETCHED, _META_REPORTED, _META_BALANCES)
        }

    if not rows:
        return None

    frame = pd.DataFrame([dict(row) for row in rows]).rename(columns=_REVERSE)
    frame = frame[[c for c in _COLUMNS if c in frame.columns]]
    if 'Date Acquired' in frame.columns:
        frame['Date Acquired'] = pd.to_datetime(frame['Date Acquired'], errors='coerce')

    return {
        'holdings': frame,
        'fetched_at': meta[_META_FETCHED],
        'reported_total': meta[_META_REPORTED],
        'account_balances': meta[_META_BALANCES] or [],
    }


def _value(value):
    """
    Numbers and text through; every flavour of missing becomes NULL.

    ``pd.isna`` first and unconditionally: NaT is neither a float nor a
    Timestamp, so type-based checks let it reach the driver, which rejects it.
    """
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):     # arrays and other non-scalars
        pass
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if hasattr(value, 'item'):          # numpy scalar -> Python scalar
        return value.item()
    return value


def _set_meta(connection, key, value) -> None:
    connection.execute(
        db.q('INSERT INTO meta (key, value) VALUES (?,?) '
             'ON CONFLICT (key) DO UPDATE SET value = excluded.value'),
        (key, json.dumps(value)),
    )


def _get_meta(connection, key):
    row = connection.execute(
        db.q('SELECT value FROM meta WHERE key = ?'), (key,)).fetchone()
    if row is None:
        return None
    try:
        return json.loads(row['value'])
    except (json.JSONDecodeError, TypeError):
        return row['value']
