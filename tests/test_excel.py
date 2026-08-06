"""
Excel export tests.

Both exporters write to a buffer, so a test can build a real workbook and read it
back without touching disk. The empty-frame cases matter: ``map(len).max()``
returns NaN on an empty frame and ``set_column`` rejects NaN, which aborted the
whole export rather than skipping one sheet.
"""

import io

import pandas as pd
import pytest

from portfolio import excel, schema
from portfolio.storage import cache


@pytest.fixture
def holdings():
    return pd.DataFrame([
        {'Symbol': 'AAA', 'Symbol Description': 'Alpha', 'Current Price': 20.0,
         'Quantity': 100.0, 'Date Acquired': pd.Timestamp('2024-01-02'), 'Price Paid': 10.0,
         'Total Cost': 1000.0, 'Market Value': 2000.0, 'Total Gain': 1000.0,
         'Total Gain %': 100.0, 'Percent of Portfolio': 62.5},
        {'Symbol': 'CASH', 'Symbol Description': 'Cash', 'Current Price': 1.0,
         'Quantity': 1200.0, 'Date Acquired': pd.NaT, 'Price Paid': 1.0,
         'Total Cost': 1200.0, 'Market Value': 1200.0, 'Total Gain': 0.0,
         'Total Gain %': 0.0, 'Percent of Portfolio': 37.5},
    ])


@pytest.fixture
def transactions():
    return pd.DataFrame([
        {'Date': pd.Timestamp('2024-01-02'), 'Security Name': 'ACH DEPOSIT', 'Symbol': '',
         'Quantity': None, 'Price': None, 'Total Value': 2000.0,
         'Transaction Type': 'Online Transfer', 'Category': 'Deposit'},
    ])


def sheets(buffer):
    buffer.seek(0)
    return pd.read_excel(buffer, sheet_name=None)


def test_holdings_workbook_has_every_sheet(holdings, transactions):
    cash_flows = pd.DataFrame([{
        'Date': pd.Timestamp('2024-01-02'), 'Description': 'ACH DEPOSIT',
        'Total Value': 2000.0, 'Category': 'Deposit',
    }])
    income = pd.DataFrame([{
        'Date': pd.Timestamp('2024-07-01'), 'Description': 'ALPHA DIVIDEND',
        'Total Value': 25.0, 'Transaction Type': 'Dividend',
    }])

    buffer = io.BytesIO()
    excel.export_to_excel(holdings, transactions, cash_flows, income, output=buffer)
    written = sheets(buffer)

    assert set(written) == {'Holdings', 'Transactions', 'Cash Flows', 'Income'}
    # Cash is summarised beneath the table, not listed as a holding.
    assert 'CASH' not in set(written['Holdings']['Symbol'].dropna())


def test_empty_holdings_do_not_abort_the_export():
    """Regression: autofit called set_column with NaN and raised."""
    empty = pd.DataFrame(columns=[
        'Symbol', 'Symbol Description', 'Quantity', 'Total Cost',
        'Market Value', 'Total Gain', 'Total Gain %',
    ])
    buffer = io.BytesIO()
    excel.export_to_excel(empty, output=buffer)
    assert 'Holdings' in sheets(buffer)


def test_optional_sheets_are_skipped_when_absent(holdings):
    buffer = io.BytesIO()
    excel.export_to_excel(holdings, None, pd.DataFrame(), None, output=buffer)
    assert set(sheets(buffer)) == {'Holdings'}


def test_cash_flow_sheet_survives_missing_columns(holdings):
    """A frame without Category still writes, without the colour coding."""
    partial = pd.DataFrame([{'Date': pd.Timestamp('2024-01-02'), 'Total Value': 2000.0}])
    buffer = io.BytesIO()
    excel.export_to_excel(holdings, cash_flows_df=partial, output=buffer)
    assert 'Cash Flows' in sheets(buffer)


def test_analytics_workbook_flattens_the_report(holdings):
    from portfolio import analytics

    report = analytics.PortfolioAnalytics(holdings).generate_full_report()
    buffer = io.BytesIO()
    excel.export_analytics_to_excel(holdings, report, output=buffer)
    written = sheets(buffer)

    assert set(written) == {'Analytics Summary', 'Holdings Detail'}
    labels = set(written['Analytics Summary'].iloc[:, 0].dropna().astype(str).str.strip())
    assert schema.PERFORMANCE in labels
    assert schema.TOTAL_RETURN_PCT in labels


def test_analytics_workbook_handles_an_empty_report(holdings):
    buffer = io.BytesIO()
    excel.export_analytics_to_excel(holdings, {}, output=buffer)
    assert 'Analytics Summary' in sheets(buffer)


# ── Cache round-trip ──────────────────────────────────────────────────────────

def test_cache_round_trip_preserves_types(tmp_path, monkeypatch, holdings, transactions):
    """
    numpy scalars must survive: np.int64 is not JSON-serialisable and used to make
    the cache write fail inside a bare `except: pass`, so data silently vanished
    on restart.
    """
    import numpy as np

    monkeypatch.setattr(cache, 'CACHE_FILE', tmp_path / 'portfolio_cache.json')
    portfolio = {
        'fetched_at': '2026-08-05T10:00:00',
        'holdings': holdings,
        'transactions': transactions,
        'cash_flows': pd.DataFrame(),
        'income': pd.DataFrame(),
        'summary': {'Total Stocks': np.int64(1), 'Cash': np.float64(1200.0),
                    'Missing': float('nan')},
        'analytics_report': {},
    }
    cache.save_portfolio(portfolio)
    loaded = cache.load_portfolio()

    assert loaded['summary']['Total Stocks'] == 1
    assert loaded['summary']['Missing'] is None
    assert len(loaded['holdings']) == len(holdings)
    assert pd.api.types.is_datetime64_any_dtype(loaded['holdings']['Date Acquired'])


def test_cache_keeps_account_digits_as_text(tmp_path, monkeypatch):
    """
    Counterparty '1607' must not become 1607.0 — every account lookup is a string
    comparison, so a numeric coercion silently breaks transfer classification.
    """
    monkeypatch.setattr(cache, 'CACHE_FILE', tmp_path / 'portfolio_cache.json')
    frame = pd.DataFrame([{
        'Date': pd.Timestamp('2025-01-27'), 'Security Name': 'TRANSFER TO XXXXX1607',
        'Total Value': -500.0, 'Category': 'Withdrawal',
        'Ref ID': '128218358906', 'Counterparty': '1607',
    }])
    cache.save_portfolio({'transactions': frame, 'holdings': pd.DataFrame()})
    loaded = cache.load_portfolio()

    assert loaded['transactions']['Counterparty'].iloc[0] == '1607'
    assert loaded['transactions']['Ref ID'].iloc[0] == '128218358906'


def test_legacy_cache_format_still_loads(tmp_path, monkeypatch):
    """Caches written before frames were tagged stored bare record lists."""
    import json

    path = tmp_path / 'portfolio_cache.json'
    monkeypatch.setattr(cache, 'CACHE_FILE', path)
    path.write_text(json.dumps({
        'fetched_at': '2026-08-05T10:00:00',
        'holdings': [{'Symbol': 'AAA', 'Market Value': 100.0}],
        'transactions': [],
    }))

    loaded = cache.load_portfolio()
    assert isinstance(loaded['holdings'], pd.DataFrame)
    assert loaded['holdings']['Symbol'].iloc[0] == 'AAA'
    assert isinstance(loaded['transactions'], pd.DataFrame)
