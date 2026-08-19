"""
The two dialect differences between the local file and Postgres.

Neither test connects to anything. They pin the translation itself, because the
failure mode is a query that raises against one backend and silently means
something different against the other.
"""

import pytest

from portfolio.storage import db


@pytest.fixture
def remote(monkeypatch):
    """Pretend a connection string is configured, without contacting anything."""
    monkeypatch.setattr(db, 'dsn', lambda: 'postgresql://user:pw@host:5432/postgres')
    yield


def test_placeholders_are_left_alone_locally():
    assert db.q('SELECT 1 FROM t WHERE id = ?') == 'SELECT 1 FROM t WHERE id = ?'


def test_placeholders_become_percent_s_remotely(remote):
    assert db.q('SELECT 1 FROM t WHERE id = ?') == 'SELECT 1 FROM t WHERE id = %s'


def test_null_safe_equality_is_rewritten_remotely(remote):
    """
    SQLite overloads ``IS`` as null-safe equality; Postgres rejects ``IS $1``
    outright and spells it ``IS NOT DISTINCT FROM``. Plain ``=`` is not a
    substitute — it yields NULL when either side is, so rows with no account
    would quietly stop matching and a transfer would look unpaired.
    """
    assert db.q('WHERE account_name IS ?') == 'WHERE account_name IS NOT DISTINCT FROM %s'


def test_is_null_is_not_mangled(remote):
    assert db.q('WHERE txn_id IS NULL') == 'WHERE txn_id IS NULL'
    assert db.q('WHERE txn_id IS NOT NULL') == 'WHERE txn_id IS NOT NULL'


def test_schema_uses_the_right_identity_column(remote):
    assert 'GENERATED ALWAYS AS IDENTITY' in db.schema_sql()
    assert 'DOUBLE PRECISION' in db.schema_sql()


def test_schema_falls_back_to_sqlite_types():
    sql = db.schema_sql()
    assert 'INTEGER PRIMARY KEY' in sql
    assert 'DOUBLE PRECISION' not in sql, 'SQLite spells it REAL'
    assert 'REAL' in sql


def test_no_credentials_means_local():
    assert db.is_remote() is False
    assert db.dsn() is None
