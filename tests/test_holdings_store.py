"""
The durable copy of today's positions.

Snapshot semantics, unlike the ledger: holdings describe *today*, so the table
is replaced whole. Only transactions accumulate, because only transactions
record events that happened once and later fall out of the broker's window.
"""

import pandas as pd
import pytest

from portfolio.storage import holdings_store, ledger


@pytest.fixture(autouse=True)
def temporary_store(tmp_path, monkeypatch):
    monkeypatch.setattr(ledger, 'DB_PATH', tmp_path / 'ledger.db')
    monkeypatch.setattr(ledger, '_schema_ready', set())
    yield


def _holdings():
    return pd.DataFrame([
        {'Symbol': 'AAA', 'Symbol Description': 'Alpha', 'Current Price': 20.0,
         'Quantity': 100.0, 'Date Acquired': pd.Timestamp('2024-01-02'),
         'Price Paid': 10.0, 'Total Cost': 1000.0, 'Market Value': 2000.0,
         'Total Gain': 1000.0, 'Total Gain %': 100.0, 'Percent of Portfolio': 60.0},
        {'Symbol': 'CASH', 'Symbol Description': 'Cash', 'Current Price': 1.0,
         'Quantity': 500.0, 'Date Acquired': pd.NaT, 'Price Paid': 1.0,
         'Total Cost': 500.0, 'Market Value': 500.0, 'Total Gain': 0.0,
         'Total Gain %': 0.0, 'Percent of Portfolio': 40.0},
    ])


def test_a_round_trip_is_lossless():
    original = _holdings()
    assert holdings_store.save(original, fetched_at='2026-08-14T10:00:00') == 2

    restored = holdings_store.load()['holdings']
    assert list(restored.columns) == list(original.columns), 'column order must survive'
    pd.testing.assert_frame_equal(
        restored.set_index('Symbol').sort_index(),
        original.set_index('Symbol').sort_index(),
        check_dtype=False,
    )


def test_saving_replaces_rather_than_accumulates():
    """Holdings describe today. Two saves must not leave a position listed twice."""
    holdings_store.save(_holdings())
    holdings_store.save(_holdings().head(1))
    assert holdings_store.load()['holdings']['Symbol'].tolist() == ['AAA']


def test_a_sold_position_disappears():
    holdings_store.save(_holdings())
    remaining = _holdings()
    remaining = remaining[remaining['Symbol'] != 'AAA']
    holdings_store.save(remaining)
    assert 'AAA' not in holdings_store.load()['holdings']['Symbol'].tolist()


def test_balances_and_reported_total_travel_with_the_positions():
    balances = [{'account_id_key': 'abc', 'total_account_value': 100.0, 'net_cash': 5.0}]
    holdings_store.save(_holdings(), fetched_at='2026-08-14T10:00:00',
                        reported_total=2500.0, account_balances=balances)
    stored = holdings_store.load()
    assert stored['reported_total'] == 2500.0
    assert stored['account_balances'] == balances
    assert stored['fetched_at'] == '2026-08-14T10:00:00'


def test_a_missing_date_survives_as_null():
    """NaT is neither a float nor a Timestamp, and reached the driver unguarded."""
    holdings_store.save(_holdings())
    restored = holdings_store.load()['holdings']
    assert pd.isna(restored.set_index('Symbol').at['CASH', 'Date Acquired'])


def test_an_empty_store_reads_as_none():
    assert holdings_store.load() is None


def test_saving_nothing_is_a_no_op():
    assert holdings_store.save(pd.DataFrame()) == 0
    assert holdings_store.load() is None
