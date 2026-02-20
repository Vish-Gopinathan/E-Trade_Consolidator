import os
import argparse
import datetime
from consolidator import (
    authenticate_etrade,
    fetch_active_accounts,
    get_portfolio,
    get_cash_balance,
    consolidate_holdings,
    portfolio_summary,
    export_to_excel,
    get_all_consolidated_transactions
)
from analytics import PortfolioAnalytics, export_analytics_to_excel


def parse_args():
    parser = argparse.ArgumentParser(description='E*TRADE Portfolio Consolidator & Analytics')
    parser.add_argument(
        '--start',
        type=lambda s: datetime.date.fromisoformat(s),
        default=datetime.date(datetime.date.today().year, 1, 1),
        metavar='YYYY-MM-DD',
        help='Start date for transaction history (default: Jan 1 of current year)'
    )
    parser.add_argument(
        '--end',
        type=lambda s: datetime.date.fromisoformat(s),
        default=datetime.date.today(),
        metavar='YYYY-MM-DD',
        help='End date for transaction history (default: today)'
    )
    parser.add_argument(
        '--output-dir',
        default='.',
        metavar='DIR',
        help='Directory to write output Excel files (default: current directory)'
    )
    return parser.parse_args()


def main():
    """
    Main orchestrator - runs consolidator then analytics.
    """
    import pandas as pd

    args = parse_args()

    try:
        print("=" * 60)
        print("PORTFOLIO CONSOLIDATOR & ANALYTICS")
        print("=" * 60)

        # ===== CONSOLIDATION PHASE =====
        print("\n[1/4] Authenticating with E*TRADE...")
        auth_tokens = authenticate_etrade()

        print("[2/4] Fetching accounts and portfolios...")
        active_accounts, accounts_obj = fetch_active_accounts(auth_tokens)

        # Consolidate portfolio across all active accounts
        combined_portfolio = pd.DataFrame()
        total_cash = 0

        for key in active_accounts['accountIdKey']:
            account_portfolio = get_portfolio(accounts_obj, key)
            combined_portfolio = pd.concat([combined_portfolio, account_portfolio])
            total_cash += get_cash_balance(accounts_obj, key)

        combined_portfolio = combined_portfolio.sort_values(by='Symbol Description').reset_index(drop=True)

        # Consolidate holdings
        consolidated_portfolio = consolidate_holdings(combined_portfolio, total_cash)
        summary = portfolio_summary(consolidated_portfolio, total_cash)

        # Get transaction data using CLI-provided (or default) date range
        start_date = args.start
        end_date = args.end

        print(f"   Fetching transactions from {start_date} to {end_date}...")
        transaction_data = get_all_consolidated_transactions(accounts_obj, active_accounts, start_date, end_date)

        # Build output paths
        output_dir = args.output_dir
        os.makedirs(output_dir, exist_ok=True)
        date_str = datetime.date.today().strftime("%Y-%m-%d")
        holdings_file = os.path.join(output_dir, f'portfolio_consolidated_{date_str}.xlsx')
        analytics_file = os.path.join(output_dir, f'portfolio_analytics_{date_str}.xlsx')

        # Export consolidated portfolio
        print("[3/4] Exporting consolidated portfolio...")
        export_to_excel(consolidated_portfolio, transaction_data, filename=holdings_file)

        print("\n" + "=" * 60)
        print("PORTFOLIO SUMMARY")
        print("=" * 60)
        for key, value in summary.items():
            if key == 'Largest Holdings':
                print(f"{key}:")
                for holding in value:
                    print(f"  {holding['Symbol']}: ${holding['Market Value']:,.2f} ({holding['Percent of Portfolio']:.2f}%)")
            elif isinstance(value, float):
                print(f"{key}: {value:,.2f}")
            else:
                print(f"{key}: {value}")

        # ===== ANALYTICS PHASE =====
        print("\n" + "=" * 60)
        print("[4/4] GENERATING ANALYTICS & RISK METRICS")
        print("=" * 60)

        analytics = PortfolioAnalytics(consolidated_portfolio, transaction_data)
        full_report = analytics.generate_full_report()

        # Export analytics to separate Excel file
        export_analytics_to_excel(consolidated_portfolio, full_report, filename=analytics_file)
        
        print("\n" + "=" * 60)
        print("✓ COMPLETE - All reports generated successfully")
        print("=" * 60)
        
    except Exception as e:
        print(f"\n✗ ERROR: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()