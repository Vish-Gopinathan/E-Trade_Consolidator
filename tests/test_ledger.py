"""
The accumulating transaction ledger.

The test that matters most is
:func:`test_a_narrower_fetch_window_does_not_erase_history` — it is the whole
reason this module exists. E*TRADE's two-year window slides forward, so each
refresh returns *less* old history than the last, and the store it replaced
overwrote wholesale. Deposits fell with no withdrawal behind them.
"""

import pandas as pd
import pytest

from portfolio.storage import ledger

#: Captured before the autouse fixture stubs it out, so the two tests that
#: actually exercise seeding can put the real implementation back.
_REAL_SEED = ledger.seed_from_stores


@pytest.fixture(autouse=True)
def temporary_ledger(tmp_path, monkeypatch):
    """
    A throwaway database, and no seeding.

    Seeding reads ``data/portfolio_cache.json`` — the real account. Left enabled
    it pulls hundreds of live rows into every test, and a test suite has no
    business reading someone's actual holdings. :func:`test_seeding_imports_the_
    old_json_stores` covers that path with fixtures of its own.
    """
    monkeypatch.setattr(ledger, 'DB_PATH', tmp_path / 'ledger.db')
    monkeypatch.setattr(ledger, 'seed_from_stores', lambda: 0)
    yield


def _row(date, amount, txn_id=None, account='Brokerage', **extra):
    row = {
        'Date': date, 'Security Name': 'ACH DEPOSIT REFID:109902862906;',
        'Symbol': None, 'Quantity': None, 'Price': None, 'Total Value': amount,
        'Transaction Type': 'Transfer', 'Category': 'Deposit', 'Account': account,
        'Ref ID': '109902862906', 'Counterparty': None, 'Transaction ID': txn_id,
    }
    row.update(extra)
    return row


# ── The regression this module exists for ─────────────────────────────────────

def test_a_narrower_fetch_window_does_not_erase_history():
    ledger.merge(pd.DataFrame([
        _row('2024-06-14', 1750.0, txn_id='1'),
        _row('2025-01-03', 3500.0, txn_id='2'),
    ]))

    # The next refresh happens after the two-year window has slid past June 2024,
    # so that row simply is not in the response any more.
    combined = ledger.merge(pd.DataFrame([_row('2025-01-03', 3500.0, txn_id='2')]))

    assert len(combined) == 2, 'a row missing from a later fetch must survive'
    assert ledger.coverage()['start'] == '2024-06-14'


def test_re_merging_the_same_fetch_changes_nothing():
    frame = pd.DataFrame([_row('2024-06-14', 1750.0, txn_id='1')])
    ledger.merge(frame)
    ledger.merge(frame)
    assert ledger.coverage()['rows'] == 1


# ── The other way it lost: counting the same money twice ──────────────────────
#
# dedup_key returns ``etrade:{id}`` when a transaction id is present and a
# composite otherwise, so the two forms never collide. A row seeded from the old
# JSON stores keyed on the composite; the same transaction fetched later keyed on
# its id. Nothing stopped both landing, and the first real refresh after the
# ledger shipped roughly doubled the reported deposits.


def test_a_fetched_row_claims_its_id_less_twin():
    ledger.merge(pd.DataFrame([_row('2024-06-14', 1750.0, txn_id=None)]))
    ledger.merge(pd.DataFrame([_row('2024-06-14', 1750.0, txn_id='555')]))

    frame = ledger.load()
    assert len(frame) == 1, 'the same deposit must not be stored twice'
    assert frame['Transaction ID'].iloc[0] == '555', 'the broker id should be adopted'


def test_a_cent_of_drift_still_counts_as_the_same_event():
    """
    A statement records the settled amount while the API recomputes quantity x
    price, and a value that round-tripped through JSON can differ from a freshly
    fetched one in the last float bit — enough to round two identical numbers
    apart. Exact keys cannot survive that; the match is tolerant by design.
    """
    ledger.merge(pd.DataFrame([_row(
        '2024-06-07', -26.78, txn_id=None,
        **{'Symbol': 'TLRY', 'Quantity': 15.0, 'Transaction Type': 'Bought'},
    )]))
    ledger.merge(pd.DataFrame([_row(
        '2024-06-07', 26.775, txn_id='777',
        **{'Symbol': 'TLRY', 'Quantity': 15.0, 'Transaction Type': 'Bought'},
    )]))
    assert ledger.coverage()['rows'] == 1


def test_a_day_of_drift_still_counts_as_the_same_event():
    """Deposit notes record the day money was sent; E*TRADE records when it posted."""
    ledger.add_manual([_row('2025-10-30', 5000.0, account='Brokerage')])
    ledger.merge(pd.DataFrame([_row('2025-10-29', 5000.0, txn_id='888', account='Brokerage')]))
    assert ledger.coverage()['rows'] == 1


def test_a_hand_entered_row_cannot_duplicate_a_row_that_has_an_id():
    """
    The mirror case. Once E*TRADE starts serving a period you backfilled, a
    hand-entered row has no id to collide on, so it must be matched economically
    or the same money is counted twice.
    """
    ledger.merge(pd.DataFrame([_row('2025-03-17', 5000.0, txn_id='999')]))
    assert ledger.add_manual([_row('2025-03-17', 5000.0)]) == 0
    assert ledger.coverage()['rows'] == 1


def test_zero_dollar_rows_are_never_matched_economically():
    """
    An in-kind transfer and a tax-year marker both post $0.00 with no symbol and
    no quantity, so they are indistinguishable on the matched fields — six can
    share one date and account. They move no money, so a duplicate cannot
    distort a total, but collapsing them loses the record of what happened.
    """
    rows = [
        _row('2024-11-05', 0.0, txn_id=f'{i}',
             **{'Security Name': f'{sym} TFR TO ACCT XXXXX-2222-0'})
        for i, sym in enumerate(['CRWD', 'NVDA', 'LULU', 'SCHD', 'VPU', 'SLYG'])
    ]
    ledger.merge(pd.DataFrame(rows))
    assert ledger.coverage()['rows'] == 6


def test_two_real_trades_on_one_day_are_not_collapsed():
    """Economically identical but distinct — the broker's ids say so."""
    buy = {'Symbol': 'AAA', 'Quantity': 10.0, 'Transaction Type': 'Bought'}
    ledger.merge(pd.DataFrame([
        _row('2025-03-17', 1000.0, txn_id='a', **buy),
        _row('2025-03-17', 1000.0, txn_id='b', **buy),
    ]))
    assert ledger.coverage()['rows'] == 2


# ── Repairing a ledger written before the fix ─────────────────────────────────

def test_reconcile_collapses_pairs_and_keeps_orphans():
    ledger.merge(pd.DataFrame([
        _row('2024-06-14', 1750.0, txn_id=None),      # has a twin below
        _row('2024-08-19', 15000.0, txn_id=None),     # window slid past it: keep
    ]))
    # Write the twin straight to the table. Going through _insert would exercise
    # the fix and adopt the id-less row, which is the opposite of the pre-fix
    # state this test needs to start from.
    with ledger._connect() as connection:
        connection.execute(
            'INSERT INTO transactions (dedup_key, txn_id, date, total_value, '
            'account_name, source, created_at) VALUES (?,?,?,?,?,?,?)',
            ('etrade:555', '555', '2024-06-14', 1750.0, 'Brokerage',
             ledger.SOURCE_ETRADE, '2026-01-01T00:00:00'),
        )
        connection.commit()
    assert ledger.coverage()['rows'] == 3

    preview = ledger.reconcile_duplicates(dry_run=True)
    assert (preview['removed'], preview['kept']) == (1, 1)
    assert ledger.coverage()['rows'] == 3, 'a dry run must not change anything'

    ledger.reconcile_duplicates(dry_run=False)
    assert ledger.coverage()['rows'] == 2
    remaining = ledger.load()
    assert 15000.0 in remaining['Total Value'].tolist(), 'the orphan must survive'

    # Re-running finds nothing left to do.
    assert ledger.reconcile_duplicates(dry_run=False)['removed'] == 0


# ── Dedup identity ────────────────────────────────────────────────────────────

def test_rows_without_a_transaction_id_dedupe_on_their_contents():
    """
    Rows seeded from the old JSON cache have no ``Transaction ID``. Without the
    composite fallback they would land again on the next fetch, doubling money
    that only moved once.
    """
    ledger.merge(pd.DataFrame([_row('2024-06-14', 1750.0, txn_id=None)]))
    ledger.merge(pd.DataFrame([_row('2024-06-14', 1750.0, txn_id=None)]))
    assert ledger.coverage()['rows'] == 1


def test_the_same_amount_on_different_days_is_two_rows():
    ledger.merge(pd.DataFrame([
        _row('2024-06-14', 1750.0), _row('2024-07-24', 1750.0),
    ]))
    assert ledger.coverage()['rows'] == 2


def test_the_same_amount_in_different_accounts_is_two_rows():
    ledger.merge(pd.DataFrame([
        _row('2024-06-14', 1750.0, account='Brokerage'),
        _row('2024-06-14', 1750.0, account='Roth IRA'),
    ]))
    assert ledger.coverage()['rows'] == 2


def test_a_transaction_id_wins_over_the_composite_key():
    """Two economically identical rows are distinct when the broker says so."""
    ledger.merge(pd.DataFrame([
        _row('2024-06-14', 1750.0, txn_id='1'),
        _row('2024-06-14', 1750.0, txn_id='2'),
    ]))
    assert ledger.coverage()['rows'] == 2


# ── Manual rows ───────────────────────────────────────────────────────────────

def test_a_manual_row_does_not_double_count_against_a_broker_row():
    ledger.merge(pd.DataFrame([_row('2024-06-14', 1750.0, txn_id=None)]))
    added = ledger.add_manual([_row('2024-06-14', 1750.0, txn_id=None)])
    assert added == 0, 'a hand-entered duplicate must collide, not double the money'
    assert ledger.coverage()['rows'] == 1


def test_manual_rows_are_counted_separately():
    ledger.merge(pd.DataFrame([_row('2024-06-14', 1750.0, txn_id='1')]))
    ledger.add_manual([_row('2024-05-21', 1000.0, account=ledger.UNATTRIBUTED)])
    coverage = ledger.coverage()
    assert coverage['manual_rows'] == 1
    assert coverage['by_source'] == {'etrade': 1, 'manual': 1}
    assert coverage['start'] == '2024-05-21'


def test_broker_rows_cannot_be_edited_or_deleted():
    ledger.merge(pd.DataFrame([_row('2024-06-14', 1750.0, txn_id='1')]))
    frame = ledger.load()
    row_id = int(frame['Ledger ID'].iloc[0])

    with pytest.raises(ValueError):
        ledger.update_row(row_id, **{'Total Value': 9999.0})
    with pytest.raises(ValueError):
        ledger.delete_row(row_id)


def test_a_manual_row_can_be_corrected_and_removed():
    ledger.add_manual([_row('2024-05-21', 1000.0, account=ledger.UNATTRIBUTED)])
    row_id = int(ledger.load()['Ledger ID'].iloc[0])

    ledger.update_row(row_id, **{'Total Value': 1200.0})
    assert float(ledger.load()['Total Value'].iloc[0]) == 1200.0

    ledger.delete_row(row_id)
    assert ledger.coverage()['rows'] == 0


def test_add_manual_rejects_an_etrade_source():
    with pytest.raises(ValueError):
        ledger.add_manual([_row('2024-05-21', 1000.0)], source='etrade')


# ── Shape and typing ──────────────────────────────────────────────────────────

def test_account_digits_survive_the_round_trip_as_text():
    """``'1607'`` read back as ``1607.0`` breaks every counterparty lookup."""
    ledger.merge(pd.DataFrame([_row(
        '2024-11-07', 3500.0, txn_id='1',
        **{'Counterparty': '1344', 'Ref ID': '0121333885906'},
    )]))
    frame = ledger.load()
    assert frame['Counterparty'].iloc[0] == '1344'
    assert frame['Ref ID'].iloc[0] == '0121333885906'


def test_rows_with_no_usable_date_are_dropped():
    assert ledger.add_manual([_row(None, 1000.0), _row('2024-05-21', 1000.0)]) == 1


def test_an_empty_ledger_reports_empty_coverage():
    coverage = ledger.coverage()
    assert coverage['rows'] == 0
    assert coverage['start'] is None
    assert ledger.load().empty


def test_seeding_imports_the_old_json_stores(tmp_path, monkeypatch):
    """
    The rows already aged out of E*TRADE's window survive only in the JSON
    stores, so an empty ledger must pick them up before anything else runs.
    """
    from portfolio.storage import cache, snapshot

    monkeypatch.setattr(ledger, 'DB_PATH', tmp_path / 'seeded.db')
    monkeypatch.setattr(ledger, 'seed_from_stores', _REAL_SEED)
    monkeypatch.setattr(cache, 'load_portfolio', lambda: {
        'transactions': pd.DataFrame([_row('2024-06-14', 1750.0, txn_id='1')])
    })
    monkeypatch.setattr(snapshot, 'load', lambda: {
        'transactions': pd.DataFrame([
            _row('2024-06-14', 1750.0, txn_id='1'),      # already seeded from cache
            _row('2024-05-21', 1000.0, txn_id='9'),      # only in the snapshot
        ])
    })

    assert _REAL_SEED() == 2
    assert ledger.coverage()['start'] == '2024-05-21'
    # Runs once: a second call must not re-import onto a populated table.
    assert _REAL_SEED() == 0


def test_seeding_survives_an_unreadable_store(tmp_path, monkeypatch):
    """A broken cache must not stop the ledger existing."""
    from portfolio.storage import cache, snapshot

    def explode():
        raise OSError('cache is corrupt')

    monkeypatch.setattr(ledger, 'DB_PATH', tmp_path / 'partial.db')
    monkeypatch.setattr(ledger, 'seed_from_stores', _REAL_SEED)
    monkeypatch.setattr(cache, 'load_portfolio', explode)
    monkeypatch.setattr(snapshot, 'load', lambda: {
        'transactions': pd.DataFrame([_row('2024-05-21', 1000.0, txn_id='9')])
    })

    assert _REAL_SEED() == 1


def test_accounts_are_remembered_by_key_not_by_name():
    """
    Renaming an account must not fragment it. The display name is refreshed;
    the opaque key is the identity.
    """
    ledger.remember_accounts([
        {'account_id_key': 'abc', 'account_id': '83291344', 'account_name': 'Old Name'}
    ])
    ledger.remember_accounts([
        {'account_id_key': 'abc', 'account_id': '83291344', 'account_name': 'Brokerage'}
    ])
    known = ledger.known_accounts()
    assert list(known) == ['abc']
    assert known['abc'] == {'last4': '1344', 'name': 'Brokerage'}
