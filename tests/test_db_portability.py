"""
The ledger must behave identically on SQLite and Postgres.

Every bug this file guards against was found in production rather than here.
The costly one: ``ON CONFLICT DO UPDATE SET x = COALESCE(excluded.x, x)`` is
accepted by SQLite and rejected by Postgres as ambiguous, so recording the
account map raised mid-refresh. The whole fetch aborted, no new transactions
were stored, and the dashboard sat at the previous day's numbers looking merely
stale rather than broken.

The offline half needs no credentials and always runs.

The live half runs only when ``LEDGER_TEST_DSN`` is set, and deliberately *not*
on ``SUPABASE_DB_URL``. ``tests/conftest.py`` forces every test onto local
SQLite because a run once wrote fixture rows into the live database, so a test
naming the production variable would either be neutered by that guard — passing
against SQLite while claiming to prove Postgres behaviour, which is worse than
not running — or would have to defeat a safety rail that exists for good reason.
A separate variable is opt-in by construction:

    LEDGER_TEST_DSN="postgresql://…" pytest tests/test_db_portability.py

Point it at a scratch database. Rows here are tagged with a unique marker and
removed afterwards, but the suite should not assume that is enough.
"""

import ast
import os
import pathlib
import uuid

import pandas as pd
import pytest

from portfolio.storage import db, ledger

REPO = pathlib.Path(__file__).resolve().parent.parent


# ── Static: no dialect-specific SQL can reach the database ────────────────────

def _parameterised_statements():
    """Every execute() call in portfolio/ whose SQL carries a ? placeholder."""
    for path in sorted((REPO / 'portfolio').rglob('*.py')):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, 'attr', None)
            if name not in ('execute', 'executemany') or not node.args:
                continue
            arg = node.args[0]
            # db.executemany(connection, sql, params) puts the SQL second.
            if (name == 'executemany' and len(node.args) > 1
                    and not isinstance(arg, (ast.Constant, ast.JoinedStr))):
                arg = node.args[1]
            wrapped = isinstance(arg, ast.Call) and getattr(arg.func, 'attr', '') == 'q'
            sql = None
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                sql = arg.value
            elif isinstance(arg, ast.JoinedStr):
                sql = ''.join(v.value for v in arg.values if isinstance(v, ast.Constant))
            if sql and '?' in sql:
                yield path.relative_to(REPO), node.lineno, ' '.join(sql.split())[:70], wrapped


def test_every_parameterised_statement_is_translated():
    """
    ``?`` is SQLite's placeholder; Postgres wants ``%s``. Statements are written
    once in SQLite's style and passed through :func:`db.q`, so one that skips it
    works locally and fails only against the remote database.
    """
    unwrapped = [(p, line, sql) for p, line, sql, wrapped in _parameterised_statements()
                 if not wrapped]
    assert not unwrapped, 'SQL not passed through db.q(): ' + '; '.join(
        f'{p}:{line} {sql}' for p, line, sql in unwrapped)


def test_no_sqlite_only_syntax_remains():
    """Constructs Postgres rejects outright, which local tests would never catch."""
    banned = {
        'INSERT OR IGNORE': 'use ON CONFLICT ... DO NOTHING',
        'INSERT OR REPLACE': 'use ON CONFLICT ... DO UPDATE',
        'total_changes': 'use cursor.rowcount',
        'AUTOINCREMENT': 'declared per backend in db.schema_sql()',
        'julianday(': 'do date arithmetic in Python',
    }
    offenders = []
    for path in sorted((REPO / 'portfolio').rglob('*.py')):
        if path.name == 'db.py':          # the one place dialects may be named
            continue
        for number, line in enumerate(path.read_text().splitlines(), 1):
            if line.lstrip().startswith('#'):
                continue
            for token, advice in banned.items():
                if token in line:
                    offenders.append(f'{path.relative_to(REPO)}:{number} {token} — {advice}')
    assert not offenders, 'SQLite-only syntax: ' + '; '.join(offenders)


def test_upsert_columns_are_table_qualified():
    """
    The account-map bug, pinned. Inside ``DO UPDATE SET``, a bare column name is
    ambiguous to Postgres between the target row and ``excluded``. SQLite
    resolves it silently, so this only ever failed against the real database.
    """
    for path in sorted((REPO / 'portfolio').rglob('*.py')):
        text = path.read_text()
        for chunk in text.split('DO UPDATE SET')[1:]:
            body = chunk.split(')')[0]
            for line in body.splitlines():
                if '=' not in line or 'excluded.' not in line:
                    continue
                right = line.split('=', 1)[1]
                for token in ('COALESCE(', 'coalesce('):
                    if token in right:
                        inner = right.split(token, 1)[1]
                        assert '.' in inner.split(',')[-1], (
                            f'{path.name}: unqualified column in DO UPDATE SET — '
                            f'Postgres reads it as ambiguous: {line.strip()}'
                        )


# ── Live: the same assertions against the real backend ───────────────────────

TEST_DSN_VAR = 'LEDGER_TEST_DSN'

pytestmark_live = pytest.mark.skipif(
    not os.getenv(TEST_DSN_VAR),
    reason=f'{TEST_DSN_VAR} not set — Postgres behaviour not exercised',
)


@pytest.fixture
def remote_scratch(monkeypatch):
    """
    Rows tagged with a unique marker, removed afterwards.

    Deliberately shares the real tables rather than creating throwaway ones:
    the point is to exercise the schema that production actually uses, and a
    parallel copy could drift from it without anything noticing.
    """
    # Override the suite-wide guard, which points every test at local SQLite.
    # Only tests that opted in through TEST_DSN_VAR get here.
    monkeypatch.setattr(db, 'dsn', lambda: os.environ[TEST_DSN_VAR])
    db.reset_cache()
    ledger._schema_ready.clear()
    assert db.is_remote(), 'live test is not talking to Postgres'

    marker = f'test-{uuid.uuid4().hex[:12]}'
    yield marker
    with ledger._connect() as connection:
        connection.execute(
            db.q('DELETE FROM transactions WHERE account_name = ?'), (marker,))
        connection.execute(
            db.q('DELETE FROM accounts WHERE account_id_key = ?'), (marker,))


@pytestmark_live
def test_remote_accepts_the_account_upsert(remote_scratch):
    """The exact statement that aborted a refresh, run twice to force DO UPDATE."""
    ledger.remember_accounts([
        {'account_id_key': remote_scratch, 'account_id': '99990000', 'account_name': 'First'},
    ])
    ledger.remember_accounts([
        {'account_id_key': remote_scratch, 'account_id': '99990000', 'account_name': 'Second'},
    ])
    stored = ledger.known_accounts()[remote_scratch]
    assert stored == {'last4': '0000', 'name': 'Second'}


@pytestmark_live
def test_remote_dedup_and_row_counts(remote_scratch):
    """
    Inserting reports how many rows actually landed, on this backend too.

    ``cursor.rowcount`` replaced SQLite's ``total_changes``; if it disagreed,
    add_manual would misreport how much backfill was stored — a wrong number
    presented as fact, which is the failure this project exists to avoid.
    """
    row = {
        'Date': '2024-07-04', 'Total Value': 4321.0, 'Account': remote_scratch,
        'Transaction Type': 'Transfer', 'Category': 'Deposit',
        'Security Name': 'ACH DEPOSIT REFID:1', 'Transaction ID': f'{remote_scratch}-1',
    }
    assert ledger.merge(pd.DataFrame([row])) is not None
    with ledger._connect() as connection:
        count = connection.execute(
            db.q('SELECT COUNT(*) AS n FROM transactions WHERE account_name = ?'),
            (remote_scratch,),
        ).fetchone()['n']
    assert count == 1

    ledger.merge(pd.DataFrame([row]))          # same fetch again
    assert ledger.add_manual([{**row, 'Transaction ID': None}]) == 0

    with ledger._connect() as connection:
        count = connection.execute(
            db.q('SELECT COUNT(*) AS n FROM transactions WHERE account_name = ?'),
            (remote_scratch,),
        ).fetchone()['n']
    assert count == 1, 'a re-merge or a manual twin must not add a second copy'


@pytestmark_live
def test_remote_and_local_schemas_agree(remote_scratch):
    """Both backends must define the same columns, or a round trip loses fields."""
    expected = set(ledger._COLUMN_MAP) | {'id', 'dedup_key', 'created_at'}
    with ledger._connect() as connection:
        row = connection.execute('SELECT * FROM transactions LIMIT 1').fetchone()
    assert row is not None, 'remote ledger is empty — nothing to compare'
    assert expected <= set(dict(row)), (
        f'missing on the remote: {expected - set(dict(row))}'
    )


def test_every_column_written_exists_in_the_schema():
    """
    The stores and the DDL must agree.

    ``holdings_store`` builds its INSERT from a column map, so a field added
    there but not to :data:`db.SCHEMA` raises only against a database created
    from that schema — which is every fresh deployment, and never the developer's
    machine where the table already has the column.
    """
    from portfolio.storage import holdings_store

    schema = db.SCHEMA.lower()
    for table, columns in (
        ('holdings', set(holdings_store._COLUMNS.values())),
        ('transactions', set(ledger._COLUMN_MAP)),
    ):
        body = schema.split(f'create table if not exists {table} (', 1)[1].split(');', 1)[0]
        declared = {line.strip().split()[0] for line in body.splitlines() if line.strip()}
        missing = {c for c in columns if c.lower() not in declared}
        assert not missing, f'{table}: written but not declared in db.SCHEMA — {missing}'
