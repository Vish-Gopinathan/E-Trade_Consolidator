"""
Suite-wide safety rails.

The ledger can be backed by Postgres, and ``portfolio/etrade.py`` calls
``load_dotenv()`` at import time — so merely importing the logic layer puts a
real ``SUPABASE_DB_URL`` into the environment. Without the guard below, a test
run wrote six fixture rows into the live database. They were deleted, but the
suite should not have been able to do it at all.

Tests are hermetic: they touch temporary files and nothing else.
"""

import pytest

from portfolio.storage import db


@pytest.fixture(autouse=True)
def never_touch_a_real_database(monkeypatch):
    """
    Force local SQLite for every test in the suite.

    Autouse and unconditional. A test that wants to exercise the Postgres path
    must opt in explicitly against a throwaway database of its own — the default
    can never be the connection string sitting in the developer's ``.env``.
    """
    monkeypatch.setattr(db, 'dsn', lambda: None)
    db.reset_cache()
    yield
    db.reset_cache()
