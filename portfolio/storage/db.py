"""
Where the ledger lives: local SQLite, or Postgres when one is configured.

The ledger began as a local SQLite file because the repository is public and
CLAUDE.md forbade remote persistence. That rule was written about pushing
portfolio data *into the repo* through the Contents API, which bypasses
``.gitignore`` and would have made holdings world-readable. A credentialed
database is a different risk, and the rule now reads: nothing world-readable,
connection string in secrets only.

The move is needed because a hosted container gets a fresh filesystem on every
restart. Transactions accumulate and cannot be refetched once E*TRADE's two-year
window slides past them, so a store that dies with the container is not a store.

**No credentials configured means local SQLite, exactly as before.** The test
suite must run offline, and it does.

Only two dialect differences survive down here:

* the parameter placeholder — ``?`` against SQLite, ``%s`` against Postgres;
* the table definitions, which differ only in how an id column is declared.

Everything else is written once. ``ON CONFLICT ... DO NOTHING`` is supported by
both, and date arithmetic is done in Python and passed as a parameter rather
than expressed in SQL, which removed the last dialect-specific expression.
"""

import contextlib
import logging
import os

LOGGER = logging.getLogger(__name__)

#: Connection string for the remote store. Absent means local SQLite.
ENV_VAR = 'SUPABASE_DB_URL'


_UNRESOLVED = object()
_dsn_cache = _UNRESOLVED


def dsn() -> str | None:
    """
    The configured connection string, or None for local SQLite.

    Reads Streamlit secrets when there is a runtime — that is how a deployment
    supplies it — and falls back to the environment. The import is inside the
    function so this module keeps working with no Streamlit installed, which is
    the documented exception to the logic layer not importing it.

    Resolved once per process. :func:`q` consults this for every statement, and
    the secrets lookup is expensive enough that leaving it uncached added tens
    of seconds to a test run.
    """
    global _dsn_cache
    if _dsn_cache is _UNRESOLVED:
        value = os.getenv(ENV_VAR)
        if not value:
            try:
                import streamlit as st
                value = st.secrets.get(ENV_VAR)
            except Exception:
                value = None
        _dsn_cache = (value or '').strip() or None
    return _dsn_cache


def reset_cache() -> None:
    """Forget the resolved connection string. For tests and credential changes."""
    global _dsn_cache
    _dsn_cache = _UNRESOLVED


def is_remote() -> bool:
    """Whether the ledger is talking to Postgres rather than a local file."""
    return dsn() is not None


def q(sql: str) -> str:
    """
    Translate a statement written in SQLite's dialect for the active backend.

    Two substitutions, and deliberately no more — anything needing a third
    should be restructured until it does not, as the date arithmetic was.

    * ``?`` becomes ``%s``.
    * ``IS ?`` becomes ``IS NOT DISTINCT FROM %s``. SQLite overloads ``IS`` as
      null-safe equality, so ``account_name IS ?`` matches rows where both sides
      are NULL. Postgres reserves ``IS`` for ``IS NULL`` and friends and rejects
      ``IS $1`` outright; ``IS NOT DISTINCT FROM`` is its spelling of the same
      idea. Plain ``=`` would not do: it returns NULL when either side is, so
      rows with no account would silently stop matching.
    """
    if not is_remote():
        return sql
    return sql.replace('IS ?', 'IS NOT DISTINCT FROM ?').replace('?', '%s')


SCHEMA = """
CREATE TABLE IF NOT EXISTS transactions (
    id              {identity},
    dedup_key       TEXT    NOT NULL UNIQUE,
    txn_id          TEXT,
    date            TEXT    NOT NULL,
    security_name   TEXT,
    symbol          TEXT,
    quantity        DOUBLE PRECISION,
    price           DOUBLE PRECISION,
    total_value     DOUBLE PRECISION,
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
CREATE TABLE IF NOT EXISTS holdings (
    symbol               TEXT PRIMARY KEY,
    description          TEXT,
    quantity             DOUBLE PRECISION,
    price_paid           DOUBLE PRECISION,
    current_price        DOUBLE PRECISION,
    total_cost           DOUBLE PRECISION,
    market_value         DOUBLE PRECISION,
    total_gain           DOUBLE PRECISION,
    total_gain_pct       DOUBLE PRECISION,
    percent_of_portfolio DOUBLE PRECISION,
    date_acquired        TEXT,
    fetched_at           TEXT
);
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""

#: SQLite uses REAL rather than DOUBLE PRECISION, and rowid-backed integer keys.
_SQLITE_IDENTITY = 'INTEGER PRIMARY KEY'
_POSTGRES_IDENTITY = 'INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY'


def schema_sql() -> str:
    """The table definitions for the active backend."""
    identity = _POSTGRES_IDENTITY if is_remote() else _SQLITE_IDENTITY
    sql = SCHEMA.format(identity=identity)
    return sql if is_remote() else sql.replace('DOUBLE PRECISION', 'REAL')


@contextlib.contextmanager
def connect(db_path=None):
    """
    A connection that commits on success and rolls back on error.

    Uniform on purpose. ``sqlite3``'s own context manager commits but leaves the
    connection open, while psycopg's commits *and* closes — relying on either
    would behave differently depending on where the data happens to live, which
    is exactly the kind of difference that shows up as a missing row later.

    Rows come back addressable by column name from both backends.
    """
    if is_remote():
        import psycopg
        from psycopg.rows import dict_row
        connection = psycopg.connect(dsn(), row_factory=dict_row, connect_timeout=15)
    else:
        import sqlite3
        connection = sqlite3.connect(db_path)
        connection.row_factory = sqlite3.Row

    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def executemany(connection, sql: str, params) -> None:
    """
    Run one statement over many parameter sets, on either backend.

    ``sqlite3`` exposes ``executemany`` on the connection; psycopg only on a
    cursor. Going through a cursor works for both, so callers do not have to
    know which store they are talking to.
    """
    cursor = connection.cursor()
    cursor.executemany(sql, params)


def ensure_schema(db_path=None) -> None:
    """Create the tables if they are not there yet."""
    with connect(db_path) as connection:
        if is_remote():
            with connection.cursor() as cursor:
                cursor.execute(schema_sql())
        else:
            connection.executescript(schema_sql())
