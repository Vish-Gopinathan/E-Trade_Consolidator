import json
import os
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import warnings
warnings.filterwarnings('ignore')

# Default sector mappings used when sectors.json is not present.
# To customise, edit sectors.json in the same directory as this file.
_DEFAULT_SECTOR_GROUPS = {
    'Tech': ['AAPL', 'MSFT', 'GOOGL', 'META', 'NVDA', 'TSLA', 'AMD', 'INTC'],
    'Finance': ['JPM', 'BAC', 'WFC', 'GS', 'BLK', 'SCHW'],
    'Healthcare': ['JNJ', 'UNH', 'PFE', 'ABBV', 'MRK', 'LLY'],
    'Consumer': ['WMT', 'HD', 'MCD', 'COST', 'AMZN'],
    'Energy': ['XOM', 'CVX', 'COP', 'MPC'],
    'Utilities': ['DUK', 'NEE', 'SO'],
    'ETFs/Index': ['SPY', 'IVV', 'VOO', 'QQQ', 'VTI', 'AGG', 'BND']
}

def _load_sector_groups():
    """
    Load sector mappings from sectors.json in the same directory as this file.
    Falls back to _DEFAULT_SECTOR_GROUPS if the file is missing or invalid.
    """
    sectors_path = os.path.join(os.path.dirname(__file__), 'sectors.json')
    try:
        with open(sectors_path, 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        return _DEFAULT_SECTOR_GROUPS
    except (json.JSONDecodeError, Exception) as e:
        print(f"Warning: could not load sectors.json ({e}), using built-in defaults.")
        return _DEFAULT_SECTOR_GROUPS

class PortfolioAnalytics:
    """
    Comprehensive portfolio analytics and risk metrics.
    """
    
    def __init__(self, consolidated_df, transactions_df=None, cash_flows_df=None, risk_free_rate=0.04):
        """
        Initialize portfolio analytics.

        Parameters:
        consolidated_df:  Consolidated holdings DataFrame from consolidate_holdings()
        transactions_df:  Full transaction history DataFrame (trades + cash flows)
                          from get_all_consolidated_transactions()
        cash_flows_df:    Deposit/withdrawal rows only, from get_cash_flows().
                          If None, derived automatically from transactions_df.
        risk_free_rate:   Annual risk-free rate for Sharpe/Sortino ratios (default 4%)
        """
        self.holdings = consolidated_df[consolidated_df['Symbol'] != 'CASH'].copy()
        self.cash = consolidated_df[consolidated_df['Symbol'] == 'CASH']['Market Value'].values
        self.cash = self.cash[0] if len(self.cash) > 0 else 0
        self.transactions = transactions_df if transactions_df is not None else pd.DataFrame()
        self.risk_free_rate = risk_free_rate

        # Cash flows: use explicit argument if provided, otherwise derive from transactions
        if cash_flows_df is not None:
            self.cash_flows = cash_flows_df
        elif not self.transactions.empty and 'Category' in self.transactions.columns:
            cf = self.transactions[self.transactions['Category'].isin(['Deposit', 'Withdrawal'])].copy()
            self.cash_flows = cf[['Date', 'Security Name', 'Total Value', 'Category']].rename(
                columns={'Security Name': 'Description'}
            ) if not cf.empty else pd.DataFrame()
        else:
            self.cash_flows = pd.DataFrame()
        
    def concentration_analysis(self):
        """
        Analyze portfolio concentration and diversification.
        
        Returns:
        dict: Concentration metrics
        """
        pct_of_portfolio = self.holdings['Percent of Portfolio'].values
        
        # Herfindahl-Hirschman Index (HHI) - measures concentration
        # HHI ranges from 1/N to 10000. Lower is more diversified
        hhi = (pct_of_portfolio ** 2).sum()
        
        # Effective number of positions
        effective_positions = 10000 / hhi if hhi > 0 else len(self.holdings)
        
        # Top 10 concentration
        sorted_pct = np.sort(pct_of_portfolio)[::-1]
        top_10_pct = sorted_pct[:10].sum()
        top_5_pct = sorted_pct[:5].sum()
        top_3_pct = sorted_pct[:3].sum()
        
        return {
            'HHI Score': round(hhi, 2),
            'HHI Interpretation': self._interpret_hhi(hhi),
            'Effective Positions': round(effective_positions, 2),
            'Total Positions': len(self.holdings),
            'Top 3 Holdings %': round(top_3_pct, 2),
            'Top 5 Holdings %': round(top_5_pct, 2),
            'Top 10 Holdings %': round(top_10_pct, 2),
            'Diversification Score': round((len(self.holdings) / effective_positions) * 100, 2)
        }
    
    def _interpret_hhi(self, hhi):
        """Interpret HHI concentration score."""
        if hhi < 1500:
            return "Well Diversified"
        elif hhi < 2500:
            return "Moderately Diversified"
        elif hhi < 5000:
            return "Concentrated"
        else:
            return "Highly Concentrated"
    
    def sector_analysis(self):
        """
        Basic sector-style analysis based on holdings.
        
        Returns:
        dict: Sector concentration metrics
        """
        sector_groups = _load_sector_groups()
        
        sector_allocation = {}
        for sector, symbols in sector_groups.items():
            sector_values = self.holdings[self.holdings['Symbol'].isin(symbols)]['Market Value'].sum()
            if sector_values > 0:
                sector_allocation[sector] = round(sector_values, 2)
        
        # Unclassified
        classified_symbols = [s for symbols in sector_groups.values() for s in symbols]
        unclassified = self.holdings[~self.holdings['Symbol'].isin(classified_symbols)]['Market Value'].sum()
        if unclassified > 0:
            sector_allocation['Other'] = round(unclassified, 2)
        
        # Calculate percentages
        total = sum(sector_allocation.values())
        sector_allocation_pct = {k: round((v/total)*100, 2) for k, v in sector_allocation.items()}
        
        return {
            'Sector Allocation ($)': sector_allocation,
            'Sector Allocation (%)': sector_allocation_pct
        }
    
    def performance_metrics(self):
        """
        Calculate key performance metrics including deposit-adjusted return.

        Simple return uses cost basis from the holdings DataFrame.
        Deposit-adjusted return uses the Modified Dietz method, which accounts
        for the timing of cash deposits and withdrawals so that contributions
        are not mistaken for investment gains.

        Modified Dietz formula:
            R = (EMV - BMV - CF) / (BMV + sum(CF_i * W_i))
        where:
            EMV  = ending market value (current portfolio value incl. cash)
            BMV  = beginning market value (approximated as total cost basis)
            CF   = net cash flows (deposits - withdrawals) over the period
            W_i  = weight of each cash flow = (T - t_i) / T
                   T = total days in period, t_i = days elapsed at flow date

        Returns:
        dict: Performance metrics
        """
        total_cost = self.holdings['Total Cost'].sum()
        total_market_value = self.holdings['Market Value'].sum() + self.cash
        total_gain = total_market_value - total_cost

        # Simple return (cost-basis based)
        simple_return_pct = (total_gain / total_cost * 100) if total_cost > 0 else 0

        result = {
            'Total Return ($)': round(total_gain, 2),
            'Total Return (%)': round(simple_return_pct, 2),
            'Total Cost Basis': round(total_cost, 2),
            'Current Market Value': round(total_market_value, 2),
            'Avg Cost Per Position': round(total_cost / len(self.holdings), 2) if len(self.holdings) > 0 else 0,
        }

        # Modified Dietz deposit-adjusted return
        if not self.cash_flows.empty:
            cf = self.cash_flows.copy()
            cf['Total Value'] = pd.to_numeric(cf['Total Value'], errors='coerce').fillna(0)

            # Deposits are positive; withdrawals should already be negative
            # (enforced by _classify_cash_flow). Force withdrawals negative just in case.
            cf.loc[cf['Category'] == 'Withdrawal', 'Total Value'] = \
                -cf.loc[cf['Category'] == 'Withdrawal', 'Total Value'].abs()

            total_deposits = cf.loc[cf['Category'] == 'Deposit', 'Total Value'].sum()
            total_withdrawals = cf.loc[cf['Category'] == 'Withdrawal', 'Total Value'].sum()  # negative
            net_cash_flow = total_deposits + total_withdrawals  # deposits - |withdrawals|

            # Date range: oldest cash flow to today
            cf['Date'] = pd.to_datetime(cf['Date'])
            period_start = cf['Date'].min()
            period_end = pd.Timestamp.now()
            total_days = max((period_end - period_start).days, 1)

            # Weighted cash flows: weight = fraction of period remaining after flow
            cf['Days Elapsed'] = (cf['Date'] - period_start).dt.days
            cf['Weight'] = (total_days - cf['Days Elapsed']) / total_days
            weighted_cf = (cf['Total Value'] * cf['Weight']).sum()

            # BMV approximated as total cost basis (we don't have a snapshot of
            # portfolio value at period start, so cost basis is the best proxy)
            bmv = total_cost
            denominator = bmv + weighted_cf

            if denominator != 0:
                modified_dietz_return = (total_market_value - bmv - net_cash_flow) / denominator * 100
            else:
                modified_dietz_return = 0

            result['Total Deposits'] = round(total_deposits, 2)
            result['Total Withdrawals'] = round(abs(total_withdrawals), 2)
            result['Net Cash Flows'] = round(net_cash_flow, 2)
            result['Deposit-Adjusted Return (%)'] = round(modified_dietz_return, 2)
            result['Deposit-Adjusted Return Note'] = (
                'Modified Dietz method. BMV approximated as total cost basis.'
            )
        else:
            result['Total Deposits'] = 0
            result['Total Withdrawals'] = 0
            result['Net Cash Flows'] = 0
            result['Deposit-Adjusted Return (%)'] = 'N/A - no cash flow data'

        return result

    def cash_flow_summary(self):
        """
        Summarise deposit and withdrawal activity.

        Returns:
        dict: Cash flow metrics, or a message if no data is available.
        """
        if self.cash_flows.empty:
            return {'Message': 'No cash flow data available'}

        cf = self.cash_flows.copy()
        cf['Total Value'] = pd.to_numeric(cf['Total Value'], errors='coerce').fillna(0)
        cf.loc[cf['Category'] == 'Withdrawal', 'Total Value'] = \
            -cf.loc[cf['Category'] == 'Withdrawal', 'Total Value'].abs()

        deposits = cf[cf['Category'] == 'Deposit']
        withdrawals = cf[cf['Category'] == 'Withdrawal']

        total_deposited = deposits['Total Value'].sum()
        total_withdrawn = abs(withdrawals['Total Value'].sum())
        net = total_deposited - total_withdrawn

        result = {
            'Number of Deposits': len(deposits),
            'Total Deposited': round(total_deposited, 2),
            'Number of Withdrawals': len(withdrawals),
            'Total Withdrawn': round(total_withdrawn, 2),
            'Net Cash Flow': round(net, 2),
        }

        if not deposits.empty:
            result['Largest Deposit'] = round(deposits['Total Value'].max(), 2)
            result['Most Recent Deposit'] = str(deposits['Date'].max().date())
        if not withdrawals.empty:
            result['Largest Withdrawal'] = round(abs(withdrawals['Total Value'].min()), 2)
            result['Most Recent Withdrawal'] = str(withdrawals['Date'].max().date())

        return result
    
    def risk_metrics(self):
        """
        Calculate portfolio risk metrics.
        
        Returns:
        dict: Risk metrics
        """
        gains_pct = self.holdings['Total Gain %'].values
        
        # Standard deviation of returns
        std_dev = np.std(gains_pct) if len(gains_pct) > 1 else 0
        
        # Downside deviation (only negative returns)
        downside_returns = gains_pct[gains_pct < 0]
        downside_dev = np.std(downside_returns) if len(downside_returns) > 0 else 0
        
        # Winning vs losing positions
        winning_positions = len(gains_pct[gains_pct > 0])
        losing_positions = len(gains_pct[gains_pct < 0])
        breakeven_positions = len(gains_pct[gains_pct == 0])
        
        # Win rate
        win_rate = (winning_positions / len(gains_pct) * 100) if len(gains_pct) > 0 else 0
        
        # Max gain and loss
        max_gain = gains_pct.max()
        max_loss = gains_pct.min()
        
        # Sharpe ratio (simplified - uses return %, not precise)
        avg_return = gains_pct.mean()
        sharpe_ratio = (avg_return - (self.risk_free_rate * 100)) / std_dev if std_dev > 0 else 0
        
        # Sortino ratio (uses downside deviation)
        sortino_ratio = (avg_return - (self.risk_free_rate * 100)) / downside_dev if downside_dev > 0 else 0
        
        return {
            'Volatility (Std Dev %)': round(std_dev, 2),
            'Downside Deviation %': round(downside_dev, 2),
            'Winning Positions': winning_positions,
            'Losing Positions': losing_positions,
            'Breakeven Positions': breakeven_positions,
            'Win Rate (%)': round(win_rate, 2),
            'Best Performer (%)': round(max_gain, 2),
            'Worst Performer (%)': round(max_loss, 2),
            'Sharpe Ratio': round(sharpe_ratio, 2),
            'Sortino Ratio': round(sortino_ratio, 2)
        }
    
    def liquidity_analysis(self):
        """
        Analyze portfolio liquidity and cash position.
        
        Returns:
        dict: Liquidity metrics
        """
        total_portfolio = self.holdings['Market Value'].sum() + self.cash
        
        cash_pct = (self.cash / total_portfolio * 100) if total_portfolio > 0 else 0
        
        # Determine liquidity score (0-100)
        # Higher cash = more liquid, but potentially underinvested
        if cash_pct > 30:
            liquidity_score = 85
        elif cash_pct > 20:
            liquidity_score = 80
        elif cash_pct > 10:
            liquidity_score = 75
        elif cash_pct > 5:
            liquidity_score = 70
        else:
            liquidity_score = 65
        
        return {
            'Cash Balance': round(self.cash, 2),
            'Cash Percentage': round(cash_pct, 2),
            'Total Portfolio Value': round(total_portfolio, 2),
            'Liquidity Score (0-100)': liquidity_score,
            'Liquidity Assessment': 'Healthy' if cash_pct >= 5 else 'Low - Consider rebalancing'
        }
    
    def transaction_analysis(self):
        """
        Analyze trading activity and turnover.
        
        Returns:
        dict: Transaction metrics
        """
        if self.transactions.empty:
            return {'Message': 'No transaction data available'}
        
        buys = self.transactions[self.transactions['Transaction Type'] == 'Bought']
        sells = self.transactions[self.transactions['Transaction Type'] == 'Sold']
        
        total_bought = buys['Total Value'].sum() if len(buys) > 0 else 0
        total_sold = sells['Total Value'].sum() if len(sells) > 0 else 0
        
        # Portfolio turnover (simplified)
        total_portfolio = self.holdings['Market Value'].sum()
        turnover_pct = ((total_bought + total_sold) / 2 / total_portfolio * 100) if total_portfolio > 0 else 0
        
        return {
            'Total Buys': len(buys),
            'Total Sells': len(sells),
            'Total Amount Bought': round(total_bought, 2),
            'Total Amount Sold': round(total_sold, 2),
            'Portfolio Turnover (%)': round(turnover_pct, 2),
            'Avg Transaction Size': round((total_bought + total_sold) / (len(buys) + len(sells)), 2) if (len(buys) + len(sells)) > 0 else 0
        }
    
    def holdings_quality_analysis(self):
        """
        Analyze quality and characteristics of individual holdings.
        
        Returns:
        dict: Holdings quality metrics
        """
        quality_metrics = {
            'Highly Profitable (>50%)': len(self.holdings[self.holdings['Total Gain %'] > 50]),
            'Profitable (0-50%)': len(self.holdings[(self.holdings['Total Gain %'] > 0) & (self.holdings['Total Gain %'] <= 50)]),
            'Breakeven': len(self.holdings[self.holdings['Total Gain %'] == 0]),
            'Small Loss (-10% to 0%)': len(self.holdings[(self.holdings['Total Gain %'] < 0) & (self.holdings['Total Gain %'] >= -10)]),
            'Moderate Loss (-50% to -10%)': len(self.holdings[(self.holdings['Total Gain %'] < -10) & (self.holdings['Total Gain %'] >= -50)]),
            'Major Loss (< -50%)': len(self.holdings[self.holdings['Total Gain %'] < -50])
        }
        
        result = {'Holdings Quality Distribution': quality_metrics}

        if not self.holdings.empty:
            best_performer = self.holdings.loc[self.holdings['Total Gain %'].idxmax()]
            worst_performer = self.holdings.loc[self.holdings['Total Gain %'].idxmin()]
            result['Best Performer'] = {
                'Symbol': best_performer['Symbol'],
                'Gain %': round(best_performer['Total Gain %'], 2),
                'Market Value': round(best_performer['Market Value'], 2)
            }
            result['Worst Performer'] = {
                'Symbol': worst_performer['Symbol'],
                'Gain %': round(worst_performer['Total Gain %'], 2),
                'Market Value': round(worst_performer['Market Value'], 2)
            }

        return result
    
    def generate_full_report(self):
        """
        Generate comprehensive analytics report.
        
        Returns:
        dict: Full analytics report
        """
        return {
            'Concentration Analysis': self.concentration_analysis(),
            'Sector Analysis': self.sector_analysis(),
            'Performance Metrics': self.performance_metrics(),
            'Cash Flow Summary': self.cash_flow_summary(),
            'Risk Metrics': self.risk_metrics(),
            'Liquidity Analysis': self.liquidity_analysis(),
            'Holdings Quality': self.holdings_quality_analysis(),
            'Transaction Analysis': self.transaction_analysis()
        }


def export_analytics_to_excel(consolidated_df, analytics_report, filename=None):
    """
    Export analytics report to Excel with formatting.
    
    Parameters:
    consolidated_df: Consolidated holdings DataFrame
    analytics_report: Full analytics report from generate_full_report()
    filename: Name of Excel file
    """
    import xlsxwriter
    
    if filename is None:
        current_date = datetime.now().strftime("%Y-%m-%d")
        filename = f'portfolio_analytics_{current_date}.xlsx'
    
    with pd.ExcelWriter(filename, engine='xlsxwriter') as writer:
        workbook = writer.book
        
        # Format definitions
        header_format = workbook.add_format({
            'bold': True,
            'bg_color': '#1F4E78',
            'font_color': 'white',
            'border': 1,
            'valign': 'vcenter'
        })
        
        section_format = workbook.add_format({
            'bold': True,
            'bg_color': '#D9E1F2',
            'border': 1
        })
        
        data_format = workbook.add_format({
            'border': 1,
            'valign': 'top'
        })
        
        currency_format = workbook.add_format({
            'num_format': '$#,##0.00',
            'border': 1
        })
        
        percent_format = workbook.add_format({
            'num_format': '0.00%',
            'border': 1
        })
        
        # Create summary sheet
        summary_sheet = workbook.add_worksheet('Analytics Summary')
        row = 0
        
        for section_name, section_data in analytics_report.items():
            # Section header
            summary_sheet.write(row, 0, section_name, section_format)
            summary_sheet.write(row, 1, '', section_format)
            row += 1

            if isinstance(section_data, dict):
                for key, value in section_data.items():
                    if isinstance(value, dict):
                        # Write the sub-section label, then each key/value on its own row indented
                        summary_sheet.write(row, 0, key, data_format)
                        summary_sheet.write(row, 1, '', data_format)
                        row += 1
                        for sub_key, sub_value in value.items():
                            summary_sheet.write(row, 0, f'    {sub_key}', data_format)
                            if isinstance(sub_value, (int, float)):
                                summary_sheet.write(row, 1, sub_value, data_format)
                            else:
                                summary_sheet.write(row, 1, str(sub_value), data_format)
                            row += 1
                    elif isinstance(value, list):
                        summary_sheet.write(row, 0, key, data_format)
                        summary_sheet.write(row, 1, '', data_format)
                        row += 1
                        for item in value:
                            if isinstance(item, dict):
                                for sub_key, sub_value in item.items():
                                    summary_sheet.write(row, 0, f'    {sub_key}', data_format)
                                    if isinstance(sub_value, (int, float)):
                                        summary_sheet.write(row, 1, sub_value, data_format)
                                    else:
                                        summary_sheet.write(row, 1, str(sub_value), data_format)
                                    row += 1
                            else:
                                summary_sheet.write(row, 0, '', data_format)
                                summary_sheet.write(row, 1, str(item), data_format)
                                row += 1
                    else:
                        summary_sheet.write(row, 0, key, data_format)
                        summary_sheet.write(row, 1, value, data_format)
                        row += 1

            row += 1
        
        summary_sheet.set_column(0, 0, 30)
        summary_sheet.set_column(1, 1, 40)
        
        # Create detailed holdings sheet with performance
        holdings_sheet = workbook.add_worksheet('Holdings Detail')
        
        holdings_display = consolidated_df[consolidated_df['Symbol'] != 'CASH'].copy()
        holdings_display.to_excel(writer, sheet_name='Holdings Detail', index=False, startrow=0)
        
        # Apply formatting
        for col_num, value in enumerate(holdings_display.columns.values):
            holdings_sheet.write(0, col_num, value, header_format)
        
        for i in range(len(holdings_display.columns)):
            holdings_sheet.set_column(i, i, 15)
    
    print(f"Analytics exported to {filename}")