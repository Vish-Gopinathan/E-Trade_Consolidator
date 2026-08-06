"""
Batch export from the terminal.

Pulls holdings and transactions for a date range and writes two Excel workbooks.
The dashboard (``streamlit run app.py``) does the same work interactively; this
entry point exists for scripted or scheduled runs.

    python cli.py --start 2024-01-01 --output-dir outputs/
"""

import argparse
import datetime
import logging
import os

import pandas as pd

from portfolio import analytics, classify, etrade, excel, schema
from portfolio.storage import accounts as account_map_store


def parse_args():
    parser = argparse.ArgumentParser(description='E*TRADE portfolio consolidator and analytics')
    parser.add_argument(
        '--start', type=datetime.date.fromisoformat, metavar='YYYY-MM-DD',
        default=datetime.date(2000, 1, 1),
        help='Start of transaction history. The default reaches back past account '
             'opening, which is what makes the deposit-adjusted return meaningful.',
    )
    parser.add_argument(
        '--end', type=datetime.date.fromisoformat, metavar='YYYY-MM-DD',
        default=datetime.date.today(), help='End of transaction history (default: today)',
    )
    parser.add_argument(
        '--output-dir', default='.', metavar='DIR',
        help='Where to write the Excel files (default: current directory)',
    )
    parser.add_argument(
        '--verbose', action='store_true',
        help='Log every transaction classification decision',
    )
    return parser.parse_args()


def main():
    args = parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format='%(levelname)s %(name)s: %(message)s',
    )

    print('Authenticating with E*TRADE...')
    auth_tokens = etrade.authenticate_etrade()

    print('Fetching accounts and positions...')
    active_accounts, accounts_obj = etrade.fetch_active_accounts(auth_tokens)

    frames, total_cash, total_value = [], 0.0, 0.0
    for key in active_accounts['accountIdKey']:
        frames.append(etrade.get_portfolio(accounts_obj, key))
        totals = etrade.get_account_totals(accounts_obj, key)
        total_cash += totals['net_cash']
        total_value += totals['total_account_value']

    combined = pd.concat([f for f in frames if not f.empty], ignore_index=True)
    holdings = etrade.consolidate_holdings(combined, cash=total_cash)
    summary = etrade.portfolio_summary(holdings, cash=total_cash)

    print(f'Fetching transactions from {args.start} to {args.end}...')
    transactions = etrade.get_all_consolidated_transactions(
        accounts_obj, active_accounts, args.start, args.end,
        account_map=account_map_store.load(),
    )
    cash_flows = classify.get_cash_flows(transactions)
    income = classify.get_income(transactions)

    report = analytics.PortfolioAnalytics(holdings, transactions, cash_flows).generate_full_report()

    os.makedirs(args.output_dir, exist_ok=True)
    today = datetime.date.today().isoformat()
    excel.export_to_excel(
        holdings, transactions, cash_flows, income,
        output=os.path.join(args.output_dir, f'portfolio_consolidated_{today}.xlsx'),
    )
    excel.export_analytics_to_excel(
        holdings, report,
        output=os.path.join(args.output_dir, f'portfolio_analytics_{today}.xlsx'),
    )

    _print_summary(summary, report, total_value, total_cash)


def _print_summary(summary, report, reported_total, cash):
    print('\n' + '=' * 60)
    print('PORTFOLIO SUMMARY')
    print('=' * 60)
    print(f'  Positions          {summary["Total Stock Market Value"]:>14,.2f}')
    print(f'  Cash               {cash:>14,.2f}')
    print(f'  Total value        {summary["Total Portfolio Value"]:>14,.2f}')
    print(f'  E*TRADE reports    {reported_total:>14,.2f}')

    drift = abs(reported_total - summary['Total Portfolio Value'])
    if reported_total and drift > max(50.0, reported_total * 0.005):
        print(f'  ! Differs from E*TRADE by {drift:,.2f} — check pending settlements.')

    flows = report[schema.CASH_FLOWS]
    print(f'\n  Deposited          {flows[schema.TOTAL_DEPOSITED]:>14,.2f}')
    print(f'  Withdrawn          {flows[schema.TOTAL_WITHDRAWN]:>14,.2f}')

    needs_review = flows.get(schema.FLOWS_NEEDING_REVIEW, 0)
    if needs_review:
        print(f'  ! {needs_review} transfer(s) counted as external because the counterparty')
        print('    account is unrecognised. Review them in the dashboard.')

    performance = report[schema.PERFORMANCE]
    print(f'\n  Unrealised return  {performance[schema.TOTAL_RETURN_PCT]:>13,.2f}%')
    adjusted = performance[schema.DEPOSIT_ADJUSTED_RETURN_PCT]
    if adjusted is not None:
        print(f'  Deposit-adjusted   {adjusted:>13,.2f}%')
        print(f'    {performance[schema.DEPOSIT_ADJUSTED_RETURN_BASIS]}')

    concentration = report[schema.CONCENTRATION]
    print(f'\n  Positions          {concentration[schema.TOTAL_POSITIONS]:>14}')
    print(f'  Top 5 weight       {concentration[schema.TOP_5_PCT]:>13,.2f}%')
    print(f'  HHI                {concentration[schema.HHI]:>14,.0f}  '
          f'({concentration[schema.HHI_INTERPRETATION]})')
    print('=' * 60)


if __name__ == '__main__':
    try:
        main()
    except Exception:
        logging.exception('export failed')
        raise SystemExit(1)
