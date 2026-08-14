"""
The accumulating transaction ledger.

Every other store in this package is a *snapshot* — written whole, read whole,
overwritten on the next refresh. That is correct for holdings, which only ever
describe today. It was quietly wrong for transactions.

E*TRADE serves roughly two years of history, and the window slides forward with
today's date. Because :mod:`portfolio.storage.cache` overwrites, a deposit made
25 months ago was in yesterday's total and gone from today's — the reported
"Total Deposited" fell with no withdrawal behind it. On a real account a large
share of all deposits sat within six months of that edge, with the oldest
already past it, so the figure eroded a little at a time.

So transactions accumulate here instead. The ledger only ever grows: each
refresh unions its rows in, and history that E*TRADE has stopped serving stays
because we already saw it. That also repairs a second failure — pass 2 pairs
transfer legs by REFID, so when one leg aged out of the window the survivor fell
through to "external" and an internal transfer became a phantom deposit. Both
legs now stay present.

**SQLite, not JSON.** ``INSERT OR IGNORE`` against a ``UNIQUE`` dedup key makes a
re-merge idempotent at the storage layer rather than in pandas; manual backfill
rows need stable ids to be editable and a ``source`` column to stay honest about
provenance; and typed columns remove the ``'1607'`` -> ``1607.0`` coercion that
:mod:`portfolio.storage.cache` carries ``_TEXT_COLUMNS`` to defend against.

Local disk only. This is holdings and cash-flow data and the repository is
public; ``data/`` is gitignored and nothing here reaches a remote.
"""

import datetime
import json
import logging
import sqlite3

import pandas as pd

from portfolio import paths

LOGGER = logging.getLogger(__name__)

DB_PATH = paths.DATA_DIR / 'ledger.db'

#: Rows the app fetched from E*TRADE.
SOURCE_ETRADE = 'etrade'
#: Rows typed in by hand to cover history the API will not serve.
SOURCE_MANUAL = 'manual'
#: Rows imported from an E*TRADE CSV download.
SOURCE_CSV = 'csv'

#: Account label for money whose destination account cannot be established —
#: an account since closed, or one under a name it no longer carries. Honest
#: about the gap and reassignable later. The portfolio-level identity only needs
#: to know a dollar was external and not already counted, not where it landed.
UNATTRIBUTED = 'Unattributed'

#: DataFrame column order the rest of the app expects from a transaction frame.
FRAME_COLUMNS = [
    'Date', 'Security Name', 'Symbol', 'Quantity', 'Price', 'Total Value',
    'Transaction Type', 'Category', 'Account', 'Ref ID', 'Counterparty',
    'Transaction ID', 'Account ID Key', 'Account Last4', 'Source', 'Note',
]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS transactions (
    id              INTEGER PRIMARY KEY,
    dedup_key       TEXT    NOT NULL UNIQUE,
    txn_id          TEXT,
    date            TEXT    NOT NULL,
    security_name   TEXT,
    symbol          TEXT,
    quantity        REAL,
    price           REAL,
    total_value     REAL,
    txn_type        TEXT,
    category        TEXT,
    account_name    TEXT,
    account_id_key  TEXT,
    account_last4   TEXT,
    ref_id          TEXT,
    counterparty    TEXT,
    source          TEXT    NOT NULL,
    note            TEXT,
    created_at      TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_transactions_date ON transactions(date);

CREATE TABLE IF NOT EXISTS accounts (
    account_id_key  TEXT PRIMARY KEY,
    account_last4   TEXT,
    account_name    TEXT,
    updated_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""

#: Maps a ledger column onto the DataFrame column the app uses.
_COLUMN_MAP = {
    'date': 'Date', 'security_name': 'Security Name', 'symbol': 'Symbol',
    'quantity': 'Quantity', 'price': 'Price', 'total_value': 'Total Value',
    'txn_type': 'Transaction Type', 'category': 'Category',
    'account_name': 'Account', 'ref_id': 'Ref ID', 'counterparty': 'Counterparty',
    'txn_id': 'Transaction ID', 'account_id_key': 'Account ID Key',
    'account_last4': 'Account Last4', 'source': 'Source', 'note': 'Note',
}


# ── Connection ────────────────────────────────────────────────────────────────

def _connect() -> sqlite3.Connection:
    """Open the ledger, creating the file and schema on first use."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    connection.executescript(_SCHEMA)
    return connection


# ── Dedup identity ────────────────────────────────────────────────────────────

def dedup_key(row: dict) -> str:
    """
    Stable identity for one transaction.

    ``Ref ID`` cannot serve here despite looking like an id: only 55 of 346 rows
    on this account carry one, and it identifies a *transfer pair* rather than a
    row — 37 distinct values across those 55 rows, by design, because both legs
    share it.

    E*TRADE's own ``transactionId`` is the real key. Rows that predate its
    capture — anything seeded from the old JSON cache — and every hand-entered
    row fall back to a composite of the fields that identify a transaction
    economically.

    **The two keys never collide, which is a trap.** A transaction seeded from
    the old cache keys on the composite; the *same* transaction fetched later
    keys on ``etrade:{id}``. Nothing stops both landing, and on the first real
    refresh after the ledger shipped that doubled the reported deposits.
    :func:`_adopt_legacy` closes it: a row arriving with a transaction id first
    looks for an existing id-less row describing the same event and claims it.
    """
    txn_id = _clean_text(row.get('Transaction ID'))
    if txn_id:
        return f'etrade:{txn_id}'

    date = _iso_date(row.get('Date'))
    parts = [
        _clean_text(row.get('Account')),
        date,
        _clean_text(row.get('Transaction Type')),
        _number_key(row.get('Total Value')),
        _clean_text(row.get('Symbol')),
        _number_key(row.get('Quantity')),
        _clean_text(row.get('Security Name')),
    ]
    return 'composite:' + '|'.join(parts)


def _clean_text(value) -> str:
    """Normalise a value to comparable text, treating NaN and None as empty."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ''
    return str(value).strip()


def _number_key(value) -> str:
    """Round a number to cents so float noise cannot split one row into two."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return ''
    return '' if pd.isna(number) else f'{number:.2f}'


def _iso_date(value) -> str:
    """Date as ``YYYY-MM-DD``. Times are dropped: they vary between the list and
    detail endpoints for the same transaction, which would defeat deduping."""
    timestamp = pd.to_datetime(value, errors='coerce')
    return '' if pd.isna(timestamp) else timestamp.strftime('%Y-%m-%d')


# ── Reading ───────────────────────────────────────────────────────────────────

def load(seed: bool = True) -> pd.DataFrame:
    """
    Every transaction the ledger holds, newest first.

    On first call the table is seeded from the existing JSON stores — see
    :func:`seed_from_stores`. That is not a convenience: those files are the only
    surviving copy of the rows that have already aged out of E*TRADE's window,
    and a refresh that merged before seeding would lose them permanently.
    """
    if seed:
        seed_from_stores()
    with _connect() as connection:
        rows = connection.execute(
            'SELECT * FROM transactions ORDER BY date DESC'
        ).fetchall()
    return _rows_to_frame(rows)


def _rows_to_frame(rows) -> pd.DataFrame:
    """Turn ledger rows into the transaction frame the rest of the app expects."""
    if not rows:
        return pd.DataFrame(columns=FRAME_COLUMNS)

    frame = pd.DataFrame([dict(row) for row in rows])
    frame = frame.rename(columns=_COLUMN_MAP)
    frame['Date'] = pd.to_datetime(frame['Date'], errors='coerce')
    frame['Ledger ID'] = frame['id']

    for column in FRAME_COLUMNS:
        if column not in frame.columns:
            frame[column] = None
    # Counterparty and account digits must stay text; '1607' read back as 1607.0
    # breaks every counterparty lookup in classify.
    for column in ('Ref ID', 'Counterparty', 'Account Last4', 'Transaction ID'):
        frame[column] = frame[column].apply(_clean_text).replace('', None)

    return frame[FRAME_COLUMNS + ['Ledger ID']].reset_index(drop=True)


def coverage() -> dict:
    """
    What the ledger actually holds, for labelling totals honestly.

    A "Total Deposited" with no stated window is the bug this whole module
    exists to fix, so every figure derived from the ledger should be able to say
    what it covers.
    """
    with _connect() as connection:
        row = connection.execute(
            'SELECT COUNT(*) AS rows, MIN(date) AS start, MAX(date) AS end FROM transactions'
        ).fetchone()
        by_source = connection.execute(
            'SELECT source, COUNT(*) AS n FROM transactions GROUP BY source'
        ).fetchall()

    counts = {r['source']: r['n'] for r in by_source}
    return {
        'rows': row['rows'] or 0,
        'start': row['start'],
        'end': row['end'],
        'by_source': counts,
        'manual_rows': counts.get(SOURCE_MANUAL, 0) + counts.get(SOURCE_CSV, 0),
    }


# ── Writing ───────────────────────────────────────────────────────────────────

def merge(new_rows, source: str = SOURCE_ETRADE) -> pd.DataFrame:
    """
    Union a freshly fetched frame into the ledger and return everything held.

    Idempotent: re-merging the same fetch inserts nothing, because the unique
    dedup key rejects it. Rows already in the ledger but missing from ``new_rows``
    are untouched — that asymmetry *is* the fix. A narrower fetch window must
    never be able to delete history.

    Seeding runs first and must: it only fires on an empty table, so a refresh
    that inserted before seeding would leave the ledger permanently non-empty
    and the pre-window rows in the old JSON stores would never be picked up.
    """
    seed_from_stores()
    inserted = _insert(new_rows, source=source)
    if inserted:
        LOGGER.info('ledger: %d new row(s) from %s', inserted, source)
    return load(seed=False)


def add_manual(rows, source: str = SOURCE_MANUAL) -> int:
    """
    Add hand-entered or imported rows. Returns how many were actually stored.

    A row identical to one E*TRADE already reported collides on the composite
    dedup key and is skipped rather than doubling the money — which is the whole
    risk of manual backfill.
    """
    if source not in (SOURCE_MANUAL, SOURCE_CSV):
        raise ValueError(f'source must be {SOURCE_MANUAL!r} or {SOURCE_CSV!r}, got {source!r}')
    return _insert(rows, source=source)


def _insert(rows, source: str) -> int:
    """Insert rows, skipping any whose dedup key is already present."""
    records = _to_records(rows)
    if not records:
        return 0

    now = datetime.datetime.now().isoformat(timespec='seconds')
    payload = []
    for row in records:
        payload.append((
            dedup_key(row),
            _clean_text(row.get('Transaction ID')) or None,
            _iso_date(row.get('Date')),
            _clean_text(row.get('Security Name')) or None,
            _clean_text(row.get('Symbol')) or None,
            _as_float(row.get('Quantity')),
            _as_float(row.get('Price')),
            _as_float(row.get('Total Value')),
            _clean_text(row.get('Transaction Type')) or None,
            _clean_text(row.get('Category')) or None,
            _clean_text(row.get('Account')) or None,
            _clean_text(row.get('Account ID Key')) or None,
            _clean_text(row.get('Account Last4')) or None,
            _clean_text(row.get('Ref ID')) or None,
            _clean_text(row.get('Counterparty')) or None,
            row.get('Source') or source,
            _clean_text(row.get('Note')) or None,
            now,
        ))

    # No bare except around the write: a save that fails silently is how data
    # vanished on restart before, so a failure here must reach the caller.
    inserted = 0
    with _connect() as connection:
        for values, row in zip(payload, records):
            # A row carrying a transaction id may already be here under an
            # id-less key, from the JSON stores or a hand-entered backfill. Claim
            # that row rather than adding a second copy of the same money.
            if values[1]:
                if _adopt_legacy(connection, row, values[1]):
                    continue
            elif _already_present(connection, row):
                continue
            before = connection.total_changes
            connection.execute(
                """
                INSERT OR IGNORE INTO transactions (
                    dedup_key, txn_id, date, security_name, symbol, quantity, price,
                    total_value, txn_type, category, account_name, account_id_key,
                    account_last4, ref_id, counterparty, source, note, created_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                values,
            )
            inserted += connection.total_changes - before
        connection.commit()
    return inserted


#: How far a hand-entered date may sit from the broker's posting date and still
#: describe the same event. Deposit notes record the day money was *sent*;
#: E*TRADE records the day it posted, which is often the next business day.
_DATE_SLACK_DAYS = 3

#: Amounts are compared with a tolerance rather than as a rounded key. The same
#: transaction can differ in the last cent between sources — a statement records
#: the settled amount while the API recomputes quantity x price — and a value
#: that round-tripped through JSON can differ from a freshly fetched one in the
#: final float bit, which is enough to make two identical numbers round apart.
_AMOUNT_SLACK = 0.011


def _economic_twin(connection, account, date, symbol, quantity, amount, id_less_only: bool):
    """
    An existing row describing the same event, or None.

    Matching is deliberately tolerant on date and amount but strict on account,
    symbol and quantity. Those three make a trade unmistakable; date and amount
    are exactly the fields that legitimately drift between a statement, a
    hand-typed note and the API.

    **Zero-dollar rows are never matched.** An in-kind security transfer and an
    IRA tax-year marker both post $0.00 with no symbol and no quantity, so they
    are indistinguishable on these fields — six of them can share one date and
    account. What separates them is the description, which the composite dedup
    key already covers. They move no money, so a duplicate cannot distort a
    total, whereas collapsing them loses the record of what happened.
    """
    if abs(amount or 0.0) < 0.005:
        return None

    candidates = connection.execute(
        f"""
        SELECT id, total_value, quantity, symbol, txn_id FROM transactions
        WHERE account_name IS ?
          AND date BETWEEN date(?, ?) AND date(?, ?)
          {'AND txn_id IS NULL' if id_less_only else ''}
        """,
        (account or None, date, f'-{_DATE_SLACK_DAYS} days',
         date, f'+{_DATE_SLACK_DAYS} days'),
    ).fetchall()

    for candidate in candidates:
        if _clean_text(candidate['symbol']) != _clean_text(symbol):
            continue
        theirs = candidate['quantity']
        if (quantity is None) != (theirs is None):
            continue
        if quantity is not None and abs(abs(quantity) - abs(theirs)) > 1e-6:
            continue
        if abs(abs(amount or 0.0) - abs(candidate['total_value'] or 0.0)) > _AMOUNT_SLACK:
            continue
        return candidate
    return None


def _row_identity(row: dict) -> tuple:
    """The fields :func:`_economic_twin` compares, pulled off an incoming row."""
    return (
        _clean_text(row.get('Account')),
        _iso_date(row.get('Date')),
        _clean_text(row.get('Symbol')),
        _as_float(row.get('Quantity')),
        _as_float(row.get('Total Value')) or 0.0,
    )


def _adopt_legacy(connection, row: dict, txn_id: str) -> bool:
    """
    Claim an existing id-less row for the same event, so it is not duplicated.

    Returns True when the caller must **not** insert — either because this
    transaction id is already stored, or because an id-less row describing the
    same event has just been updated to carry it.

    Only id-less rows are claimed. Two genuinely distinct transactions can look
    economically identical — the same stock bought twice in a day — and E*TRADE
    distinguishes them by id, so a row that already has one is left alone.
    """
    if connection.execute(
        'SELECT 1 FROM transactions WHERE txn_id = ?', (txn_id,)
    ).fetchone():
        return True     # already here under its own id; nothing to claim

    twin = _economic_twin(connection, *_row_identity(row), id_less_only=True)
    if twin is None:
        return False

    # Take the broker's id, but leave `source` alone: a hand-entered row stays
    # visibly hand-entered even once E*TRADE starts serving the same event.
    connection.execute(
        'UPDATE transactions SET txn_id = ?, dedup_key = ? WHERE id = ?',
        (txn_id, f'etrade:{txn_id}', twin['id']),
    )
    return True


def _already_present(connection, row: dict) -> bool:
    """
    Whether an id-less incoming row is already accounted for.

    The mirror of :func:`_adopt_legacy`. A hand-entered or imported row has no
    id to match on, so it is compared economically against everything already
    stored — including rows that *do* carry an id. Without this, backfilling a
    period E*TRADE later starts serving would count the same money twice.

    When two entries genuinely are indistinguishable this errs towards not
    adding the second. Under-adding is visible — the backfill page reports what
    it skipped — whereas double-counted money is not.
    """
    return _economic_twin(connection, *_row_identity(row), id_less_only=False) is not None


def _to_records(rows) -> list:
    """Accept a DataFrame or a list of dicts; drop rows with no usable date."""
    if isinstance(rows, pd.DataFrame):
        if rows.empty:
            return []
        records = rows.to_dict('records')
    else:
        records = list(rows or [])
    return [r for r in records if _iso_date(r.get('Date'))]


def _as_float(value):
    """Numeric or None — never NaN, which SQLite would store as a real NaN."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return None if pd.isna(number) else number


def reconcile_duplicates(dry_run: bool = True) -> dict:
    """
    Collapse id-less rows that a later fetch has since supplied with an id.

    Repairs ledgers written before :func:`_adopt_legacy` existed, when a seeded
    row and its freshly fetched twin keyed differently and both survived. Safe to
    re-run: once a pair is collapsed there is nothing left to match.

    Rows with **no** fresh twin are kept, and that is the important half. A
    deposit that has already fallen out of E*TRADE's two-year window exists only
    as an id-less row, and deleting it would undo the very thing this module was
    built to prevent.

    Args:
        dry_run: Report what would be removed without touching anything.

    Returns:
        ``{'removed': n, 'kept': n, 'rows': [...]}`` — ``rows`` describes each
        id-less row and whether a twin was found, so the caller can show its
        working before committing.
    """
    with _connect() as connection:
        legacy = connection.execute(
            'SELECT * FROM transactions WHERE txn_id IS NULL ORDER BY date'
        ).fetchall()

        removed, kept, report = 0, 0, []
        for row in legacy:
            twin = _find_fresh_twin(connection, row)
            entry = {
                'id': row['id'], 'date': row['date'], 'account': row['account_name'],
                'type': row['txn_type'], 'amount': row['total_value'],
                'symbol': row['symbol'], 'source': row['source'],
                'twin': twin['id'] if twin else None,
            }
            report.append(entry)
            if twin is None:
                kept += 1
                continue
            removed += 1
            if not dry_run:
                connection.execute('DELETE FROM transactions WHERE id = ?', (row['id'],))
        if not dry_run:
            connection.commit()

    return {'removed': removed, 'kept': kept, 'rows': report}


def _find_fresh_twin(connection, row):
    """The id-carrying row describing the same event as ``row``, if any."""
    candidates = connection.execute(
        """
        SELECT id, total_value, quantity, symbol FROM transactions
        WHERE txn_id IS NOT NULL
          AND account_name IS ?
          AND date BETWEEN date(?, ?) AND date(?, ?)
        """,
        (row['account_name'], row['date'], f'-{_DATE_SLACK_DAYS} days',
         row['date'], f'+{_DATE_SLACK_DAYS} days'),
    ).fetchall()

    quantity = row['quantity']
    amount = row['total_value'] or 0.0
    for candidate in candidates:
        if _clean_text(candidate['symbol']) != _clean_text(row['symbol']):
            continue
        if (quantity is None) != (candidate['quantity'] is None):
            continue
        if quantity is not None and abs(abs(quantity) - abs(candidate['quantity'])) > 1e-6:
            continue
        if abs(abs(amount) - abs(candidate['total_value'] or 0.0)) > _AMOUNT_SLACK:
            continue
        return candidate
    return None




def update_row(ledger_id: int, **fields) -> None:
    """
    Edit one hand-entered row. Broker rows are immutable.

    Letting a correction rewrite what E*TRADE reported would leave the ledger
    disagreeing with the source with no record of who changed it.
    """
    reverse = {v: k for k, v in _COLUMN_MAP.items()}
    updates = {reverse[k]: v for k, v in fields.items() if k in reverse}
    if not updates:
        return

    assignments = ', '.join(f'{column} = ?' for column in updates)
    with _connect() as connection:
        row = connection.execute(
            'SELECT source FROM transactions WHERE id = ?', (ledger_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f'no ledger row with id {ledger_id}')
        if row['source'] == SOURCE_ETRADE:
            raise ValueError('E*TRADE rows are immutable; only manual rows can be edited')
        connection.execute(
            f'UPDATE transactions SET {assignments} WHERE id = ?',
            (*updates.values(), ledger_id),
        )
        connection.commit()


def delete_row(ledger_id: int) -> None:
    """Remove one hand-entered row. Broker rows cannot be deleted."""
    with _connect() as connection:
        row = connection.execute(
            'SELECT source FROM transactions WHERE id = ?', (ledger_id,)
        ).fetchone()
        if row is None:
            return
        if row['source'] == SOURCE_ETRADE:
            raise ValueError('E*TRADE rows are immutable; only manual rows can be deleted')
        connection.execute('DELETE FROM transactions WHERE id = ?', (ledger_id,))
        connection.commit()


# ── Account identity ──────────────────────────────────────────────────────────

def remember_accounts(accounts: list) -> None:
    """
    Record ``account_id_key -> (last 4, display name)``.

    The ``Account`` column is ``accountName`` as it read at fetch time, and these
    accounts have been renamed more than once. In a frame rebuilt every refresh
    that is harmless. In a ledger that accumulates it is not: rows written before
    a rename keep the old label while new rows get the new one, so one account
    silently splits into two and every per-account total is wrong.

    Storing the opaque key alongside each row keeps identity stable, and keeping
    the map here means a closed account stays resolvable after E*TRADE drops it
    from the accounts list.
    """
    if not accounts:
        return
    now = datetime.datetime.now().isoformat(timespec='seconds')
    with _connect() as connection:
        connection.executemany(
            """
            INSERT INTO accounts (account_id_key, account_last4, account_name, updated_at)
            VALUES (?,?,?,?)
            ON CONFLICT(account_id_key) DO UPDATE SET
                account_last4 = COALESCE(excluded.account_last4, account_last4),
                account_name  = COALESCE(excluded.account_name, account_name),
                updated_at    = excluded.updated_at
            """,
            [
                (
                    _clean_text(a.get('account_id_key')),
                    _clean_text(a.get('account_id'))[-4:] or None,
                    _clean_text(a.get('account_name')) or None,
                    now,
                )
                for a in accounts if _clean_text(a.get('account_id_key'))
            ],
        )
        connection.commit()


def known_accounts() -> dict:
    """``{account_id_key: {'last4', 'name'}}`` for every account ever seen."""
    with _connect() as connection:
        rows = connection.execute('SELECT * FROM accounts').fetchall()
    return {
        r['account_id_key']: {'last4': r['account_last4'], 'name': r['account_name']}
        for r in rows
    }


# ── Metadata ──────────────────────────────────────────────────────────────────

def get_meta(key: str, default=None):
    """Read one metadata value."""
    with _connect() as connection:
        row = connection.execute('SELECT value FROM meta WHERE key = ?', (key,)).fetchone()
    if row is None:
        return default
    try:
        return json.loads(row['value'])
    except (json.JSONDecodeError, TypeError):
        return row['value']


def set_meta(key: str, value) -> None:
    """Write one metadata value."""
    with _connect() as connection:
        connection.execute(
            'INSERT INTO meta (key, value) VALUES (?,?) '
            'ON CONFLICT(key) DO UPDATE SET value = excluded.value',
            (key, json.dumps(value)),
        )
        connection.commit()


# ── Seeding ───────────────────────────────────────────────────────────────────

def seed_from_stores() -> int:
    """
    Populate an empty ledger from the JSON stores written before it existed.

    Runs once, and only when the table is empty. ``data/portfolio_cache.json``
    and ``data/month_end_snapshot.json`` between them still hold the June and
    July 2024 rows that E*TRADE has already stopped serving; if a refresh merged
    before this ran, those rows would be gone for good.
    """
    with _connect() as connection:
        already = connection.execute('SELECT COUNT(*) AS n FROM transactions').fetchone()['n']
    if already:
        return 0

    from portfolio.storage import cache, snapshot

    total = 0
    for label, loader in (('cache', cache.load_portfolio), ('snapshot', snapshot.load)):
        try:
            portfolio = loader()
        except Exception as exc:
            LOGGER.warning('ledger seed: could not read %s (%s)', label, exc)
            continue
        if not portfolio:
            continue
        transactions = portfolio.get('transactions')
        if transactions is None or (hasattr(transactions, 'empty') and transactions.empty):
            continue
        added = _insert(transactions, source=SOURCE_ETRADE)
        total += added
        LOGGER.info('ledger seed: %d row(s) from %s', added, label)

    if total:
        set_meta('seeded_at', datetime.datetime.now().isoformat(timespec='seconds'))
    return total
