"""
Transaction classification: deciding what each non-trade row actually means.

Everything downstream depends on this. Deposit-adjusted return subtracts external
cash flows so contributions are not mistaken for gains; get one classification
wrong and the headline performance number is wrong with it.

The hard case is a transfer, because E*TRADE describes an inter-account move and a
real bank withdrawal almost identically::

    TRANSFER TO XXXXX1344 REFID:132627026906     -111.38   your other E*TRADE account
    TRANSFER FROM XXXXX7449 REFID:132627026906   +111.38   ... the matching leg
    TRANSFER TO XXXXX1607 REFID:128218358906     -500.00   money that actually left

No single row carries enough information to tell those apart, which is why
classification runs in **two passes**:

1. :func:`classify_row` looks at one row and decides everything it can. Rows that
   are unmistakable — ``ACH DEPOSIT``, ``Contribution``, ``Dividend`` — are settled
   here. Ambiguous transfers are parked as :data:`PENDING_TRANSFER`.
2. :func:`reconcile_transfers` runs once over **all accounts combined** and settles
   the parked rows, first by pairing legs that share a REFID and net to zero, then
   by checking the counterparty account number against your own accounts and the
   user-maintained account map.

The default for an unrecognised counterparty is **external**, not internal. If the
other side of a transfer were one of your accounts, its matching leg would be in
the data and the REFID pairing would have found it; money moving to an account we
never see has, as far as this portfolio is concerned, left. Those rows are flagged
``Needs Review`` so the UI can ask rather than quietly assume.
"""

import re

import pandas as pd

# ── Categories ────────────────────────────────────────────────────────────────
#
# Every transaction row carries exactly one of these in its ``Category`` column.

TRADE = 'Trade'              # security buy or sell
DEPOSIT = 'Deposit'          # external money entering the brokerage
WITHDRAWAL = 'Withdrawal'    # external money leaving the brokerage
INCOME = 'Income'            # dividends, interest, fees — generated inside the account
INTERNAL = 'Internal'        # movement between your own accounts; nets to zero overall
OTHER = 'Other'              # unrecognised; surfaced in the UI rather than guessed at
PENDING_TRANSFER = 'Transfer (unresolved)'  # pass-1 placeholder, never survives pass 2

#: Categories that represent real external cash movement. These, and only these,
#: feed deposit-adjusted return and the Cash Flows page totals.
EXTERNAL_CATEGORIES = (DEPOSIT, WITHDRAWAL)

# ── Transaction type vocabulary ───────────────────────────────────────────────

TRADE_TYPES = ['Bought', 'Sold']

#: Always income, whatever the description says. Money generated inside the
#: account is not an external contribution and must never inflate deposits.
INCOME_TYPES = [
    'Dividend', 'Qualified Dividend', 'Interest', 'Interest Income',
    'Misc Income', 'Fee', 'Refund',
]

#: Unambiguously external. Direction comes from the sign of the amount, except
#: for IRA contributions and distributions which are directional by definition.
EXTERNAL_CASH_TYPES = [
    'Electronic Funds Transfer', 'Check', 'Wire', 'ACH',
    'Contribution', 'Distribution',
]

#: Types that need pass 2. ``Online Transfer`` is the label E*TRADE actually uses
#: for both bank deposits and inter-account moves — omitting it was why every
#: transfer landed in ``Other`` and withdrawals totalled zero.
TRANSFER_TYPES = ['Transfer', 'Online Transfer', 'Journal']

#: Description fragments that prove an external bank movement regardless of type.
_EXTERNAL_KEYWORDS = (
    'ach deposit', 'ach withdrawal', 'electronic funds', 'wire',
    'deposit from bank', 'withdrawal to bank', 'external',
)

# ── Description parsing ───────────────────────────────────────────────────────

#: ``ACH DEPOSIT REFID:22406294395;`` -> ``22406294395``. Both legs of an
#: inter-account transfer share one REFID, which is what makes pairing possible.
_REF_ID_RE = re.compile(r'REFID:\s*(\d+)', re.I)

#: ``TRANSFER TO XXXXX1344 REFID:...`` -> ``1344``. E*TRADE masks all but the
#: last four digits of the counterparty account.
_COUNTERPARTY_RE = re.compile(r'\bTRANSFER\s+(?:TO|FROM)\s+X*(\d{4})\b', re.I)

#: ``NVDA TFR TO ACCT XXXXX-7449-0`` — an in-kind move of the *security* between
#: accounts. Always $0 in cash terms and never a cash flow.
_IN_KIND_RE = re.compile(r'\bTFR\s+(?:TO|FROM)\s+ACCT\b', re.I)


def parse_ref_id(description: str) -> str | None:
    """Return the REFID embedded in a description, or None."""
    match = _REF_ID_RE.search(description or '')
    return match.group(1) if match else None


def parse_counterparty(description: str) -> str | None:
    """Return the masked counterparty account's last 4 digits, or None."""
    match = _COUNTERPARTY_RE.search(description or '')
    return match.group(1) if match else None


# ── Pass 1: single-row classification ─────────────────────────────────────────

def classify_row(t_type: str, description: str, amount: float | None) -> str:
    """
    Classify one non-trade transaction from the row alone.

    Args:
        t_type: ``transactionType`` from the API, e.g. ``'Online Transfer'``.
        description: API description, which carries the REFID and counterparty.
        amount: Signed dollar amount. Positive is money into the account.

    Returns:
        One of the category constants. :data:`PENDING_TRANSFER` means the row
        cannot be settled without seeing the other accounts — pass it to
        :func:`reconcile_transfers`.
    """
    desc = (description or '').lower()
    combined = f'{(t_type or "").lower()} {desc}'

    # Income first and unconditionally. A dividend described as "TRANSFER" is
    # still a dividend, and letting a keyword override that would double-count it
    # as a contribution.
    if t_type in INCOME_TYPES:
        return INCOME

    # In-kind security moves between accounts: no cash changes hands.
    if _IN_KIND_RE.search(description or ''):
        return INTERNAL

    # An explicit bank-movement phrase settles the row whatever its type is.
    if any(keyword in combined for keyword in _EXTERNAL_KEYWORDS):
        return direction_by_amount(amount)

    # IRA contributions and distributions are directional by definition; E*TRADE
    # reports contributions with inconsistent signs across account types.
    if t_type == 'Contribution':
        return DEPOSIT
    if t_type == 'Distribution':
        return WITHDRAWAL

    if t_type in EXTERNAL_CASH_TYPES:
        return direction_by_amount(amount)

    if t_type in TRANSFER_TYPES:
        # A named counterparty is resolvable in pass 2.
        if parse_counterparty(description):
            return PENDING_TRANSFER
        # A journal entry with no counterparty and no external phrasing is
        # bookkeeping — E*TRADE's own corrections and re-postings.
        if (t_type or '').lower() == 'journal':
            return INTERNAL
        return OTHER

    return OTHER


def direction_by_amount(amount: float | None) -> str:
    """Deposit for money in, Withdrawal for money out, Other when unknown."""
    if amount is None or pd.isna(amount):
        return OTHER
    return DEPOSIT if amount >= 0 else WITHDRAWAL


# ── Pass 2: cross-account reconciliation ──────────────────────────────────────

def reconcile_transfers(df: pd.DataFrame, own_accounts=(), account_map=None) -> pd.DataFrame:
    """
    Settle every :data:`PENDING_TRANSFER` row now that all accounts are present.

    Must run on the **combined** frame from every account. Running it per account
    defeats the whole mechanism: the two legs of an inter-account transfer live in
    different accounts, so a per-account view can never see them net to zero.

    Resolution order:

    1. **REFID pairing.** Rows sharing a REFID with one positive and one negative
       leg summing to zero are two halves of one move — both become
       :data:`INTERNAL`. This is evidence, not a guess, so it wins outright.
    2. **Counterparty lookup.** ``account_map`` (what the user tagged in the UI)
       beats ``own_accounts`` (last 4 digits of your active accounts), which beats
       the default.
    3. **Default: external.** Direction from the sign, and ``Needs Review`` set so
       the UI can ask about a counterparty it has not seen tagged before.

    Args:
        df: Combined transaction frame. Needs ``Category``, ``Total Value``,
            ``Ref ID`` and ``Counterparty`` columns.
        own_accounts: Account numbers you own; only the last 4 digits are compared.
        account_map: ``{'1607': 'external', '7449': 'internal'}`` from
            :mod:`portfolio.storage.accounts`.

    Returns:
        A new frame with every ``PENDING_TRANSFER`` replaced, plus the columns
        ``Needs Review`` (bool) and ``Classification Note`` (str) explaining each
        decision so the UI never has to state a conclusion it cannot justify.
    """
    if df.empty or 'Category' not in df.columns:
        return df

    out = df.copy()
    account_map = {str(k)[-4:]: v for k, v in (account_map or {}).items()}
    own = {str(a)[-4:] for a in own_accounts if a is not None}

    for column, default in (('Needs Review', False), ('Classification Note', '')):
        if column not in out.columns:
            out[column] = default

    pending = out['Category'] == PENDING_TRANSFER
    if not pending.any():
        return out

    amounts = pd.to_numeric(out['Total Value'], errors='coerce')

    # ── 1. REFID pairing ──────────────────────────────────────────────────────
    # groupby drops rows with a null Ref ID, which is what we want: a row with no
    # REFID has nothing to pair against.
    for ref_id, group in out[pending].groupby('Ref ID'):
        legs = amounts.loc[group.index].dropna()
        has_both_directions = (legs > 0).any() and (legs < 0).any()
        if len(legs) >= 2 and has_both_directions and abs(legs.sum()) <= 0.01:
            out.loc[group.index, 'Category'] = INTERNAL
            out.loc[group.index, 'Classification Note'] = (
                f'paired inter-account transfer (REFID {ref_id}, legs net to $0)'
            )

    # ── 2 & 3. Counterparty lookup, then external by default ──────────────────
    for idx in out.index[out['Category'] == PENDING_TRANSFER]:
        counterparty = out.at[idx, 'Counterparty']
        tagged = account_map.get(counterparty) if counterparty else None

        if tagged == 'internal':
            out.at[idx, 'Category'] = INTERNAL
            out.at[idx, 'Classification Note'] = f'account {counterparty} tagged as yours'
        elif tagged == 'external':
            out.at[idx, 'Category'] = direction_by_amount(amounts.at[idx])
            out.at[idx, 'Classification Note'] = f'account {counterparty} tagged as external'
        elif counterparty in own:
            out.at[idx, 'Category'] = INTERNAL
            out.at[idx, 'Classification Note'] = (
                f'account {counterparty} matches one of your E*TRADE accounts'
            )
        else:
            out.at[idx, 'Category'] = direction_by_amount(amounts.at[idx])
            out.at[idx, 'Needs Review'] = True
            out.at[idx, 'Classification Note'] = (
                f'unmatched transfer to account {counterparty or "?"} — counted as external; '
                'tag it in Transfer Review if it is an account of yours'
            )

    return out


def unresolved_counterparties(df: pd.DataFrame) -> pd.DataFrame:
    """
    Summarise every counterparty account still awaiting a decision.

    Feeds the Transfer Review panel: one row per counterparty with how many
    transfers and how much money hinge on the answer, so the user can see what
    tagging it would change before they tag it.
    """
    empty = pd.DataFrame(columns=['Counterparty', 'Transfers', 'Net Amount', 'First', 'Last'])
    if df.empty or 'Needs Review' not in df.columns:
        return empty

    flagged = df[df['Needs Review'].fillna(False)].copy()
    if flagged.empty:
        return empty

    flagged['Total Value'] = pd.to_numeric(flagged['Total Value'], errors='coerce')
    summary = flagged.groupby('Counterparty').agg(
        Transfers=('Total Value', 'size'),
        **{'Net Amount': ('Total Value', 'sum')},
        First=('Date', 'min'),
        Last=('Date', 'max'),
    ).reset_index()
    return summary.sort_values('Net Amount')


# ── Views over a classified frame ─────────────────────────────────────────────

def get_cash_flows(transaction_df: pd.DataFrame) -> pd.DataFrame:
    """
    Deposit and withdrawal rows only — the input to deposit-adjusted return.

    Excludes trades, income, internal transfers and unrecognised rows. Positive
    ``Total Value`` is money in.
    """
    columns = ['Date', 'Description', 'Total Value', 'Category', 'Needs Review']
    if transaction_df.empty or 'Category' not in transaction_df.columns:
        return pd.DataFrame(columns=columns)

    flows = transaction_df[transaction_df['Category'].isin(EXTERNAL_CATEGORIES)].copy()
    if flows.empty:
        return pd.DataFrame(columns=columns)

    flows = flows.rename(columns={'Security Name': 'Description'})
    present = [c for c in columns if c in flows.columns]
    return flows[present].sort_values('Date', ascending=False).reset_index(drop=True)


def get_income(transaction_df: pd.DataFrame) -> pd.DataFrame:
    """Dividend and interest rows — money generated inside the account."""
    columns = ['Date', 'Description', 'Total Value', 'Transaction Type']
    if transaction_df.empty or 'Category' not in transaction_df.columns:
        return pd.DataFrame(columns=columns)

    income = transaction_df[transaction_df['Category'] == INCOME].copy()
    if income.empty:
        return pd.DataFrame(columns=columns)

    income = income.rename(columns={'Security Name': 'Description'})
    present = [c for c in columns if c in income.columns]
    return income[present].sort_values('Date', ascending=False).reset_index(drop=True)
