"""
Reading an E*TRADE transaction CSV.

The export format is not stable — column names differ between account types and
export dates, and some downloads carry title lines above the header. So the
parser recognises headers rather than trusting positions, and an unusable file
comes back as an empty frame plus an explanation rather than an exception.
"""

import io

import pytest

from portfolio import importer


def _csv(text: str):
    return io.StringIO(text.strip() + '\n')


def test_a_standard_export_parses():
    frame, problems = importer.read_csv(_csv("""
TransactionDate,TransactionType,SecurityType,Symbol,Quantity,Amount,Price,Description
08/19/2024,Transfer,CASH,,,15000.00,,ACH DEPOSIT REFID:115053551906
02/14/2020,Bought,EQ,AAPL,300,-45900.00,153.00,APPLE INC
"""))
    assert not problems
    assert len(frame) == 2
    assert frame['Total Value'].tolist() == [15000.0, -45900.0]
    assert frame['Symbol'].tolist() == [None, 'AAPL']


def test_title_lines_above_the_header_are_skipped():
    """Exports often lead with an account banner before the real header row."""
    frame, _ = importer.read_csv(_csv("""
For Account,####-0000
Generated,08/14/2026

TransactionDate,TransactionType,Symbol,Quantity,Amount
08/19/2024,Transfer,,,15000.00
"""))
    assert len(frame) == 1
    assert frame['Total Value'].iloc[0] == 15000.0


def test_currency_formatting_and_parenthesised_negatives_are_read():
    frame, _ = importer.read_csv(_csv("""
Date,Type,Amount
08/19/2024,Transfer,"$15,000.00"
09/20/2024,Transfer,"($1,250.50)"
"""))
    assert frame['Total Value'].tolist() == [15000.0, -1250.5]


def test_sells_are_stored_with_a_negative_quantity():
    """
    The realised-P&L walk reads direction from the sign of the quantity, and
    exports are not consistent about it, so the type is authoritative.
    """
    frame, _ = importer.read_csv(_csv("""
Date,TransactionType,Symbol,Quantity,Amount
04/15/2018,Sold,IBM,800,116000.00
03/10/2011,Bought,IBM,800,-96000.00
"""))
    quantities = dict(zip(frame['Symbol'] + frame['Transaction Type'], frame['Quantity']))
    assert quantities['IBMSold'] == -800.0
    assert quantities['IBMBought'] == 800.0


def test_rows_with_no_readable_date_are_reported_not_dropped_silently():
    frame, problems = importer.read_csv(_csv("""
Date,Type,Amount
not-a-date,Transfer,100.00
08/19/2024,Transfer,15000.00
"""))
    assert len(frame) == 1
    assert any('date' in problem for problem in problems)


def test_rows_with_no_amount_are_reported():
    frame, problems = importer.read_csv(_csv("""
Date,Type,Amount
08/19/2024,Transfer,
09/20/2024,Transfer,250.00
"""))
    assert len(frame) == 1
    assert any('amount' in problem for problem in problems)


def test_a_file_without_the_required_columns_explains_itself():
    frame, problems = importer.read_csv(_csv("""
Ticker,Shares
AAPL,300
"""))
    assert frame.empty
    assert problems and 'Date' in problems[0]
    assert 'Ticker' in problems[0], 'the message should show what it did see'


def test_an_empty_file_does_not_raise():
    frame, problems = importer.read_csv(_csv('\n'))
    assert frame.empty
    assert problems


def test_bytes_input_is_accepted():
    """Streamlit's uploader hands over bytes, sometimes with a BOM."""
    frame, _ = importer.read_csv(
        io.BytesIO('﻿Date,Type,Amount\n08/19/2024,Transfer,15000.00\n'.encode('utf-8'))
    )
    assert len(frame) == 1


@pytest.mark.parametrize('text, expected', [
    ('$1,234.56', 1234.56), ('(1,234.56)', -1234.56), ('1234.56', 1234.56),
    ('', None), ('--', None), ('n/a', None), (None, None), (42, 42.0),
])
def test_number_parsing(text, expected):
    assert importer._to_number(text) == expected
