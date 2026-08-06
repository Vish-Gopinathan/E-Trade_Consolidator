"""
Transaction classification tests.

Fixtures are real E*TRADE descriptions from a live account with the account digits
changed. They are kept verbatim otherwise, because the failures this code has had
all came from the exact wording: ``Online Transfer`` missing from the type
vocabulary, ``surprisePercent`` scaling, ``TFR TO ACCT`` rows carrying $0.
"""

import pandas as pd
import pytest

from portfolio import classify


def make_frame(rows):
    """Build a transaction frame and run pass 1 over it, as the fetcher does."""
    frame = pd.DataFrame(rows)
    frame['Ref ID'] = frame['Security Name'].map(classify.parse_ref_id)
    frame['Counterparty'] = frame['Security Name'].map(classify.parse_counterparty)
    frame['Category'] = [
        classify.classify_row(row['Transaction Type'], row['Security Name'], row['Total Value'])
        for _, row in frame.iterrows()
    ]
    return frame


def row(date, t_type, description, amount, account='Brokerage'):
    return {
        'Date': pd.Timestamp(date), 'Transaction Type': t_type,
        'Security Name': description, 'Total Value': amount, 'Account': account,
    }


# ── Description parsing ───────────────────────────────────────────────────────

@pytest.mark.parametrize('description, expected', [
    ('ACH DEPOSIT REFID:22406294395;', '22406294395'),
    ('TRANSFER TO XXXXX7449 REFID:23390594395', '23390594395'),
    ('NVDA TFR TO ACCT XXXXX-7449-0 REFID:22966254368881', '22966254368881'),
    ('BROKERAGE DEPOSIT', None),
    ('', None),
])
def test_parse_ref_id(description, expected):
    assert classify.parse_ref_id(description) == expected


@pytest.mark.parametrize('description, expected', [
    ('TRANSFER TO XXXXX1607 REFID:128218358906', '1607'),
    ('TRANSFER FROM XXXXX1344 REFID:23390594395', '1344'),
    ('ACH DEPOSIT REFID:22406294395;', None),
])
def test_parse_counterparty(description, expected):
    assert classify.parse_counterparty(description) == expected


# ── Pass 1 ────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize('t_type, description, amount, expected', [
    # Bank movements are settled on sight, whatever the transaction type says.
    ('Online Transfer', 'ACH DEPOSIT REFID:22406294395;', 5000.0, classify.DEPOSIT),
    ('Transfer', 'ACH DEPOSIT REFID:107835613906;', 1000.0, classify.DEPOSIT),
    ('Electronic Funds Transfer', 'EFT WITHDRAWAL', -2000.0, classify.WITHDRAWAL),

    # Income never becomes a cash flow, however it is described.
    ('Dividend', 'APPLE INC', 118.0, classify.INCOME),
    ('Qualified Dividend', 'NVIDIA CORPORATION REC 06/26/26', 4.4, classify.INCOME),
    ('Interest Income', 'MORGAN STANLEY PRIVATE BANK NA (Period 07/01-07/31)', 12.3,
     classify.INCOME),

    # IRA contributions and distributions are directional by definition.
    ('Contribution', 'TY 2026 $ 7500 INDIVID CONTRIB - CURR YR', 7500.0, classify.DEPOSIT),
    ('Distribution', 'IRA DISTRIBUTION', -1000.0, classify.WITHDRAWAL),

    # In-kind security moves: no cash changes hands, so never a cash flow.
    ('Transfer', 'NVDA TFR TO ACCT XXXXX-7449-0 REFID:22966254368881', 0.0, classify.INTERNAL),
    ('Transfer', 'SHOP TFR FROM ACCT XXXXX-7449-0 REFID:22964057896881', 0.0, classify.INTERNAL),

    # A named counterparty needs the other accounts to resolve.
    ('Online Transfer', 'TRANSFER TO XXXXX1607 REFID:128218358906', -500.0,
     classify.PENDING_TRANSFER),

    # Bookkeeping and genuinely unknown rows.
    ('Journal', 'ADJUSTMENT', 0.0, classify.INTERNAL),
    ('Stock Split', 'VANGUARD GROWTH ETF SPLIT RATIO 6:1', 0.0, classify.OTHER),
])
def test_classify_row(t_type, description, amount, expected):
    assert classify.classify_row(t_type, description, amount) == expected


def test_online_transfer_is_in_the_vocabulary():
    """
    Regression: ``Online Transfer`` was in no type list, so every transfer fell
    through to Other and was dropped — withdrawals totalled $0 against a real
    $4,775.
    """
    assert 'Online Transfer' in classify.TRANSFER_TYPES


# ── Pass 2 ────────────────────────────────────────────────────────────────────

def test_paired_legs_become_internal():
    """Two legs sharing a REFID and netting to zero are one inter-account move."""
    frame = make_frame([
        row('2026-06-05', 'Online Transfer', 'TRANSFER TO XXXXX7449 REFID:23390594395',
            -3753.0, account='Individual'),
        row('2026-06-05', 'Online Transfer', 'TRANSFER FROM XXXXX1344 REFID:23390594395',
            3753.0, account='Roth IRA'),
    ])
    result = classify.reconcile_transfers(frame)

    assert set(result['Category']) == {classify.INTERNAL}
    assert not result['Needs Review'].any()
    assert 'REFID 23390594395' in result['Classification Note'].iloc[0]
    assert classify.get_cash_flows(result).empty


def test_unmatched_transfer_counts_as_external_and_is_flagged():
    """
    An unmatched leg is external by default: if the far side were one of your
    accounts, its matching leg would be here. Flagged so the UI can ask.
    """
    frame = make_frame([
        row('2025-01-27', 'Online Transfer', 'TRANSFER TO XXXXX1607 REFID:128218358906', -500.0),
    ])
    result = classify.reconcile_transfers(frame, own_accounts=['1344', '7449'])

    assert result['Category'].iloc[0] == classify.WITHDRAWAL
    assert bool(result['Needs Review'].iloc[0])
    assert '1607' in result['Classification Note'].iloc[0]


def test_own_account_number_marks_a_transfer_internal():
    """A single leg still resolves when the counterparty is a known account."""
    frame = make_frame([
        row('2025-03-17', 'Online Transfer', 'TRANSFER TO XXXXX1344 REFID:132627026906', -111.38),
    ])
    result = classify.reconcile_transfers(frame, own_accounts=['XXXXX1344', 'XXXXX7449'])

    assert result['Category'].iloc[0] == classify.INTERNAL
    assert not result['Needs Review'].iloc[0]


def test_account_map_overrides_the_default():
    """A user decision beats the external-by-default fallback."""
    frame = make_frame([
        row('2025-01-27', 'Online Transfer', 'TRANSFER TO XXXXX1607 REFID:128218358906', -500.0),
    ])
    result = classify.reconcile_transfers(frame, account_map={'1607': 'internal'})

    assert result['Category'].iloc[0] == classify.INTERNAL
    assert not result['Needs Review'].iloc[0]


def test_near_zero_pair_is_not_treated_as_matched():
    """Two legs that do not cancel are two separate movements, not one transfer."""
    frame = make_frame([
        row('2025-01-27', 'Online Transfer', 'TRANSFER TO XXXXX1607 REFID:111', -500.0),
        row('2025-01-27', 'Online Transfer', 'TRANSFER FROM XXXXX1607 REFID:111', 250.0),
    ])
    result = classify.reconcile_transfers(frame)
    assert classify.INTERNAL not in set(result['Category'])


def test_no_pending_category_survives_reconciliation():
    """PENDING_TRANSFER is an internal state and must never reach the UI."""
    frame = make_frame([
        row('2026-06-05', 'Online Transfer', 'TRANSFER TO XXXXX7449 REFID:1', -100.0),
        row('2026-06-05', 'Online Transfer', 'TRANSFER FROM XXXXX1344 REFID:1', 100.0),
        row('2025-01-27', 'Online Transfer', 'TRANSFER TO XXXXX1607 REFID:2', -500.0),
    ])
    result = classify.reconcile_transfers(frame, own_accounts=['1344'])
    assert classify.PENDING_TRANSFER not in set(result['Category'])


# ── End-to-end shape, mirroring the real account ──────────────────────────────

def test_realistic_history_totals():
    """
    The scenario the reported bug came from: ACH deposits, a paired inter-account
    move, an unmatched transfer out, an in-kind security move, and dividends.
    """
    frame = make_frame([
        row('2026-05-28', 'Online Transfer', 'ACH DEPOSIT REFID:22406294395;', 5000.0),
        row('2026-05-01', 'Online Transfer', 'ACH DEPOSIT REFID:19855674395;', 10000.0),
        row('2026-06-05', 'Online Transfer', 'TRANSFER TO XXXXX7449 REFID:23390594395', -3753.0),
        row('2026-06-05', 'Online Transfer', 'TRANSFER FROM XXXXX1344 REFID:23390594395', 3753.0),
        row('2024-11-07', 'Transfer', 'TRANSFER TO XXXXX1607 REFID:121333885906', -3500.0),
        row('2025-01-30', 'Transfer', 'IBIT TFR TO ACCT XXXXX-7449-0 REFID:229704', 0.0),
        row('2026-06-26', 'Qualified Dividend', 'NVIDIA CORPORATION', 4.4),
    ])
    result = classify.reconcile_transfers(frame, own_accounts=['1344', '7449'])
    counts = result['Category'].value_counts()

    assert counts[classify.DEPOSIT] == 2
    assert counts[classify.WITHDRAWAL] == 1      # the unmatched 1607 transfer
    assert counts[classify.INTERNAL] == 3        # paired legs + the in-kind move
    assert counts[classify.INCOME] == 1

    flows = classify.get_cash_flows(result)
    assert flows['Total Value'].sum() == pytest.approx(11500.0)

    pending = classify.unresolved_counterparties(result)
    assert list(pending['Counterparty']) == ['1607']
    assert pending['Net Amount'].iloc[0] == pytest.approx(-3500.0)


def test_get_cash_flows_and_income_on_empty_input():
    assert classify.get_cash_flows(pd.DataFrame()).empty
    assert classify.get_income(pd.DataFrame()).empty
    assert classify.reconcile_transfers(pd.DataFrame()).empty
