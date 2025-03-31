import os
import pandas as pd
import pyetrade
from dotenv import load_dotenv
import datetime
import webbrowser
import warnings

# Suppress all warnings
warnings.filterwarnings('ignore')
def authenticate_etrade():
    """
    Authenticate with E*TRADE API using OAuth.
    
    Automatically opens the authorization URL in the default web browser.
    
    Returns:
    dict: OAuth tokens for API access
    """
    # Load environment variables from .env file
    load_dotenv()
    
    # Retrieve API credentials from environment variables
    consumer_key = os.getenv('CONSUMER_KEY')
    consumer_secret = os.getenv('CONSUMER_SECRET')
    
    # Initialize OAuth authentication
    oauth = pyetrade.ETradeOAuth(consumer_key, consumer_secret)
    
    # Get the request token URL
    request_token_url = oauth.get_request_token()
    
    # Open the authorization URL in the default web browser
    try:
        webbrowser.open(request_token_url)
        print("Authorization URL opened in your default web browser.")
        print("Please complete the authorization process in the browser.")
    except Exception as e:
        print(f"Could not open browser automatically. Please visit the URL manually: {request_token_url}")
        print(f"Error details: {e}")
    
    # Prompt user to enter verification code from the authorization URL
    verifier_code = input("Enter the verification code from the webpage: ")
    
    # Obtain access tokens
    tokens = oauth.get_access_token(verifier_code)
    
    return {
        'consumer_key': consumer_key,
        'consumer_secret': consumer_secret,
        'oauth_token': tokens['oauth_token'],
        'oauth_token_secret': tokens['oauth_token_secret']
    }

def fetch_active_accounts(auth_tokens):
    """
    Fetch active E*TRADE accounts for the authenticated user.
    
    Parameters:
    auth_tokens (dict): Authentication tokens from E*TRADE OAuth
    
    Returns:
    pandas.DataFrame: DataFrame of active accounts
    """
    # Initialize E*TRADE Accounts object
    accounts_obj = pyetrade.ETradeAccounts(
        auth_tokens['consumer_key'],
        auth_tokens['consumer_secret'],
        auth_tokens['oauth_token'],
        auth_tokens['oauth_token_secret'],
        dev=False
    )
    
    # Retrieve accounts in JSON format
    accounts = accounts_obj.list_accounts(resp_format='json')['AccountListResponse']['Accounts']['Account']
    
    # Convert to DataFrame and filter active accounts
    accounts_df = pd.DataFrame(accounts)
    active_accounts = accounts_df[accounts_df['accountStatus'] == 'ACTIVE']
    
    return active_accounts, accounts_obj

def get_portfolio(accounts_obj, account_id_key):
    """
    Retrieve portfolio details for a specific account.
    
    Parameters:
    accounts_obj (pyetrade.ETradeAccounts): E*TRADE Accounts object
    account_id_key (str): Unique account identifier
    
    Returns:
    pandas.DataFrame: Portfolio holdings for the account
    """
    # Fetch complete portfolio for the account
    port = accounts_obj.get_account_portfolio(account_id_key, resp_format='json', view='Complete')
    
    # Extract portfolio data
    data = eval(str(port['PortfolioResponse']['AccountPortfolio']))[0]
    positions = data['Position']
    
    # Extract detailed position information
    portfolio_data = []
    for position in positions:
        portfolio_data.append({
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
            'Percent of Portfolio': position['pctOfPortfolio']
        })

    return pd.DataFrame(portfolio_data)

def get_cash_balance(accounts_obj, account_id_key):
    """
    Retrieve cash balance for a specific account.
    
    Parameters:
    accounts_obj (pyetrade.ETradeAccounts): E*TRADE Accounts object
    account_id_key (str): Unique account identifier
    
    Returns:
    float: Net cash balance for the account
    """
    return accounts_obj.get_account_balance(account_id_key, resp_format='json')['BalanceResponse']['Computed']['netCash']

def consolidate_holdings(df, cash=0):
    """
    Consolidate holdings across multiple portfolios.
    
    Parameters:
    df (pandas.DataFrame): DataFrame containing portfolio holdings
    cash (float, optional): Total cash amount in the portfolio
    
    Returns:
    pandas.DataFrame: Consolidated holdings with cash as final row
    """
    # Group by symbol and aggregate key metrics
    consolidated = df.groupby('Symbol').agg({
        'Quantity': 'sum',
        'Total Cost': 'sum',
        'Market Value': 'sum',
        'Symbol Description': 'first',
        'Date Acquired': 'min',
        'Current Price': 'max'
    }).reset_index()
    
    # Calculate weighted average price paid
    consolidated['Price Paid'] = consolidated['Total Cost'] / consolidated['Quantity']
    
    # Calculate total gain and total gain percentage
    consolidated['Total Gain'] = consolidated['Market Value'] - consolidated['Total Cost']
    consolidated['Total Gain %'] = (consolidated['Total Gain'] / consolidated['Total Cost']) * 100
    
    # Calculate percent of total portfolio
    total_portfolio_value = consolidated['Market Value'].sum()
    consolidated['Percent of Portfolio'] = (consolidated['Market Value'] / total_portfolio_value) * 100
    
    # Round numeric columns for readability
    numeric_columns = ['Quantity', 'Price Paid', 'Total Cost', 'Market Value', 
                       'Total Gain', 'Total Gain %', 'Percent of Portfolio']
    for col in numeric_columns:
        consolidated[col] = consolidated[col].round(2)
    
    # Sort by market value in descending order
    consolidated = consolidated.sort_values('Market Value', ascending=False)
    
    # Prepare the final DataFrame with selected columns
    result_df = consolidated[['Symbol', 'Symbol Description', 'Current Price', 'Quantity', 
                               'Date Acquired', 'Price Paid', 'Total Cost', 'Market Value', 
                               'Total Gain', 'Total Gain %', 'Percent of Portfolio']]
    
    # Add cash as the final row if cash balance exists
    if cash > 0:
        cash_row = pd.DataFrame({
            'Symbol': ['CASH'],
            'Symbol Description': ['Cash'],
            'Current Price': [1.0],
            'Quantity': [cash],
            'Date Acquired': pd.NaT,
            'Price Paid': [1.0],
            'Total Cost': [cash],
            'Market Value': [cash],
            'Total Gain': [0],
            'Total Gain %': [0],
            'Percent of Portfolio': [(cash / (total_portfolio_value + cash)) * 100]
        })
        
        # Concatenate the original DataFrame with the cash row
        result_df = pd.concat([result_df, cash_row], ignore_index=True)
    
    return result_df

def portfolio_summary(consolidated_df, cash=0):
    """
    Generate a summary of the consolidated portfolio.
    
    Parameters:
    consolidated_df (pandas.DataFrame): Consolidated holdings DataFrame
    cash (float, optional): Total cash amount in the portfolio
    
    Returns:
    dict: Portfolio summary statistics
    """
    # Calculate total market value of stocks (excluding cash)
    total_stock_value = consolidated_df[consolidated_df['Symbol'] != 'CASH']['Market Value'].sum()
    
    # Total portfolio value includes cash
    total_portfolio_value = total_stock_value + cash
    
    # Calculate total cost basis of stocks (excluding cash)
    total_cost_basis = consolidated_df[consolidated_df['Symbol'] != 'CASH']['Total Cost'].sum()
    
    # Calculate total unrealized gain (excluding cash)
    total_unrealized_gain = consolidated_df[consolidated_df['Symbol'] != 'CASH']['Total Gain'].sum()
    
    # Calculate total unrealized gain percentage
    total_unrealized_gain_pct = (total_unrealized_gain / total_cost_basis) * 100 if total_cost_basis != 0 else 0
    
    return {
        'Total Stocks': len(consolidated_df[consolidated_df['Symbol'] != 'CASH']),
        'Total Stock Market Value': total_stock_value,
        'Cash': cash,
        'Total Portfolio Value': total_portfolio_value,
        'Total Cost Basis': total_cost_basis,
        'Total Unrealized Gain': total_unrealized_gain,
        'Total Unrealized Gain %': total_unrealized_gain_pct,
        'Cash Percentage': (cash / total_portfolio_value) * 100 if total_portfolio_value > 0 else 0,
        'Largest Holdings': consolidated_df[consolidated_df['Symbol'] != 'CASH'].head(3)[['Symbol', 'Market Value', 'Percent of Portfolio']].to_dict('records')
    }

def add_totals_row(consolidated_df):
    """
    Add a totals row to the consolidated DataFrame.
    
    Parameters:
    consolidated_df (pandas.DataFrame): Consolidated holdings DataFrame
    
    Returns:
    pandas.DataFrame: DataFrame with added totals row
    """
    # Exclude the cash row if it exists
    stock_df = consolidated_df[consolidated_df['Symbol'] != 'CASH']
    cash_row = consolidated_df[consolidated_df['Symbol'] == 'CASH']
    
    # Calculate totals
    totals_row = pd.DataFrame({
        'Symbol': ['TOTAL'],
        'Symbol Description': ['Portfolio Total'],
        'Current Price': ['-'],
        'Quantity': [stock_df['Quantity'].sum()],
        'Date Acquired': [stock_df['Date Acquired'].min()],
        'Price Paid': ['-'],
        'Total Cost': [stock_df['Total Cost'].sum()],
        'Market Value': [stock_df['Market Value'].sum()],
        'Total Gain': [stock_df['Total Gain'].sum()],
        'Total Gain %': [(stock_df['Total Gain'].sum() / stock_df['Total Cost'].sum()) * 100],
        'Percent of Portfolio': [100.0]
    })
    
    # If cash exists, add it to the final DataFrame
    if not cash_row.empty:
        consolidated_df = pd.concat([consolidated_df, totals_row, cash_row], ignore_index=True)
    else:
        consolidated_df = pd.concat([consolidated_df, totals_row], ignore_index=True)
    
    return consolidated_df

# Add the new transaction functions from new.py
def get_consolidated_transactions(accounts_obj, account_id_key, start_date, end_date):
    """
    Retrieves and consolidates transaction data into a Pandas DataFrame.

    Args:
        accounts_obj: An instance of the API wrapper object with list_transactions and list_transaction_details methods.
        account_id_key: The unique identifier for the brokerage account.
        start_date: The start date for the transaction history (datetime.date object).
        end_date: The end date for the transaction history (datetime.date object).

    Returns:
        A Pandas DataFrame containing the consolidated transaction data.
    """
    # Ensure both dates are datetime.date objects
    if not isinstance(start_date, datetime.date):
        raise TypeError("start_date must be a datetime.date object")
    if not isinstance(end_date, datetime.date):
        raise TypeError("end_date must be a datetime.date object")
        
    transaction_list_response = accounts_obj.list_transactions(account_id_key, start_date, end_date)
    transactions = transaction_list_response.get('TransactionListResponse', {}).get('Transaction', [])

    if not transactions:
        return pd.DataFrame()
        
    transaction_data = []
    for transaction in transactions:
        if transaction['transactionType'] in ['Bought', 'Sold']:
            transaction_id = transaction['transactionId']
            transaction_details_response = accounts_obj.list_transaction_details(account_id_key, transaction_id)
            transaction_details = transaction_details_response.get('TransactionDetailsResponse', {}).get('Brokerage', {})
            product_info = transaction.get('brokerage', {}).get('product', {})

            transaction_date = pd.to_datetime(transaction['transactionDate'], unit='ms')
            security_name = transaction['description']
            quantity = transaction_details.get('quantity')
            price = transaction_details.get('price')

            # Ensure quantity and price are not None before calculating total value
            if quantity is not None and price is not None:
                try:
                    quantity = float(quantity)
                    price = float(price)
                    total_value = quantity * price
                except ValueError:
                    total_value = None
            else:
                total_value = None

            transaction_data.append({
                'Date': transaction_date,
                'Security Name': security_name,
                'Quantity': quantity,
                'Price': price,
                'Total Value': total_value,
                'Transaction Type': transaction['transactionType']
            })

    df = pd.DataFrame(transaction_data)
    return df

def get_all_consolidated_transactions(accounts_obj, active_accounts, start_date, end_date):
    """
    Get all transactions across all accounts.
    
    Args:
        accounts_obj: E*TRADE Accounts object
        active_accounts: DataFrame of active accounts
        start_date: Start date for transaction history (datetime.date object)
        end_date: End date for transaction history (datetime.date object)
        
    Returns:
        DataFrame: Combined transactions from all accounts
    """
    # Ensure dates are datetime.date objects
    if not isinstance(start_date, datetime.date):
        raise TypeError("start_date must be a datetime.date object")
    if not isinstance(end_date, datetime.date):
        raise TypeError("end_date must be a datetime.date object")
        
    out = pd.DataFrame()
    for key in active_accounts['accountIdKey']:
        account_transactions = get_consolidated_transactions(accounts_obj, key, start_date, end_date)
        out = pd.concat([out, account_transactions])

    # Sort by date with most recent first
    if not out.empty:
        out = out.sort_values('Date', ascending=False).reset_index(drop=True)
        
    return out

def export_to_excel(consolidated_df, transactions_df=None, filename=None, summary_spacing=3):
    """
    Export consolidated holdings DataFrame to an Excel file with formatting.
    
    Parameters:
    consolidated_df (pandas.DataFrame): Consolidated holdings DataFrame
    transactions_df (pandas.DataFrame, optional): Transactions DataFrame to include
    filename (str, optional): Name of the Excel file to save
    summary_spacing (int, optional): Number of blank rows before the summary section
    """
    # Generate filename with current date if not provided
    if filename is None:
        current_date = datetime.datetime.now().strftime("%Y-%m-%d")
        filename = f'portfolio_consolidated_{current_date}.xlsx'
    
    # Create a Pandas Excel writer using XlsxWriter as the engine
    with pd.ExcelWriter(filename, engine='xlsxwriter') as writer:
        # Write the main DataFrame without the totals
        main_df = consolidated_df.copy()
        
        # Separate out the cash row if it exists
        cash_df = main_df[main_df['Symbol'] == 'CASH']
        main_df = main_df[main_df['Symbol'] != 'CASH']
        
        # Write just the main holdings (no cash, no totals)
        main_df.to_excel(writer, sheet_name='Holdings', index=False)
        
        # Get the workbook and worksheet objects
        workbook = writer.book
        worksheet = writer.sheets['Holdings']
        
        # Formatting styles
        currency_format = workbook.add_format({
            'num_format': '$#,##0.00',
            'align': 'right'
        })
        
        percent_format = workbook.add_format({
            'num_format': '0.00%',
            'align': 'right'
        })
        
        header_format = workbook.add_format({
            'bold': True,
            'bg_color': '#D3D3D3',  # Light gray background
            'border': 1
        })
        
        total_row_format = workbook.add_format({
            'bold': True,
            'bg_color': '#E6F2FF',  # Light blue background
            'border': 1
        })
        
        total_row_currency_format = workbook.add_format({
            'bold': True,
            'bg_color': '#E6F2FF',
            'num_format': '$#,##0.00',
            'align': 'right',
            'border': 1
        })
        
        total_row_percent_format = workbook.add_format({
            'bold': True,
            'bg_color': '#E6F2FF',
            'num_format': '0.00%',
            'align': 'right',
            'border': 1
        })
        
        # Apply format to the header row
        for col_num, value in enumerate(main_df.columns.values):
            worksheet.write(0, col_num, value, header_format)
        
        # Apply currency formatting
        worksheet.set_column('F:H', 12, currency_format)  # Price Paid, Total Cost, Market Value
        worksheet.set_column('I:I', 12, currency_format)  # Total Gain
        
        # Apply percentage formatting
        worksheet.set_column('J:K', 12, percent_format)  # Total Gain %, Percent of Portfolio
        
        # Calculate row positions
        last_data_row = len(main_df)
        summary_start_row = last_data_row + summary_spacing
        
        # Write summary section header
        worksheet.write(summary_start_row, 0, "PORTFOLIO SUMMARY", header_format)
        
        # Calculate totals
        total_quantity = main_df['Quantity'].sum()
        total_cost = main_df['Total Cost'].sum()
        total_market_value = main_df['Market Value'].sum()
        total_gain = main_df['Total Gain'].sum()
        total_gain_pct = (total_gain / total_cost) * 100 if total_cost > 0 else 0
        
        # Write totals row with header row
        summary_header_row = summary_start_row + 1
        summary_data_row = summary_header_row + 1
        
        # Add header for summary table
        worksheet.write(summary_header_row, 0, "Description", header_format)
        worksheet.write(summary_header_row, 1, "Value", header_format)
        
        # Add summary data
        worksheet.write(summary_data_row, 0, "Total Stocks", total_row_format)
        worksheet.write(summary_data_row, 1, main_df.shape[0], total_row_format)
        
        worksheet.write(summary_data_row + 1, 0, "Total Quantity", total_row_format)
        worksheet.write(summary_data_row + 1, 1, total_quantity, total_row_format)
        
        worksheet.write(summary_data_row + 2, 0, "Total Cost", total_row_format)
        worksheet.write(summary_data_row + 2, 1, total_cost, total_row_currency_format)
        
        worksheet.write(summary_data_row + 3, 0, "Total Market Value", total_row_format)
        worksheet.write(summary_data_row + 3, 1, total_market_value, total_row_currency_format)
        
        worksheet.write(summary_data_row + 4, 0, "Total Gain/Loss", total_row_format)
        worksheet.write(summary_data_row + 4, 1, total_gain, total_row_currency_format)
        
        worksheet.write(summary_data_row + 5, 0, "Total Gain/Loss %", total_row_format)
        worksheet.write(summary_data_row + 5, 1, total_gain_pct / 100, total_row_percent_format)
        
        # Add cash if it exists
        if not cash_df.empty:
            cash_value = cash_df['Market Value'].values[0]
            worksheet.write(summary_data_row + 6, 0, "Cash", total_row_format)
            worksheet.write(summary_data_row + 6, 1, cash_value, total_row_currency_format)
            
            worksheet.write(summary_data_row + 7, 0, "Total Portfolio Value", total_row_format)
            worksheet.write(summary_data_row + 7, 1, total_market_value + cash_value, total_row_currency_format)
            
            cash_percentage = (cash_value / (total_market_value + cash_value)) * 100
            worksheet.write(summary_data_row + 8, 0, "Cash %", total_row_format)
            worksheet.write(summary_data_row + 8, 1, cash_percentage / 100, total_row_percent_format)
        
        # Auto-adjust column widths for the main table
        for i, col in enumerate(main_df.columns):
            column_len = max(main_df[col].astype(str).map(len).max(), len(col))
            worksheet.set_column(i, i, column_len + 2)
        
        # Adjust summary columns
        worksheet.set_column(0, 0, 20)  # Summary description column
        worksheet.set_column(1, 1, 15)  # Summary value column
        
        # Add Transactions sheet if transactions data is provided
        if transactions_df is not None and not transactions_df.empty:
            # Write transactions to a new sheet
            transactions_df.to_excel(writer, sheet_name='Transactions', index=False)
            
            # Get the transactions worksheet
            trans_worksheet = writer.sheets['Transactions']
            
            # Apply formatting to transactions sheet
            for col_num, value in enumerate(transactions_df.columns.values):
                trans_worksheet.write(0, col_num, value, header_format)
            
            # Format date column
            date_format = workbook.add_format({'num_format': 'yyyy-mm-dd'})
            trans_worksheet.set_column('A:A', 12, date_format)  # Date column
            
            # Format currency columns
            if 'Price' in transactions_df.columns:
                price_col = transactions_df.columns.get_loc('Price')
                trans_worksheet.set_column(price_col, price_col, 12, currency_format)
            
            if 'Total Value' in transactions_df.columns:
                value_col = transactions_df.columns.get_loc('Total Value')
                trans_worksheet.set_column(value_col, value_col, 12, currency_format)
            
            # Auto-adjust column widths for transactions table
            for i, col in enumerate(transactions_df.columns):
                column_len = max(transactions_df[col].astype(str).map(len).max(), len(col))
                trans_worksheet.set_column(i, i, column_len + 2)
    
    print(f"Portfolio exported successfully to {filename}")

def main():
    """
    Main function to orchestrate portfolio consolidation and export.
    """
    try:
        # Authenticate with E*TRADE
        auth_tokens = authenticate_etrade()
        
        # Fetch active accounts
        active_accounts, accounts_obj = fetch_active_accounts(auth_tokens)
        
        # Consolidate portfolio across all active accounts
        combined_portfolio = pd.DataFrame()
        total_cash = 0
        
        for key in active_accounts['accountIdKey']:
            # Fetch portfolio for each account
            account_portfolio = get_portfolio(accounts_obj, key)
            combined_portfolio = pd.concat([combined_portfolio, account_portfolio])
            
            # Accumulate cash balances
            total_cash += get_cash_balance(accounts_obj, key)
        
        # Sort and reset index of combined portfolio
        combined_portfolio = combined_portfolio.sort_values(by='Symbol Description').reset_index(drop=True)
        
        # Consolidate holdings and generate summary
        consolidated_portfolio = consolidate_holdings(combined_portfolio, total_cash)
        summary = portfolio_summary(consolidated_portfolio, total_cash)
        
        # Get transaction data for the last month using datetime.date objects
        today = datetime.date.today()
        # Start from the first day of the previous month
        start_date = datetime.date(2025,1,1)#(today.year, today.month-1 if today.month > 1 else 12, 1)
        end_date = today
        
        print(f"Fetching transactions from {start_date} to {end_date}...")
        transaction_data = get_all_consolidated_transactions(accounts_obj, active_accounts, start_date, end_date)
        
        # Export to Excel (with transactions data)
        export_to_excel(consolidated_portfolio, transaction_data)
        
        # Print portfolio summary
        print("\nPortfolio Summary:")
        for key, value in summary.items():
            print(f"{key}: {value}")
        
        print(f"\nTransactions: Retrieved {len(transaction_data)} transactions from the last month")
    
    except Exception as e:
        print(f"An error occurred: {e}")

if __name__ == "__main__":
    main()