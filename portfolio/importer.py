"""
Reading an E*TRADE transaction CSV.

The website will hand you a download covering periods the API refuses, which
makes it the practical route to closing a history gap. The format is not stable
across account types or export dates, so this reads by *recognising* column
names rather than trusting positions, and reports what it could not use instead
of dropping it silently.

Nothing here writes. The caller previews the frame and decides.
"""

import io
import logging

import pandas as pd

LOGGER = logging.getLogger(__name__)

#: Lower-cased header fragments mapped to the column the ledger expects. Matched
#: as substrings, longest first, because exports vary between e.g.
#: ``TransactionDate``, ``Transaction Date`` and plain ``Date``.
_HEADERS = {
    'transactiondate': 'Date',
    'transaction date': 'Date',
    'date': 'Date',
    'transactiontype': 'Transaction Type',
    'transaction type': 'Transaction Type',
    'type': 'Transaction Type',
    'securitytype': None,          # present in exports, not something we store
    'symbol': 'Symbol',
    'description': 'Security Name',
    'quantity': 'Quantity',
    'price': 'Price',
    'amount': 'Total Value',
    'commission': None,
    'account': 'Account',
}

#: Columns a row needs before it is worth keeping.
_REQUIRED = ('Date', 'Total Value')


def read_csv(source, account: str | None = None) -> tuple:
    """
    Parse an E*TRADE CSV export into ledger-shaped rows.

    Args:
        source: A file-like object, bytes, or a path.
        account: Account label to stamp on rows whose CSV has no account column.

    Returns:
        ``(frame, problems)`` — the usable rows, and a list of plain-language
        notes about anything skipped. An empty frame with a populated problem
        list is a normal outcome for an unrecognised export, not an exception.
    """
    problems = []
    raw = _read_frame(source)
    if raw is None or raw.empty:
        return pd.DataFrame(), ['The file contained no rows.']

    mapping = _map_columns(raw.columns)
    missing = [column for column in _REQUIRED if column not in mapping.values()]
    if missing:
        return pd.DataFrame(), [
            'Could not find a column for: ' + ', '.join(missing)
            + '. Columns seen: ' + ', '.join(str(c) for c in raw.columns) + '.'
        ]

    frame = raw.rename(columns=mapping)
    frame = frame[[c for c in frame.columns if c in set(_HEADERS.values()) - {None}]]
    frame = frame.loc[:, ~frame.columns.duplicated()]

    before = len(frame)
    # format='mixed' because a single export can carry more than one date format,
    # and letting pandas infer per element warns on every call.
    frame['Date'] = pd.to_datetime(frame['Date'], errors='coerce', format='mixed')
    undated = int(frame['Date'].isna().sum())
    if undated:
        problems.append(f'{undated} row(s) had no readable date and were skipped.')
    frame = frame[frame['Date'].notna()]

    for column in ('Quantity', 'Price', 'Total Value'):
        if column in frame.columns:
            frame[column] = frame[column].apply(_to_number)

    unpriced = int(frame['Total Value'].isna().sum())
    if unpriced:
        problems.append(f'{unpriced} row(s) had no readable amount and were skipped.')
    frame = frame[frame['Total Value'].notna()]

    if frame.empty:
        problems.append(f'None of the {before} row(s) could be used.')
        return pd.DataFrame(), problems

    for column, default in (
        ('Transaction Type', 'Transfer'), ('Symbol', None),
        ('Security Name', ''), ('Account', account or 'Imported'),
    ):
        if column not in frame.columns:
            frame[column] = default
    frame['Account'] = frame['Account'].fillna(account or 'Imported')
    frame['Symbol'] = frame['Symbol'].apply(
        lambda s: str(s).strip().upper() or None if pd.notna(s) else None
    )
    frame['Transaction Type'] = frame['Transaction Type'].fillna('Transfer').astype(str).str.strip()

    # E*TRADE reports sells with a negative quantity and this app relies on that
    # sign to tell a buy from a sell. Exports are not consistent about it, so
    # the transaction type is treated as authoritative.
    sells = frame['Transaction Type'].str.lower().str.startswith('sold')
    if 'Quantity' in frame.columns:
        frame.loc[sells, 'Quantity'] = -frame.loc[sells, 'Quantity'].abs()

    return frame.reset_index(drop=True), problems


def _read_frame(source):
    """Read the CSV, tolerating a preamble above the real header row."""
    data = source.read() if hasattr(source, 'read') else source
    if isinstance(data, bytes):
        data = data.decode('utf-8-sig', errors='replace')
    if not isinstance(data, str):
        return pd.read_csv(data)

    # Exports often carry a few title lines before the header. Find the first row
    # that looks like a header and start there rather than failing outright.
    lines = data.splitlines()
    for offset, line in enumerate(lines[:25]):
        lowered = line.lower()
        if 'date' in lowered and (',' in line):
            try:
                return pd.read_csv(io.StringIO('\n'.join(lines[offset:])))
            except (pd.errors.ParserError, ValueError) as exc:
                LOGGER.debug('csv parse failed at line %d: %s', offset, exc)
                continue
    try:
        return pd.read_csv(io.StringIO(data))
    except (pd.errors.ParserError, ValueError):
        return None


def _map_columns(columns) -> dict:
    """Match each CSV header to a ledger column, longest fragment first."""
    fragments = sorted((f for f in _HEADERS if _HEADERS[f]), key=len, reverse=True)
    mapping, claimed = {}, set()
    for column in columns:
        lowered = str(column).strip().lower().replace('_', ' ')
        for fragment in fragments:
            target = _HEADERS[fragment]
            if fragment in lowered and target not in claimed:
                mapping[column] = target
                claimed.add(target)
                break
    return mapping


def _to_number(value):
    """``$1,234.56``, ``(1,234.56)`` and ``1234.56`` all become floats."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if isinstance(value, (int, float)):
        return float(value)

    text = str(value).strip().replace('$', '').replace(',', '').replace('−', '-')
    if not text or text in ('-', '--'):
        return None
    negative = text.startswith('(') and text.endswith(')')
    if negative:
        text = text[1:-1]
    try:
        number = float(text)
    except ValueError:
        return None
    return -number if negative else number
