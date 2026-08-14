"""
Assembling the portfolio dict that every page reads.

There is exactly one definition of what a portfolio is, and it lives here. It was
previously inline at the end of the refresh in ``app.py``, which meant the only
way to produce one was to hold live E*TRADE credentials — so a ledger correction,
a hand-entered backfill or a Streamlit Cloud cold start could not show anything
until someone completed an OAuth round trip.

Transactions come from the ledger rather than from the fetch, because the ledger
is the accumulating store and a single fetch is only ever a window onto it.
Classification is re-derived on the way through: rows carry the verdict the rules
gave when they were written, so without recomputing, a fix reaches only rows
downloaded afterwards.
"""

import datetime
import logging

import pandas as pd

from portfolio import analytics, classify, etrade, schema
from portfolio.storage import accounts as account_map_store
from portfolio.storage import ledger

LOGGER = logging.getLogger(__name__)


def build_portfolio(holdings, transactions=None, cash=0.0, reported_total=None,
                    account_balances=(), fetched_at=None, splits=None) -> dict:
    """
    Build the portfolio dict from holdings and the ledger.

    Args:
        holdings: Consolidated holdings frame, including the synthetic CASH row.
        transactions: Classified transactions. Defaults to the whole ledger,
            which is what a rebuild wants; the refresh passes its merged frame.
        cash: Cash balance, used for the summary block.
        reported_total: E*TRADE's own total account value, kept so the UI can
            show the drift between it and positions plus cash.
        account_balances: Per-account balances, minus the raw payload.
        fetched_at: ISO timestamp. Defaults to now.
        splits: ``{symbol: ratios}`` for realised P&L. Optional — absent, every
            split factor is 1.0, which is correct for anything that has not split.

    Returns:
        The dict the pages read, keyed as ``holdings``, ``transactions``,
        ``cash_flows``, ``income``, ``analytics_report``, ``summary`` and so on.
    """
    if transactions is None:
        transactions = ledger.load()

    if transactions is not None and not transactions.empty:
        # Your own accounts, so pass 2 can recognise a transfer between them even
        # when the matching leg is outside the fetched window. The ledger
        # remembers these by opaque key, which survives a rename.
        own = [a['last4'] for a in ledger.known_accounts().values() if a.get('last4')]
        transactions = classify.reconcile_transfers(
            classify.classify_frame(transactions),
            own_accounts=own,
            account_map=account_map_store.load(),
        )
        transactions = transactions.sort_values('Date', ascending=False).reset_index(drop=True)
    else:
        transactions = pd.DataFrame()

    cash_flows = classify.get_cash_flows(transactions)
    income = classify.get_income(transactions)
    report = analytics.PortfolioAnalytics(
        holdings, transactions, cash_flows, splits=splits
    ).generate_full_report()

    return {
        'fetched_at': fetched_at or datetime.datetime.now().isoformat(),
        'holdings': holdings,
        'transactions': transactions,
        'cash_flows': cash_flows,
        'income': income,
        'analytics_report': report,
        'summary': etrade.portfolio_summary(holdings, cash=cash),
        'reported_total': reported_total,
        'transactions_coverage': ledger.coverage(),
        'account_balances': list(account_balances),
    }


def rebuild_from_stored(portfolio: dict = None) -> dict:
    """
    Rebuild the portfolio from the ledger and the last known holdings.

    No network. Holdings, cash and balances are carried over from whatever was
    last saved — they describe today and cannot be recomputed — while every
    figure derived from transactions is recalculated against the current ledger.

    This is what makes a ledger correction or a hand-entered backfill visible
    without an E*TRADE refresh, and what lets a cold start open with data.

    Raises:
        ValueError: when there is no stored portfolio to take holdings from.
    """
    from portfolio.storage import cache, snapshot

    if portfolio is None:
        portfolio = cache.load_portfolio()
        if not portfolio and snapshot.exists():
            portfolio = snapshot.load()
    if not portfolio:
        raise ValueError('no stored portfolio to rebuild from — refresh from E*TRADE first')

    holdings = portfolio.get('holdings')
    if holdings is None or (hasattr(holdings, 'empty') and holdings.empty):
        raise ValueError('the stored portfolio has no holdings to rebuild from')

    cash_rows = holdings[holdings['Symbol'] == 'CASH']['Market Value']
    return build_portfolio(
        holdings,
        cash=float(cash_rows.iloc[0]) if len(cash_rows) else 0.0,
        reported_total=portfolio.get('reported_total'),
        account_balances=portfolio.get('account_balances', ()),
        fetched_at=portfolio.get('fetched_at'),
    )
