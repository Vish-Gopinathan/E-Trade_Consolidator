import os
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

def main():
    """
    Main orchestrator - runs consolidator then analytics.
    """
    try:
        print("=" * 60)
        print("PORTFOLIO CONSOLIDATOR & ANALYTICS")
        print("=" * 60)
        
        # ===== CONSOLIDATION PHASE =====
        print("\n[1/3] Authenticating with E*TRADE...")
        auth_tokens = authenticate_etrade()
        
        print("[2/3] Fetching accounts and portfolios...")
        active_accounts, accounts_obj = fetch_active_accounts(auth_tokens)
        
        # Consolidate portfolio across all active accounts
        combined_portfolio = __import__('pandas').DataFrame()
        total_cash = 0
        
        for key in active_accounts['accountIdKey']:
            account_portfolio = get_portfolio(accounts_obj, key)
            combined_portfolio = __import__('pandas').concat([combined_portfolio, account_portfolio])
            total_cash += get_cash_balance(accounts_obj, key)
        
        combined_portfolio = combined_portfolio.sort_values(by='Symbol Description').reset_index(drop=True)
        
        # Consolidate holdings
        consolidated_portfolio = consolidate_holdings(combined_portfolio, total_cash)
        summary = portfolio_summary(consolidated_portfolio, total_cash)
        
        # Get transaction data
        today = datetime.date.today()
        start_date = datetime.date(2025, 1, 1)
        end_date = today
        
        print(f"   Fetching transactions from {start_date} to {end_date}...")
        transaction_data = get_all_consolidated_transactions(accounts_obj, active_accounts, start_date, end_date)
        
        # Export consolidated portfolio
        print("[3/3] Exporting consolidated portfolio...")
        export_to_excel(consolidated_portfolio, transaction_data)
        
        print("\n" + "=" * 60)
        print("PORTFOLIO SUMMARY")
        print("=" * 60)
        for key, value in summary.items():
            print(f"{key}: {value}")
        
        # ===== ANALYTICS PHASE =====
        print("\n" + "=" * 60)
        print("GENERATING ANALYTICS & RISK METRICS")
        print("=" * 60)
        
        analytics = PortfolioAnalytics(consolidated_portfolio, transaction_data)
        full_report = analytics.generate_full_report()
        
        # Export analytics to separate Excel file
        export_analytics_to_excel(consolidated_portfolio, full_report)
        
        print("\n" + "=" * 60)
        print("✓ COMPLETE - All reports generated successfully")
        print("=" * 60)
        
    except Exception as e:
        print(f"\n✗ ERROR: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()