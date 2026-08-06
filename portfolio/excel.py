"""
Excel report generation.

Both exporters accept either a path or an in-memory buffer as ``output``, which is
what lets the dashboard build a workbook for download without touching disk.

Formatting is resolved by **column name**, never by spreadsheet letter. The old
``set_column('F:H', ...)`` style silently formatted whatever happened to sit in
those positions, so reordering a column turned dollars into a date with no error
anywhere.

Percentage columns hold whole numbers (21.47 means 21.47%), so data cells use the
plain ``0.00`` format. Only the summary block, which divides by 100 first, uses
Excel's ``0.00%``.
"""

import datetime
import logging

import pandas as pd

LOGGER = logging.getLogger(__name__)

_CURRENCY_COLUMNS = {
    'Price Paid', 'Total Cost', 'Market Value', 'Total Gain', 'Current Price',
    'Total Value', 'Price', 'Net Amount',
}
_PERCENT_COLUMNS = {'Total Gain %', 'Percent of Portfolio', 'Surprise %'}
_DATE_COLUMNS = {'Date', 'Date Acquired', 'First', 'Last'}


# ── Shared helpers ────────────────────────────────────────────────────────────

def _autofit(worksheet, df: pd.DataFrame, formats: dict | None = None) -> None:
    """
    Size every column to its widest value.

    ``Series.map(len).max()`` returns NaN on an empty frame and ``set_column``
    rejects NaN, so an empty sheet used to abort the whole export. Falls back to
    the header width.
    """
    formats = formats or {}
    for index, column in enumerate(df.columns):
        header_width = len(str(column))
        if len(df):
            widest = df[column].astype(str).map(len).max()
            width = max(int(widest) if pd.notna(widest) else 0, header_width)
        else:
            width = header_width
        worksheet.set_column(index, index, min(width + 2, 60), formats.get(column))


def _write_sheet(writer, df: pd.DataFrame, sheet_name: str, header_format,
                 column_formats: dict) -> None:
    """Write a frame with a styled header row and name-resolved column formats."""
    df.to_excel(writer, sheet_name=sheet_name, index=False)
    worksheet = writer.sheets[sheet_name]
    for index, column in enumerate(df.columns):
        worksheet.write(0, index, str(column), header_format)
    _autofit(worksheet, df, column_formats)


def _column_formats(df: pd.DataFrame, currency, percent, date) -> dict:
    """Map each column to the right number format based on its name."""
    formats = {}
    for column in df.columns:
        if column in _CURRENCY_COLUMNS:
            formats[column] = currency
        elif column in _PERCENT_COLUMNS:
            formats[column] = percent
        elif column in _DATE_COLUMNS:
            formats[column] = date
    return formats


def _default_name(prefix: str) -> str:
    return f'{prefix}_{datetime.datetime.now():%Y-%m-%d}.xlsx'


# ── Holdings / transactions workbook ──────────────────────────────────────────

def export_to_excel(consolidated_df, transactions_df=None, cash_flows_df=None,
                    income_df=None, output=None, summary_spacing=3):
    """
    Write the holdings workbook.

    Sheets: Holdings (with a summary block), and Transactions, Cash Flows and
    Income when data for them is supplied. A sheet whose frame is empty or missing
    its key columns is skipped with a log line rather than raising — a partial
    workbook is more useful than none.

    Args:
        consolidated_df: Output of :func:`portfolio.etrade.consolidate_holdings`.
        output: File path or a binary buffer. Defaults to a dated filename in the
            working directory.
    """
    output = output if output is not None else _default_name('portfolio_consolidated')

    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        workbook = writer.book

        header_fmt = workbook.add_format({'bold': True, 'bg_color': '#D3D3D3', 'border': 1})
        currency_fmt = workbook.add_format({'num_format': '$#,##0.00', 'align': 'right'})
        percent_fmt = workbook.add_format({'num_format': '0.00', 'align': 'right'})
        date_fmt = workbook.add_format({'num_format': 'yyyy-mm-dd'})
        total_fmt = workbook.add_format({'bold': True, 'bg_color': '#E6F2FF', 'border': 1})
        total_currency_fmt = workbook.add_format({
            'bold': True, 'bg_color': '#E6F2FF', 'num_format': '$#,##0.00',
            'align': 'right', 'border': 1,
        })
        total_percent_fmt = workbook.add_format({
            'bold': True, 'bg_color': '#E6F2FF', 'num_format': '0.00%',
            'align': 'right', 'border': 1,
        })

        holdings = consolidated_df.copy() if consolidated_df is not None else pd.DataFrame()
        cash_rows = holdings[holdings['Symbol'] == 'CASH'] if 'Symbol' in holdings else pd.DataFrame()
        if 'Symbol' in holdings:
            holdings = holdings[holdings['Symbol'] != 'CASH']

        _write_sheet(writer, holdings, 'Holdings', header_fmt,
                     _column_formats(holdings, currency_fmt, percent_fmt, date_fmt))
        _write_holdings_summary(
            writer.sheets['Holdings'], holdings, cash_rows, summary_spacing,
            header_fmt, total_fmt, total_currency_fmt, total_percent_fmt,
        )

        _maybe_write(writer, transactions_df, 'Transactions', header_fmt,
                     currency_fmt, percent_fmt, date_fmt)
        _write_cash_flows(writer, cash_flows_df, workbook, header_fmt,
                          currency_fmt, percent_fmt, date_fmt)
        _write_income(writer, income_df, workbook, header_fmt,
                      currency_fmt, percent_fmt, date_fmt)

    LOGGER.info('portfolio workbook written to %s', output)
    return output


def _maybe_write(writer, df, sheet_name, header_fmt, currency_fmt, percent_fmt, date_fmt):
    """Write an optional sheet, skipping quietly when there is nothing to write."""
    if df is None or df.empty:
        LOGGER.debug('skipping %s sheet: no data', sheet_name)
        return False
    _write_sheet(writer, df, sheet_name, header_fmt,
                 _column_formats(df, currency_fmt, percent_fmt, date_fmt))
    return True


def _write_holdings_summary(worksheet, holdings, cash_rows, spacing,
                            header_fmt, total_fmt, currency_fmt, percent_fmt):
    """Append the totals block beneath the holdings table."""
    if holdings.empty:
        return

    total_cost = float(holdings['Total Cost'].sum())
    market_value = float(holdings['Market Value'].sum())
    total_gain = float(holdings['Total Gain'].sum())

    row = len(holdings) + spacing
    worksheet.write(row, 0, 'PORTFOLIO SUMMARY', header_fmt)
    worksheet.write(row + 1, 0, 'Description', header_fmt)
    worksheet.write(row + 1, 1, 'Value', header_fmt)

    entries = [
        ('Total Stocks', len(holdings), total_fmt),
        ('Total Quantity', float(holdings['Quantity'].sum()), total_fmt),
        ('Total Cost', total_cost, currency_fmt),
        ('Total Market Value', market_value, currency_fmt),
        ('Total Gain/Loss', total_gain, currency_fmt),
        ('Total Gain/Loss %', (total_gain / total_cost) if total_cost else 0, percent_fmt),
    ]

    if not cash_rows.empty:
        cash_value = float(cash_rows['Market Value'].iloc[0])
        total_value = market_value + cash_value
        entries += [
            ('Cash', cash_value, currency_fmt),
            ('Total Portfolio Value', total_value, currency_fmt),
            ('Cash %', (cash_value / total_value) if total_value else 0, percent_fmt),
        ]

    for offset, (label, value, value_fmt) in enumerate(entries, start=row + 2):
        worksheet.write(offset, 0, label, total_fmt)
        worksheet.write(offset, 1, value, value_fmt)


def _write_cash_flows(writer, cash_flows_df, workbook, header_fmt,
                      currency_fmt, percent_fmt, date_fmt):
    """Cash Flows sheet: rows tinted by direction, with totals beneath."""
    if not _maybe_write(writer, cash_flows_df, 'Cash Flows', header_fmt,
                        currency_fmt, percent_fmt, date_fmt):
        return
    if 'Total Value' not in cash_flows_df.columns or 'Category' not in cash_flows_df.columns:
        LOGGER.debug('cash flows sheet written without tinting: missing key columns')
        return

    worksheet = writer.sheets['Cash Flows']
    deposit_fmt = workbook.add_format({
        'bg_color': '#E2EFDA', 'border': 1, 'num_format': '$#,##0.00', 'align': 'right'})
    withdrawal_fmt = workbook.add_format({
        'bg_color': '#FCE4D6', 'border': 1, 'num_format': '$#,##0.00', 'align': 'right'})

    value_column = cash_flows_df.columns.get_loc('Total Value')
    for offset, (_, row) in enumerate(cash_flows_df.iterrows(), start=1):
        value = pd.to_numeric(row['Total Value'], errors='coerce')
        if pd.isna(value):
            continue
        fmt = deposit_fmt if row.get('Category') == 'Deposit' else withdrawal_fmt
        worksheet.write(offset, value_column, float(value), fmt)

    amounts = pd.to_numeric(cash_flows_df['Total Value'], errors='coerce')
    deposits = float(amounts[cash_flows_df['Category'] == 'Deposit'].sum())
    withdrawals = float(amounts[cash_flows_df['Category'] == 'Withdrawal'].sum())

    total_fmt = workbook.add_format({'bold': True, 'bg_color': '#E6F2FF', 'border': 1})
    total_currency_fmt = workbook.add_format({
        'bold': True, 'bg_color': '#E6F2FF', 'num_format': '$#,##0.00',
        'align': 'right', 'border': 1})

    start = len(cash_flows_df) + 2
    worksheet.write(start, 0, 'SUMMARY', header_fmt)
    worksheet.write(start, 1, '', header_fmt)
    for offset, (label, value) in enumerate([
        ('Total Deposited', deposits),
        ('Total Withdrawn', abs(withdrawals)),
        ('Net Cash Flow', deposits + withdrawals),
    ], start=start + 1):
        worksheet.write(offset, 0, label, total_fmt)
        worksheet.write(offset, 1, value, total_currency_fmt)


def _write_income(writer, income_df, workbook, header_fmt,
                  currency_fmt, percent_fmt, date_fmt):
    """Income sheet: rows tinted by income type, with per-type subtotals."""
    if not _maybe_write(writer, income_df, 'Income', header_fmt,
                        currency_fmt, percent_fmt, date_fmt):
        return
    if 'Total Value' not in income_df.columns:
        LOGGER.debug('income sheet written without totals: no Total Value column')
        return

    worksheet = writer.sheets['Income']
    tints = {
        'Dividend': workbook.add_format({
            'bg_color': '#FFF2CC', 'border': 1, 'num_format': '$#,##0.00', 'align': 'right'}),
        'Interest': workbook.add_format({
            'bg_color': '#DDEEFF', 'border': 1, 'num_format': '$#,##0.00', 'align': 'right'}),
    }
    default_tint = workbook.add_format({
        'bg_color': '#F2F2F2', 'border': 1, 'num_format': '$#,##0.00', 'align': 'right'})

    value_column = income_df.columns.get_loc('Total Value')
    for offset, (_, row) in enumerate(income_df.iterrows(), start=1):
        value = pd.to_numeric(row['Total Value'], errors='coerce')
        if pd.isna(value):
            continue
        fmt = tints.get(row.get('Transaction Type', ''), default_tint)
        worksheet.write(offset, value_column, float(value), fmt)

    total_fmt = workbook.add_format({'bold': True, 'bg_color': '#E6F2FF', 'border': 1})
    total_currency_fmt = workbook.add_format({
        'bold': True, 'bg_color': '#E6F2FF', 'num_format': '$#,##0.00',
        'align': 'right', 'border': 1})

    amounts = pd.to_numeric(income_df['Total Value'], errors='coerce')
    row = len(income_df) + 2
    worksheet.write(row, 0, 'SUMMARY', header_fmt)
    worksheet.write(row, 1, '', header_fmt)
    worksheet.write(row + 1, 0, 'Total Income', total_fmt)
    worksheet.write(row + 1, 1, float(amounts.sum()), total_currency_fmt)

    if 'Transaction Type' in income_df.columns:
        for offset, (income_type, group) in enumerate(
            income_df.groupby('Transaction Type'), start=row + 2
        ):
            subtotal = float(pd.to_numeric(group['Total Value'], errors='coerce').sum())
            worksheet.write(offset, 0, f'  {income_type} Total', total_fmt)
            worksheet.write(offset, 1, subtotal, total_currency_fmt)


# ── Analytics workbook ────────────────────────────────────────────────────────

def export_analytics_to_excel(consolidated_df, analytics_report, output=None):
    """
    Write the analytics workbook: a flattened report sheet plus holdings detail.

    Nested dicts and lists in the report are indented one level rather than
    stringified, so the sheet stays readable as the report grows.
    """
    output = output if output is not None else _default_name('portfolio_analytics')

    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        workbook = writer.book
        header_fmt = workbook.add_format({
            'bold': True, 'bg_color': '#1F4E78', 'font_color': 'white',
            'border': 1, 'valign': 'vcenter'})
        section_fmt = workbook.add_format({'bold': True, 'bg_color': '#D9E1F2', 'border': 1})
        data_fmt = workbook.add_format({'border': 1, 'valign': 'top'})

        sheet = workbook.add_worksheet('Analytics Summary')
        # A real header row, so anything reading the sheet back (pandas, Excel's
        # own filters) treats row 0 as column names instead of promoting the first
        # section title into one.
        sheet.write(0, 0, 'Metric', header_fmt)
        sheet.write(0, 1, 'Value', header_fmt)

        row = 1
        for section_name, section_data in (analytics_report or {}).items():
            sheet.write(row, 0, section_name, section_fmt)
            sheet.write(row, 1, '', section_fmt)
            row = _write_report_block(sheet, row + 1, section_data, data_fmt)
            row += 1

        sheet.set_column(0, 0, 34)
        sheet.set_column(1, 1, 40)

        holdings = consolidated_df[consolidated_df['Symbol'] != 'CASH'].copy()
        currency_fmt = workbook.add_format({'num_format': '$#,##0.00', 'align': 'right'})
        percent_fmt = workbook.add_format({'num_format': '0.00', 'align': 'right'})
        date_fmt = workbook.add_format({'num_format': 'yyyy-mm-dd'})
        _write_sheet(writer, holdings, 'Holdings Detail', header_fmt,
                     _column_formats(holdings, currency_fmt, percent_fmt, date_fmt))

    LOGGER.info('analytics workbook written to %s', output)
    return output


def _write_report_block(sheet, row, data, data_fmt, indent=0):
    """Recursively flatten one report section onto the sheet. Returns the next row."""
    pad = '    ' * indent

    if isinstance(data, dict):
        for key, value in data.items():
            if isinstance(value, (dict, list)):
                sheet.write(row, 0, f'{pad}{key}', data_fmt)
                sheet.write(row, 1, '', data_fmt)
                row = _write_report_block(sheet, row + 1, value, data_fmt, indent + 1)
            else:
                sheet.write(row, 0, f'{pad}{key}', data_fmt)
                sheet.write(row, 1, value if isinstance(value, (int, float)) else str(value), data_fmt)
                row += 1
        return row

    if isinstance(data, list):
        for item in data:
            row = _write_report_block(sheet, row, item, data_fmt, indent)
        return row

    sheet.write(row, 0, pad, data_fmt)
    sheet.write(row, 1, str(data), data_fmt)
    return row + 1
