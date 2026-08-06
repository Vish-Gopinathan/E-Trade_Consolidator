"""
Transaction fetching: paging, history clamping, and request count.

These are the three things that made a refresh slow — and, in the paging case,
wrong. All are tested against a fake accounts object that records every call, so
the assertions are about *how many requests are made and what they ask for*, not
just the returned frame.
"""

import datetime

import pandas as pd
import pytest

from portfolio import etrade


class FakeAccounts:
    """
    A stand-in for pyetrade's ETradeAccounts that records every request.

    ``pages`` maps a window start date to the list of pages that window returns,
    so a test can hand back more than one page and assert the fetcher follows the
    marker to the end.
    """

    def __init__(self, pages=None, details=None):
        self.pages = pages or {}
        self.details = details or {}
        self.list_calls = []
        self.detail_calls = []

    def list_transactions(self, account_id_key, start_date=None, end_date=None,
                          sort_order='DESC', marker=None, count=50, resp_format='json'):
        self.list_calls.append((start_date, end_date, marker, count))
        window_pages = self.pages.get(start_date, [])
        index = int(marker) if marker else 0
        if index >= len(window_pages):
            return {'TransactionListResponse': {}}
        body = {'Transaction': window_pages[index]}
        if index + 1 < len(window_pages):
            body['marker'] = str(index + 1)
            body['moreTransactions'] = 'true'
        return {'TransactionListResponse': body}

    def list_transaction_details(self, account_id_key, transaction_id, resp_format='json'):
        self.detail_calls.append(transaction_id)
        return {'TransactionDetailsResponse': self.details.get(transaction_id, {})}


def txn(transaction_id, day, t_type='Bought', description='BOUGHT AAA',
        amount=-1000.0, brokerage=None):
    row = {
        'transactionId': transaction_id,
        'transactionDate': int(pd.Timestamp(day).timestamp() * 1000),
        'transactionType': t_type,
        'description': description,
        'amount': amount,
    }
    if brokerage is not None:
        row['brokerage'] = brokerage
    return row


TRADE_BROKERAGE = {'quantity': 100.0, 'price': 10.0, 'product': {'symbol': 'AAA'}}


# ── History clamping ──────────────────────────────────────────────────────────

def test_start_date_is_clamped_to_the_available_history():
    end = datetime.date(2026, 8, 5)
    clamped, was_clamped = etrade.clamp_start_date(datetime.date(2000, 1, 1), end)
    assert was_clamped
    assert clamped == end - datetime.timedelta(days=etrade.MAX_HISTORY_DAYS)


def test_a_recent_start_date_is_left_alone():
    end = datetime.date(2026, 8, 5)
    start = datetime.date(2026, 1, 1)
    clamped, was_clamped = etrade.clamp_start_date(start, end)
    assert clamped == start
    assert not was_clamped


def test_a_decade_request_does_not_become_a_hundred_round_trips():
    """
    The reported symptom. A 2000–2026 request used to issue 108 windows per
    account, 99 of which could only come back empty.
    """
    fake = FakeAccounts()
    etrade.get_consolidated_transactions(
        fake, 'KEY', datetime.date(2000, 1, 1), datetime.date(2026, 8, 5))

    expected = len(list(etrade._date_chunks(
        datetime.date(2026, 8, 5) - datetime.timedelta(days=etrade.MAX_HISTORY_DAYS),
        datetime.date(2026, 8, 5),
    )))
    assert len(fake.list_calls) == expected
    assert expected < 20, 'the clamped range should be a handful of windows, not 108'


def test_the_clamp_covers_history_this_account_has_actually_returned():
    """
    The live account holds transactions from 806 days before the fetch, past the
    documented two-year limit. Clamping to two years would have silently dropped
    six weeks of deposits — and deposits feed the return calculation.
    """
    assert etrade.MAX_HISTORY_DAYS > 806


# ── Paging ────────────────────────────────────────────────────────────────────

def test_every_page_of_a_busy_window_is_fetched():
    """
    Regression: the fetcher took the first 50-row page and discarded the rest, so
    a busy quarter silently lost transactions.
    """
    start = datetime.date(2026, 1, 1)
    window_start = list(etrade._date_chunks(start, datetime.date(2026, 3, 1)))[0][0]
    fake = FakeAccounts(pages={window_start: [
        [txn(f'p1-{i}', '2026-01-05', brokerage=TRADE_BROKERAGE) for i in range(50)],
        [txn(f'p2-{i}', '2026-01-06', brokerage=TRADE_BROKERAGE) for i in range(50)],
        [txn('p3-0', '2026-01-07', brokerage=TRADE_BROKERAGE)],
    ]})

    frame = etrade.get_consolidated_transactions(
        fake, 'KEY', start, datetime.date(2026, 3, 1))

    assert len(frame) == 101
    assert [call[2] for call in fake.list_calls[:3]] == [None, '1', '2']


def test_paging_stops_when_the_marker_does_not_advance():
    """A server repeating its marker must not spin forever."""
    class StuckAccounts(FakeAccounts):
        def list_transactions(self, *args, **kwargs):
            self.list_calls.append(kwargs.get('marker'))
            return {'TransactionListResponse': {
                'Transaction': [txn('x', '2026-01-05', brokerage=TRADE_BROKERAGE)],
                'marker': 'same', 'moreTransactions': 'true',
            }}

    fake = StuckAccounts()
    etrade.get_consolidated_transactions(
        fake, 'KEY', datetime.date(2026, 1, 1), datetime.date(2026, 1, 20))
    assert len(fake.list_calls) == 2      # first page, then the repeat, then stop


def test_a_single_unwrapped_transaction_is_handled():
    """The API returns a bare object rather than a list when there is only one."""
    class SingleAccounts(FakeAccounts):
        def list_transactions(self, *args, **kwargs):
            self.list_calls.append(kwargs.get('marker'))
            if len(self.list_calls) > 1:
                return {'TransactionListResponse': {}}
            return {'TransactionListResponse': {
                'Transaction': txn('solo', '2026-01-05', t_type='Dividend',
                                   description='ALPHA DIVIDEND', amount=25.0),
            }}

    fake = SingleAccounts()
    frame = etrade.get_consolidated_transactions(
        fake, 'KEY', datetime.date(2026, 1, 1), datetime.date(2026, 1, 20))
    assert len(frame) == 1
    assert frame['Category'].iloc[0] == 'Income'


# ── Trade details ─────────────────────────────────────────────────────────────

def test_no_detail_call_when_the_list_already_has_the_data():
    """
    The list response carries a brokerage block with quantity, price and symbol.
    Fetching each trade's detail anyway was 215 extra round trips on a real
    account — the single largest cost of a refresh.
    """
    start = datetime.date(2026, 1, 1)
    window_start = list(etrade._date_chunks(start, datetime.date(2026, 3, 1)))[0][0]
    fake = FakeAccounts(pages={window_start: [
        [txn('t1', '2026-01-05', brokerage=TRADE_BROKERAGE)],
    ]})

    frame = etrade.get_consolidated_transactions(
        fake, 'KEY', start, datetime.date(2026, 3, 1))

    assert fake.detail_calls == []
    row = frame.iloc[0]
    assert row['Symbol'] == 'AAA'
    assert row['Quantity'] == 100.0
    assert row['Price'] == 10.0
    assert row['Total Value'] == pytest.approx(1000.0)


def test_detail_call_is_the_fallback_when_the_list_omits_the_data():
    start = datetime.date(2026, 1, 1)
    window_start = list(etrade._date_chunks(start, datetime.date(2026, 3, 1)))[0][0]
    fake = FakeAccounts(
        pages={window_start: [[txn('t1', '2026-01-05')]]},   # no brokerage block
        details={'t1': {'Brokerage': TRADE_BROKERAGE}},
    )

    frame = etrade.get_consolidated_transactions(
        fake, 'KEY', start, datetime.date(2026, 3, 1))

    assert fake.detail_calls == ['t1']
    assert frame['Symbol'].iloc[0] == 'AAA'
    assert frame['Quantity'].iloc[0] == 100.0


def test_a_failed_detail_call_keeps_the_row():
    class FailingAccounts(FakeAccounts):
        def list_transaction_details(self, *args, **kwargs):
            raise RuntimeError('rate limited')

    start = datetime.date(2026, 1, 1)
    window_start = list(etrade._date_chunks(start, datetime.date(2026, 3, 1)))[0][0]
    fake = FailingAccounts(pages={window_start: [[txn('t1', '2026-01-05')]]})

    frame = etrade.get_consolidated_transactions(
        fake, 'KEY', start, datetime.date(2026, 3, 1))

    assert len(frame) == 1
    assert frame['Total Value'].iloc[0] == pytest.approx(-1000.0)   # falls back to amount


def test_a_failed_window_does_not_lose_the_other_windows():
    class FlakyAccounts(FakeAccounts):
        def list_transactions(self, account_id_key, start_date=None, end_date=None, **kwargs):
            self.list_calls.append(start_date)
            if len(self.list_calls) == 1:
                raise RuntimeError('transient 500')
            if len(self.list_calls) == 2:
                return {'TransactionListResponse': {
                    'Transaction': [txn('ok', '2026-04-05', brokerage=TRADE_BROKERAGE)]}}
            return {'TransactionListResponse': {}}

    fake = FlakyAccounts()
    frame = etrade.get_consolidated_transactions(
        fake, 'KEY', datetime.date(2026, 1, 1), datetime.date(2026, 6, 1))
    assert len(frame) == 1


# ── Progress ──────────────────────────────────────────────────────────────────

def test_progress_is_reported_once_per_window():
    fake = FakeAccounts()
    seen = []
    etrade.get_consolidated_transactions(
        fake, 'KEY', datetime.date(2026, 1, 1), datetime.date(2026, 8, 5),
        on_progress=lambda done, total: seen.append((done, total)),
    )
    assert len(seen) == len(fake.list_calls)
    assert seen[-1][0] == seen[-1][1]      # finishes at 100%
