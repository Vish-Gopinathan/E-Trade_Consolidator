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
    account_portfolio = port['PortfolioResponse']['AccountPortfolio']
    data = account_portfolio[0] if isinstance(account_portfolio, list) else account_portfolio
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
    
    # Rebuild: stocks + totals row, then re-append cash at the end if present
    if not cash_row.empty:
        consolidated_df = pd.concat([stock_df, totals_row, cash_row], ignore_index=True)
    else:
        consolidated_df = pd.concat([stock_df, totals_row], ignore_index=True)
    
    return consolidated_df

# Transaction type constants
#
# Category definitions:
#   Trade    - security buy/sell (Bought, Sold)
#   Deposit  - external money entering the brokerage (ACH in, wire in, IRA contribution)
#   Withdrawal - external money leaving the brokerage (ACH out, wire out, IRA distribution)
#   Income   - dividends, interest, fees — money generated within the account, NOT an
#              external transfer and should NOT affect deposit-adjusted return calculations
#   Internal - inter-account transfers within E*TRADE (Journal entries, internal moves)
#              these are excluded from all cash flow reporting
#   Other    - anything unrecognised

TRADE_TYPES = ['Bought', 'Sold']

# Types that are always income, never external cash flows
INCOME_TYPES = ['Dividend', 'Interest', 'Fee', 'Refund']

# Types that are unambiguously external cash movements (sign of amount determines direction)
EXTERNAL_CASH_TYPES = ['Electronic Funds Transfer', 'Check', 'Wire', 'ACH',
                       'Contribution', 'Distribution']

# Types that require description-based disambiguation (Transfer can be external ACH
# or internal inter-account move; Journal is almost always internal bookkeeping)
AMBIGUOUS_TYPES = ['Transfer', 'Journal']

# Keywords in the description that indicate an EXTERNAL transfer.
# 'ach deposit' and 'ach withdrawal' are the exact labels E*TRADE uses for
# bank transfers — list them first so they match before shorter substrings.
_EXTERNAL_DESCRIPTION_KEYWORDS = ['ach deposit', 'ach withdrawal', 'ach', 'electronic funds',
                                   'wire', 'external', 'bank transfer',
                                   'deposit from bank', 'withdrawal to bank']

# Keywords in the description that indicate an INTERNAL transfer (exclude from cash flows)
_INTERNAL_DESCRIPTION_KEYWORDS = ['internal', 'journal', 'inter-account', 'transfer from acct',
                                   'transfer to acct', 'transfer between']

def _date_chunks(start_date, end_date, chunk_days=89):
    """
    Split [start_date, end_date] into consecutive windows of at most chunk_days.

    The E*TRADE API rejects list_transactions requests that span more than 90
    days, so we break long ranges into 89-day windows (one day of margin) and
    concatenate the results.

    Args:
        start_date: datetime.date — start of full range (inclusive).
        end_date:   datetime.date — end of full range (inclusive).
        chunk_days: Maximum window size in days (default 89).

    Yields:
        (chunk_start, chunk_end) pairs of datetime.date objects.
    """
    current = start_date
    delta = datetime.timedelta(days=chunk_days)
    while current <= end_date:
        chunk_end = min(current + delta, end_date)
        yield current, chunk_end
        current = chunk_end + datetime.timedelta(days=1)


def get_consolidated_transactions(accounts_obj, account_id_key, start_date, end_date):
    """
    Retrieves all transactions for one account into a single DataFrame.

    Includes both trade transactions (Bought/Sold) and cash flow transactions
    (deposits, withdrawals, transfers). All rows share the same schema; columns
    that don't apply to a given transaction type are left as None.

    The E*TRADE API limits each list_transactions call to a 90-day window.
    This function automatically splits longer date ranges into 89-day chunks
    and merges the results, so callers can pass any date range without
    worrying about the API limit.

    Columns returned:
        Date             - transaction date
        Security Name    - description from API
        Quantity         - shares (trades only)
        Price            - price per share (trades only)
        Total Value      - total dollar amount (positive = money in, negative = money out)
        Transaction Type - original type string from API
        Category         - 'Trade', 'Deposit', 'Withdrawal', 'Income', 'Internal', or 'Other'

    Args:
        accounts_obj: pyetrade.ETradeAccounts instance.
        account_id_key: Unique account identifier string.
        start_date: datetime.date — start of range (inclusive).
        end_date: datetime.date — end of range (inclusive).

    Returns:
        pandas.DataFrame of all transactions, sorted newest-first.
    """
    if not isinstance(start_date, datetime.date):
        raise TypeError("start_date must be a datetime.date object")
    if not isinstance(end_date, datetime.date):
        raise TypeError("end_date must be a datetime.date object")

    # Collect transactions across all 89-day windows
    all_transactions = []
    for chunk_start, chunk_end in _date_chunks(start_date, end_date):
        try:
            resp = accounts_obj.list_transactions(
                account_id_key, chunk_start, chunk_end, resp_format='json'
            )
            chunk_txns = resp.get('TransactionListResponse', {}).get('Transaction', [])
            if chunk_txns:
                all_transactions.extend(chunk_txns)
        except Exception as e:
            print(f"   Warning: failed to fetch transactions {chunk_start}–{chunk_end}: {e}")

    transactions = all_transactions

    if not transactions:
        return pd.DataFrame()

    transaction_data = []
    for transaction in transactions:
        t_type = transaction.get('transactionType', '')
        t_date = pd.to_datetime(transaction['transactionDate'], unit='ms')
        description = transaction.get('description', '')
        amount = transaction.get('amount')  # top-level amount field present on all transaction types

        if t_type in TRADE_TYPES:
            # Trades: fetch details for quantity and price
            transaction_id = transaction['transactionId']
            try:
                details_response = accounts_obj.list_transaction_details(
                    account_id_key, transaction_id, resp_format='json'
                )
                details = details_response.get('TransactionDetailsResponse', {}).get('Brokerage', {})
            except Exception:
                details = {}

            quantity = details.get('quantity')
            price = details.get('price')

            if quantity is not None and price is not None:
                try:
                    quantity = float(quantity)
                    price = float(price)
                    total_value = quantity * price
                except ValueError:
                    total_value = None
            else:
                total_value = float(amount) if amount is not None else None

            transaction_data.append({
                'Date': t_date,
                'Security Name': description,
                'Quantity': quantity,
                'Price': price,
                'Total Value': total_value,
                'Transaction Type': t_type,
                'Category': 'Trade'
            })

        else:
            # Everything that isn't a trade: income, external cash flows, internal transfers
            try:
                dollar_amount = float(amount) if amount is not None else None
            except (ValueError, TypeError):
                dollar_amount = None

            category = _classify_non_trade(t_type, description, dollar_amount)

            # Debug: print non-trade transactions so classification can be verified
            print(f"   [TXN] type='{t_type}' | desc='{description}' | "
                  f"amount={dollar_amount} | → {category}")

            # Skip pure internal bookkeeping entries — they are not meaningful cash flows
            if category == 'Internal':
                continue

            transaction_data.append({
                'Date': t_date,
                'Security Name': description,
                'Quantity': None,
                'Price': None,
                'Total Value': dollar_amount,
                'Transaction Type': t_type,
                'Category': category
            })

    if not transaction_data:
        return pd.DataFrame()

    df = pd.DataFrame(transaction_data)
    df = df.sort_values('Date', ascending=False).reset_index(drop=True)
    return df


def _classify_non_trade(t_type, description, amount):
    """
    Classify a non-trade transaction into one of:
      'Deposit'    - external money entering the brokerage
      'Withdrawal' - external money leaving the brokerage
      'Income'     - dividends, interest, fees (internal to the account)
      'Internal'   - inter-account transfers within E*TRADE (excluded from reporting)
      'Other'      - unrecognised type

    Classification priority:
      1. Income types are always Income regardless of description.
      2. Ambiguous types (Transfer, Journal) are resolved by description keywords:
         - External keywords → Deposit/Withdrawal by amount sign
         - Internal keywords → Internal (will be excluded)
         - Journal with no external signal → Internal
      3. Unambiguous external types (Electronic Funds Transfer, Wire, ACH, Check,
         Contribution, Distribution) are classified by type name and amount sign.
      4. Anything else with deposit/withdrawal keywords in the description is
         classified by amount sign.
      5. Fallthrough → Other.
    """
    t_lower = t_type.lower()
    desc_lower = description.lower()
    combined = t_lower + ' ' + desc_lower

    # --- 1. Income types ---
    if t_type in INCOME_TYPES:
        return 'Income'

    # --- 2. Ambiguous types: resolve by description ---
    if t_type in AMBIGUOUS_TYPES:
        has_external_signal = any(kw in combined for kw in _EXTERNAL_DESCRIPTION_KEYWORDS)
        has_internal_signal = any(kw in combined for kw in _INTERNAL_DESCRIPTION_KEYWORDS)

        # Journal with no external signal → internal bookkeeping
        if t_lower == 'journal' and not has_external_signal:
            return 'Internal'

        if has_internal_signal and not has_external_signal:
            return 'Internal'

        if has_external_signal:
            return _direction_by_amount(amount)

        # Transfer/Journal with no clear signal: use amount sign as fallback,
        # but flag as 'Other' so it doesn't silently inflate deposit totals
        return 'Other'

    # --- 3. Unambiguous external types ---
    if t_type in EXTERNAL_CASH_TYPES:
        # IRA distributions are always outflows
        if t_lower == 'distribution':
            return 'Withdrawal'
        # IRA contributions are always inflows
        if t_lower == 'contribution':
            return 'Deposit'
        # For all others, amount sign is definitive
        return _direction_by_amount(amount)

    # --- 4. Catch-all: description keyword match ---
    # Also match bare 'deposit' / 'withdrawal' words anywhere in the description
    # (e.g. E*TRADE description "ACH DEPOSIT" on a Transfer-type transaction).
    _catch_all_keywords = _EXTERNAL_DESCRIPTION_KEYWORDS + ['deposit', 'withdrawal', 'withdrawl']
    if any(kw in combined for kw in _catch_all_keywords):
        return _direction_by_amount(amount)

    return 'Other'


def _direction_by_amount(amount):
    """Return 'Deposit' for positive amounts, 'Withdrawal' for negative."""
    if amount is None:
        return 'Other'
    return 'Deposit' if amount >= 0 else 'Withdrawal'

def get_all_consolidated_transactions(accounts_obj, active_accounts, start_date, end_date):
    """
    Get all transactions (trades + cash flows) across all active accounts.

    Args:
        accounts_obj: E*TRADE Accounts object
        active_accounts: DataFrame of active accounts
        start_date: Start date for transaction history (datetime.date object)
        end_date: End date for transaction history (datetime.date object)

    Returns:
        DataFrame: Combined transactions from all accounts, sorted newest-first.
                   Columns: Date, Security Name, Quantity, Price, Total Value,
                             Transaction Type, Category
    """
    if not isinstance(start_date, datetime.date):
        raise TypeError("start_date must be a datetime.date object")
    if not isinstance(end_date, datetime.date):
        raise TypeError("end_date must be a datetime.date object")

    out = pd.DataFrame()
    for key in active_accounts['accountIdKey']:
        account_transactions = get_consolidated_transactions(accounts_obj, key, start_date, end_date)
        out = pd.concat([out, account_transactions])

    if not out.empty:
        out = out.sort_values('Date', ascending=False).reset_index(drop=True)

    return out


def get_cash_flows(transaction_df):
    """
    Extract deposit and withdrawal rows from a full transaction DataFrame.

    This is a convenience filter over the output of get_all_consolidated_transactions.
    It returns only rows where Category is 'Deposit' or 'Withdrawal', which are
    the inputs needed for deposit-adjusted return calculations (Modified Dietz).

    Args:
        transaction_df: DataFrame returned by get_all_consolidated_transactions.

    Returns:
        DataFrame with columns: Date, Description, Total Value, Category
        Positive Total Value = money coming into the portfolio (deposit).
        Negative Total Value = money leaving the portfolio (withdrawal).
        Only includes Category == 'Deposit' or 'Withdrawal'. Income (dividends,
        interest), Internal transfers, Trades, and Other are excluded.
        Returns empty DataFrame if no external cash flow rows exist.
    """
    if transaction_df.empty or 'Category' not in transaction_df.columns:
        return pd.DataFrame(columns=['Date', 'Description', 'Total Value', 'Category'])

    cash_flows = transaction_df[transaction_df['Category'].isin(['Deposit', 'Withdrawal'])].copy()

    if cash_flows.empty:
        return pd.DataFrame(columns=['Date', 'Description', 'Total Value', 'Category'])

    result = cash_flows[['Date', 'Security Name', 'Total Value', 'Category']].copy()
    result = result.rename(columns={'Security Name': 'Description'})
    result = result.sort_values('Date', ascending=False).reset_index(drop=True)
    return result

def export_to_excel(consolidated_df, transactions_df=None, cash_flows_df=None,
                    income_df=None, filename=None, summary_spacing=3):
    """
    Export consolidated holdings DataFrame to an Excel file with formatting.

    Parameters:
    consolidated_df (pandas.DataFrame): Consolidated holdings DataFrame
    transactions_df (pandas.DataFrame, optional): Full transactions DataFrame (trades + cash flows)
    cash_flows_df (pandas.DataFrame, optional): Deposit/withdrawal rows for the Cash Flows sheet
    income_df (pandas.DataFrame, optional): Income rows (dividends, interest) for the Income sheet
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
        
        # Data columns store whole-number percentages (e.g. 21.47 means 21.47%).
        # Excel's '0.00%' format multiplies by 100, so we use '0.00' with the
        # column header making the unit clear. The summary section divides by 100
        # before writing and correctly uses '0.00%' there.
        percent_format = workbook.add_format({
            'num_format': '0.00',
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

        # Add Cash Flows sheet if cash flow data is provided
        if cash_flows_df is not None and not cash_flows_df.empty:
            cash_flows_df.to_excel(writer, sheet_name='Cash Flows', index=False)

            cf_worksheet = writer.sheets['Cash Flows']

            # Header formatting
            for col_num, value in enumerate(cash_flows_df.columns.values):
                cf_worksheet.write(0, col_num, value, header_format)

            # Date and currency formatting
            date_format = workbook.add_format({'num_format': 'yyyy-mm-dd'})
            cf_worksheet.set_column('A:A', 14, date_format)   # Date

            # Colour-code rows: green for deposits, red for withdrawals
            green_format = workbook.add_format({
                'bg_color': '#E2EFDA', 'border': 1, 'num_format': '$#,##0.00', 'align': 'right'
            })
            red_format = workbook.add_format({
                'bg_color': '#FCE4D6', 'border': 1, 'num_format': '$#,##0.00', 'align': 'right'
            })

            value_col_idx = list(cash_flows_df.columns).index('Total Value')
            category_col_idx = list(cash_flows_df.columns).index('Category')

            for row_idx, (_, row) in enumerate(cash_flows_df.iterrows(), start=1):
                fmt = green_format if row.get('Category') == 'Deposit' else red_format
                val = row['Total Value']
                if val is not None:
                    try:
                        cf_worksheet.write(row_idx, value_col_idx, float(val), fmt)
                    except (ValueError, TypeError):
                        cf_worksheet.write(row_idx, value_col_idx, str(val), fmt)

            # Summary rows below the data
            summary_start = len(cash_flows_df) + 2
            cf_worksheet.write(summary_start, 0, "SUMMARY", header_format)
            cf_worksheet.write(summary_start, 1, '', header_format)

            deposits = cash_flows_df[cash_flows_df['Category'] == 'Deposit']['Total Value']
            withdrawals = cash_flows_df[cash_flows_df['Category'] == 'Withdrawal']['Total Value']
            total_dep = pd.to_numeric(deposits, errors='coerce').sum()
            total_with = pd.to_numeric(withdrawals, errors='coerce').sum()

            total_row_format = workbook.add_format({
                'bold': True, 'bg_color': '#E6F2FF', 'border': 1
            })
            total_row_currency_format = workbook.add_format({
                'bold': True, 'bg_color': '#E6F2FF', 'num_format': '$#,##0.00',
                'align': 'right', 'border': 1
            })

            cf_worksheet.write(summary_start + 1, 0, "Total Deposited", total_row_format)
            cf_worksheet.write(summary_start + 1, 1, total_dep, total_row_currency_format)
            cf_worksheet.write(summary_start + 2, 0, "Total Withdrawn", total_row_format)
            cf_worksheet.write(summary_start + 2, 1, abs(total_with), total_row_currency_format)
            cf_worksheet.write(summary_start + 3, 0, "Net Cash Flow", total_row_format)
            cf_worksheet.write(summary_start + 3, 1, total_dep + total_with, total_row_currency_format)

            # Auto-adjust column widths
            for i, col in enumerate(cash_flows_df.columns):
                column_len = max(cash_flows_df[col].astype(str).map(len).max(), len(col))
                cf_worksheet.set_column(i, i, column_len + 2)

        # Add Income sheet if income data is provided
        if income_df is not None and not income_df.empty:
            income_df.to_excel(writer, sheet_name='Income', index=False)

            inc_worksheet = writer.sheets['Income']

            # Header formatting
            for col_num, value in enumerate(income_df.columns.values):
                inc_worksheet.write(0, col_num, value, header_format)

            # Date formatting
            date_format = workbook.add_format({'num_format': 'yyyy-mm-dd'})
            inc_worksheet.set_column('A:A', 14, date_format)   # Date

            # Colour-code rows by income type: gold for dividends, blue for interest, grey for other
            dividend_format = workbook.add_format({
                'bg_color': '#FFF2CC', 'border': 1, 'num_format': '$#,##0.00', 'align': 'right'
            })
            interest_format = workbook.add_format({
                'bg_color': '#DDEEFF', 'border': 1, 'num_format': '$#,##0.00', 'align': 'right'
            })
            other_income_format = workbook.add_format({
                'bg_color': '#F2F2F2', 'border': 1, 'num_format': '$#,##0.00', 'align': 'right'
            })

            value_col_idx = list(income_df.columns).index('Total Value') if 'Total Value' in income_df.columns else None
            type_col_idx = list(income_df.columns).index('Transaction Type') if 'Transaction Type' in income_df.columns else None

            if value_col_idx is not None:
                for row_idx, (_, row) in enumerate(income_df.iterrows(), start=1):
                    t_type = row.get('Transaction Type', '') if type_col_idx is not None else ''
                    if t_type == 'Dividend':
                        fmt = dividend_format
                    elif t_type == 'Interest':
                        fmt = interest_format
                    else:
                        fmt = other_income_format

                    val = row.get('Total Value')
                    if val is not None:
                        try:
                            inc_worksheet.write(row_idx, value_col_idx, float(val), fmt)
                        except (ValueError, TypeError):
                            inc_worksheet.write(row_idx, value_col_idx, str(val), fmt)

            # Summary rows below the income data
            inc_summary_start = len(income_df) + 2
            inc_worksheet.write(inc_summary_start, 0, "SUMMARY", header_format)
            inc_worksheet.write(inc_summary_start, 1, '', header_format)

            inc_total_row_format = workbook.add_format({
                'bold': True, 'bg_color': '#E6F2FF', 'border': 1
            })
            inc_total_currency_format = workbook.add_format({
                'bold': True, 'bg_color': '#E6F2FF', 'num_format': '$#,##0.00',
                'align': 'right', 'border': 1
            })

            inc_amounts = pd.to_numeric(income_df['Total Value'], errors='coerce') if 'Total Value' in income_df.columns else pd.Series(dtype=float)
            total_income = inc_amounts.sum()

            r = inc_summary_start + 1
            inc_worksheet.write(r, 0, "Total Income", inc_total_row_format)
            inc_worksheet.write(r, 1, total_income, inc_total_currency_format)
            r += 1

            # Per-type subtotals
            if 'Transaction Type' in income_df.columns and 'Total Value' in income_df.columns:
                for t_type, group in income_df.groupby('Transaction Type'):
                    type_total = pd.to_numeric(group['Total Value'], errors='coerce').sum()
                    inc_worksheet.write(r, 0, f"  {t_type} Total", inc_total_row_format)
                    inc_worksheet.write(r, 1, type_total, inc_total_currency_format)
                    r += 1

            # Auto-adjust column widths
            for i, col in enumerate(income_df.columns):
                column_len = max(income_df[col].astype(str).map(len).max(), len(col))
                inc_worksheet.set_column(i, i, column_len + 2)

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
        start_date = datetime.date(2025, 1, 1)
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

        from analytics import PortfolioAnalytics, export_analytics_to_excel

        # After you have consolidated_portfolio and transaction_data
        analytics = PortfolioAnalytics(consolidated_portfolio, transaction_data)
        full_report = analytics.generate_full_report()

        # Export to Excel
        export_analytics_to_excel(consolidated_portfolio, full_report)

        # Or print specific analyses
        print("\nConcentration Analysis:")
        print(analytics.concentration_analysis())
        print("\nRisk Metrics:")
        print(analytics.risk_metrics())
    
    except Exception as e:
        print(f"An error occurred: {e}")
