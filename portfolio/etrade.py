"""
E*TRADE API access and holdings consolidation.

Everything that talks to E*TRADE lives here: OAuth, account discovery, positions,
balances and transaction history. Classification of what those transactions *mean*
is deliberately separate — see :mod:`portfolio.classify`.

Two API details drive most of the code below and are easy to get wrong:

**Balances.** ``BalanceResponse.Computed.accountBalance`` is a cash-side figure,
*not* the total account value. Total value lives at
``Computed.RealTimeValues.totalAccountValue``. Treating the two as interchangeable
makes cash read as zero — see :func:`get_account_totals`.

**Transaction windows.** ``list_transactions`` rejects any range longer than 90
days. :func:`_date_chunks` splits longer ranges transparently, so callers can ask
for a decade without knowing.

This module must not import streamlit; the UI layer lives in ``ui/``.
"""

import datetime
import logging
import os
import webbrowser

import pandas as pd
import pyetrade
from dotenv import load_dotenv

from portfolio import classify, paths

LOGGER = logging.getLogger(__name__)

load_dotenv(paths.ROOT / '.env')


# ── Authentication ────────────────────────────────────────────────────────────

def _credential(key: str) -> str:
    """Read a credential from Streamlit secrets when available, else the env."""
    try:
        import streamlit as st
        return st.secrets[key]
    except Exception:
        return os.getenv(key, '')


def get_oauth_url():
    """
    Step 1 of the browser OAuth flow used by the dashboard.

    Returns:
        ``(authorization_url, oauth_object, consumer_key, consumer_secret)``.
        All four must be held until :func:`complete_oauth` — the same oauth object
        has to exchange the verifier code.
    """
    consumer_key = _credential('CONSUMER_KEY')
    consumer_secret = _credential('CONSUMER_SECRET')
    if not consumer_key or not consumer_secret:
        raise RuntimeError(
            'CONSUMER_KEY and CONSUMER_SECRET are not set. Copy .env.example to '
            '.env and fill them in, or add them to Streamlit secrets.'
        )
    oauth = pyetrade.ETradeOAuth(consumer_key, consumer_secret)
    return oauth.get_request_token(), oauth, consumer_key, consumer_secret


def complete_oauth(oauth, verifier_code, consumer_key, consumer_secret) -> dict:
    """Step 2: exchange the verifier code for access tokens."""
    tokens = oauth.get_access_token(verifier_code)
    return {
        'consumer_key': consumer_key,
        'consumer_secret': consumer_secret,
        'oauth_token': tokens['oauth_token'],
        'oauth_token_secret': tokens['oauth_token_secret'],
    }


def authenticate_etrade() -> dict:
    """
    Terminal OAuth flow for :mod:`cli`. Opens the authorization URL in a browser
    and prompts for the verifier code on stdin.
    """
    url, oauth, consumer_key, consumer_secret = get_oauth_url()
    try:
        webbrowser.open(url)
        print('Authorization URL opened in your browser. Complete the flow there.')
    except Exception as exc:  # pragma: no cover - depends on the desktop session
        print(f'Could not open a browser. Visit this URL manually: {url}\n({exc})')

    verifier_code = input('Enter the verification code from the webpage: ')
    return complete_oauth(oauth, verifier_code, consumer_key, consumer_secret)


# ── Accounts, positions, balances ─────────────────────────────────────────────

def fetch_active_accounts(auth_tokens: dict):
    """
    Return ``(active_accounts_df, accounts_obj)`` for every ACTIVE account.

    The accounts object is reused for all later calls, so it is returned rather
    than rebuilt per request.
    """
    accounts_obj = pyetrade.ETradeAccounts(
        auth_tokens['consumer_key'],
        auth_tokens['consumer_secret'],
        auth_tokens['oauth_token'],
        auth_tokens['oauth_token_secret'],
        dev=False,
    )
    response = accounts_obj.list_accounts(resp_format='json')
    accounts = response['AccountListResponse']['Accounts']['Account']
    accounts_df = pd.DataFrame(accounts)
    return accounts_df[accounts_df['accountStatus'] == 'ACTIVE'], accounts_obj


def get_portfolio(accounts_obj, account_id_key: str) -> pd.DataFrame:
    """
    Return one account's positions as a DataFrame.

    An account with no positions returns an empty frame rather than raising — a
    cash-only IRA is a normal state, not an error.
    """
    response = accounts_obj.get_account_portfolio(
        account_id_key, resp_format='json', view='Complete'
    )
    account_portfolio = response.get('PortfolioResponse', {}).get('AccountPortfolio')
    if not account_portfolio:
        return pd.DataFrame()

    data = account_portfolio[0] if isinstance(account_portfolio, list) else account_portfolio
    positions = data.get('Position') or []
    if isinstance(positions, dict):  # single position comes back unwrapped
        positions = [positions]

    rows = [{
        'Symbol': position['Product']['symbol'],
        'Symbol Description': position['Complete']['symbolDescription'],
        'Current Price': position['Complete']['price'],
        'Quantity': position['quantity'],
        'Date Acquired': pd.to_datetime(position['dateAcquired'], unit='ms'),
        'Price Paid': position['pricePaid'],
        'Total Cost': position['totalCost'],
        'Market Value': position['marketValue'],
        'Total Gain': position['totalGain'],
        'Total Gain %': position['totalGainPct'],
        'Percent of Portfolio': position['pctOfPortfolio'],
    } for position in positions]

    return pd.DataFrame(rows)


def get_account_totals(accounts_obj, account_id_key: str) -> dict:
    """
    Return the authoritative cash and total-value figures for one account.

    The two fields are **not** interchangeable, and conflating them is what made
    cash display as zero:

    ``Computed.RealTimeValues.totalAccountValue``
        What E*TRADE's website calls Total Account Value — positions plus cash.
    ``Computed.accountBalance``
        A cash-side balance. Subtracting position value from it yields a large
        negative number, not the free cash.
    ``Computed.netCash``
        Free uninvested cash. This is what the dashboard shows as "Cash".

    ``pyetrade`` requests ``realTimeNAV=True`` by default, so ``RealTimeValues`` is
    normally present; the fallbacks cover accounts where it is not.

    Returns:
        ``{'account_id_key', 'total_account_value', 'net_cash', 'raw_computed'}``.
        ``raw_computed`` is kept so the UI can show the untouched API response when
        a figure looks wrong.
    """
    response = accounts_obj.get_account_balance(account_id_key, resp_format='json')
    computed = response.get('BalanceResponse', {}).get('Computed', {}) or {}
    real_time = computed.get('RealTimeValues', {}) or {}

    def _number(source: dict, *keys) -> float:
        """First key present with a parseable value, else 0.0."""
        for key in keys:
            value = source.get(key)
            if value in (None, ''):
                continue
            try:
                return float(value)
            except (TypeError, ValueError):
                continue
        return 0.0

    total_value = _number(real_time, 'totalAccountValue')
    if not total_value:
        total_value = _number(computed, 'accountBalance')

    return {
        'account_id_key': account_id_key,
        'total_account_value': total_value,
        'net_cash': _number(
            computed, 'netCash', 'cashAvailableForInvestment', 'cashBalance'
        ),
        'raw_computed': computed,
    }


# ── Consolidation ─────────────────────────────────────────────────────────────

def consolidate_holdings(df: pd.DataFrame, cash: float = 0) -> pd.DataFrame:
    """
    Merge positions in the same symbol across accounts into one row each.

    Cost basis is summed and re-divided by total quantity, giving a genuine
    weighted average price paid rather than an average of averages.

    Args:
        df: Concatenated per-account positions from :func:`get_portfolio`.
        cash: Total free cash across accounts, appended as a final ``CASH`` row so
            allocation percentages account for it.
    """
    if df.empty:
        return pd.DataFrame(columns=[
            'Symbol', 'Symbol Description', 'Current Price', 'Quantity',
            'Date Acquired', 'Price Paid', 'Total Cost', 'Market Value',
            'Total Gain', 'Total Gain %', 'Percent of Portfolio',
        ])

    consolidated = df.groupby('Symbol').agg({
        'Quantity': 'sum',
        'Total Cost': 'sum',
        'Market Value': 'sum',
        'Symbol Description': 'first',
        'Date Acquired': 'min',
        'Current Price': 'max',
    }).reset_index()

    consolidated['Price Paid'] = consolidated['Total Cost'] / consolidated['Quantity']
    consolidated['Total Gain'] = consolidated['Market Value'] - consolidated['Total Cost']
    consolidated['Total Gain %'] = (
        consolidated['Total Gain'] / consolidated['Total Cost']
    ) * 100

    positions_value = consolidated['Market Value'].sum()
    total_value = positions_value + max(cash, 0)
    consolidated['Percent of Portfolio'] = (
        consolidated['Market Value'] / total_value * 100 if total_value else 0
    )

    for column in ('Quantity', 'Price Paid', 'Total Cost', 'Market Value',
                   'Total Gain', 'Total Gain %', 'Percent of Portfolio'):
        consolidated[column] = consolidated[column].round(2)

    consolidated = consolidated.sort_values('Market Value', ascending=False)
    result = consolidated[[
        'Symbol', 'Symbol Description', 'Current Price', 'Quantity', 'Date Acquired',
        'Price Paid', 'Total Cost', 'Market Value', 'Total Gain', 'Total Gain %',
        'Percent of Portfolio',
    ]]

    if cash > 0:
        cash_row = pd.DataFrame({
            'Symbol': ['CASH'],
            'Symbol Description': ['Cash'],
            'Current Price': [1.0],
            'Quantity': [round(cash, 2)],
            'Date Acquired': [pd.NaT],
            'Price Paid': [1.0],
            'Total Cost': [round(cash, 2)],
            'Market Value': [round(cash, 2)],
            'Total Gain': [0.0],
            'Total Gain %': [0.0],
            'Percent of Portfolio': [round(cash / total_value * 100, 2) if total_value else 0.0],
        })
        result = pd.concat([result, cash_row], ignore_index=True)

    return result.reset_index(drop=True)


def portfolio_summary(consolidated_df: pd.DataFrame, cash: float = 0) -> dict:
    """
    Headline figures for the Overview page. All dollar values are floats;
    percentages are whole numbers (30.11 means 30.11%).
    """
    stocks = consolidated_df[consolidated_df['Symbol'] != 'CASH']
    stock_value = float(stocks['Market Value'].sum())
    cost_basis = float(stocks['Total Cost'].sum())
    unrealized_gain = float(stocks['Total Gain'].sum())
    total_value = stock_value + cash

    return {
        'Total Stocks': int(len(stocks)),
        'Total Stock Market Value': stock_value,
        'Cash': float(cash),
        'Total Portfolio Value': total_value,
        'Total Cost Basis': cost_basis,
        'Total Unrealized Gain': unrealized_gain,
        'Total Unrealized Gain %': (unrealized_gain / cost_basis * 100) if cost_basis else 0.0,
        'Cash Percentage': (cash / total_value * 100) if total_value > 0 else 0.0,
        'Largest Holdings': stocks.head(3)[
            ['Symbol', 'Market Value', 'Percent of Portfolio']
        ].to_dict('records'),
    }


# ── Transactions ──────────────────────────────────────────────────────────────

def _date_chunks(start_date, end_date, chunk_days=89):
    """
    Split a date range into windows the API will accept.

    ``list_transactions`` rejects spans over 90 days, so we walk it in 89-day
    windows (one day of margin) and concatenate. Yields ``(start, end)`` pairs.
    """
    current = start_date
    delta = datetime.timedelta(days=chunk_days)
    while current <= end_date:
        chunk_end = min(current + delta, end_date)
        yield current, chunk_end
        current = chunk_end + datetime.timedelta(days=1)


def get_consolidated_transactions(accounts_obj, account_id_key, start_date, end_date,
                                  account_label=None) -> pd.DataFrame:
    """
    Fetch and classify one account's transactions over any date range.

    Every row carries the same schema whether it is a trade, a dividend or a
    transfer; inapplicable columns are None.

    Columns:
        Date, Security Name, Symbol, Quantity, Price, Total Value,
        Transaction Type, Category, Account, Ref ID, Counterparty.

    ``Category`` may be :data:`~portfolio.classify.PENDING_TRANSFER` — those rows
    are only resolvable once every account has been fetched. Use
    :func:`get_all_consolidated_transactions`, which runs the second pass, rather
    than calling this directly.

    Args:
        account_label: Human-readable account name for the ``Account`` column.
            Defaults to the account id key.
    """
    if not isinstance(start_date, datetime.date):
        raise TypeError('start_date must be a datetime.date')
    if not isinstance(end_date, datetime.date):
        raise TypeError('end_date must be a datetime.date')

    raw_transactions = []
    for chunk_start, chunk_end in _date_chunks(start_date, end_date):
        try:
            response = accounts_obj.list_transactions(
                account_id_key, chunk_start, chunk_end, resp_format='json'
            )
            chunk = response.get('TransactionListResponse', {}).get('Transaction', [])
            if chunk:
                raw_transactions.extend(chunk)
        except Exception as exc:
            LOGGER.warning(
                'transaction fetch failed for %s %s–%s: %s',
                account_id_key, chunk_start, chunk_end, exc,
            )

    if not raw_transactions:
        return pd.DataFrame()

    label = account_label or account_id_key
    rows = []
    for transaction in raw_transactions:
        t_type = transaction.get('transactionType', '')
        description = transaction.get('description', '') or ''
        amount = transaction.get('amount')
        try:
            amount = float(amount) if amount is not None else None
        except (TypeError, ValueError):
            amount = None

        row = {
            'Date': pd.to_datetime(transaction['transactionDate'], unit='ms'),
            'Security Name': description,
            'Symbol': '',
            'Quantity': None,
            'Price': None,
            'Total Value': amount,
            'Transaction Type': t_type,
            'Category': classify.OTHER,
            'Account': label,
            'Ref ID': classify.parse_ref_id(description),
            'Counterparty': classify.parse_counterparty(description),
        }

        if t_type in classify.TRADE_TYPES:
            row.update(_trade_detail(accounts_obj, account_id_key, transaction, amount))
            row['Category'] = classify.TRADE
        else:
            row['Category'] = classify.classify_row(t_type, description, amount)

        rows.append(row)

    return pd.DataFrame(rows).sort_values('Date', ascending=False).reset_index(drop=True)


def _trade_detail(accounts_obj, account_id_key, transaction, amount) -> dict:
    """
    Fetch quantity, price and symbol for one trade.

    Requires a second API call per trade — the transaction list itself carries
    only the dollar amount. A failure here degrades to the amount alone rather
    than losing the row.
    """
    try:
        response = accounts_obj.list_transaction_details(
            account_id_key, transaction['transactionId'], resp_format='json'
        )
        details = response.get('TransactionDetailsResponse', {}).get('Brokerage', {}) or {}
    except Exception as exc:
        LOGGER.debug('transaction detail failed for %s: %s', transaction.get('transactionId'), exc)
        details = {}

    product = details.get('Product') or details.get('product') or {}
    symbol = product.get('symbol', '')
    if not symbol:
        symbol = ((transaction.get('brokerage') or {}).get('product') or {}).get('symbol', '')

    quantity, price = details.get('quantity'), details.get('price')
    total_value = amount
    if quantity is not None and price is not None:
        try:
            quantity, price = float(quantity), float(price)
            total_value = quantity * price
        except (TypeError, ValueError):
            quantity = price = None

    return {'Symbol': symbol, 'Quantity': quantity, 'Price': price, 'Total Value': total_value}


def get_all_consolidated_transactions(accounts_obj, active_accounts, start_date, end_date,
                                      account_map=None) -> pd.DataFrame:
    """
    Fetch every account's transactions and reconcile transfers across them.

    The second pass is the point of this function. Two legs of an inter-account
    transfer live in different accounts, so only a combined frame can recognise
    that they cancel out — see :func:`portfolio.classify.reconcile_transfers`.

    Args:
        account_map: User decisions from :mod:`portfolio.storage.accounts`,
            keyed by the counterparty's last 4 digits.
    """
    frames = []
    for _, account in active_accounts.iterrows():
        frames.append(get_consolidated_transactions(
            accounts_obj,
            account['accountIdKey'],
            start_date,
            end_date,
            account_label=account.get('accountName') or account.get('accountId'),
        ))

    frames = [f for f in frames if not f.empty]
    if not frames:
        return pd.DataFrame()

    combined = pd.concat(frames, ignore_index=True)
    combined = classify.reconcile_transfers(
        combined,
        own_accounts=active_accounts.get('accountId', pd.Series(dtype=str)).tolist(),
        account_map=account_map,
    )
    return combined.sort_values('Date', ascending=False).reset_index(drop=True)


#: Re-exported so callers have one import for the whole transaction pipeline.
get_cash_flows = classify.get_cash_flows
get_income = classify.get_income
