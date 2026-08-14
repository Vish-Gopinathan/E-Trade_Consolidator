"""
Realised profit and loss: what selling actually locked in.

Everything the dashboard called a "gain" before this module was **unrealised** —
today's market value against what the current positions cost. That leaves out
the money already taken off the table, which is why the portfolio value would
not reconcile against deposits: on this account $19k of the balance was neither
a deposit nor an unrealised gain, it was the accumulated result of closed
trades.

**FIFO.** Shares are matched oldest-buy-first. E*TRADE lets you elect a lot at
sale time, so a specific-identification election would produce a different tax
answer; FIFO is the right default and the only one derivable from a transaction
feed that does not record the election. The figures here are for understanding
the portfolio, not for filing — use the broker's 1099-B for that.

**Unmatched sells are the honest hard case.** A sell whose buy predates the
available history has no cost basis, and the tempting shortcut — treating basis
as zero — would report the entire proceeds as profit. On this account that
would have invented thousands of dollars of gain out of nine positions. Those
shares are counted, reported and excluded from the total instead.
"""

import logging

import pandas as pd

from portfolio import classify

LOGGER = logging.getLogger(__name__)

#: Share counts below this are floating-point residue from lot arithmetic, not
#: holdings. Fractional shares are real, so this has to sit well under a
#: plausible fraction while still absorbing accumulated error.
_EPSILON = 1e-6


def cumulative_split_factor(splits, since_date) -> float:
    """
    Product of every split ratio that took effect **after** ``since_date``.

    100 shares bought before a 10:1 split are 1,000 shares today. Matching a
    pre-split buy against a post-split sell without this understates the shares
    sold by the split ratio and leaves a phantom open lot behind.

    Accepts a ``pd.Series`` indexed by date (what yfinance returns) or a plain
    ``{date: ratio}`` mapping (what the local price store holds). Returns 1.0
    when there is nothing to apply — the overwhelmingly common case.
    """
    if splits is None:
        return 1.0

    if isinstance(splits, dict):
        if not splits:
            return 1.0
        splits = pd.Series(splits, dtype=float)
        splits.index = pd.to_datetime(splits.index, errors='coerce')

    if not hasattr(splits, 'empty') or splits.empty:
        return 1.0

    try:
        since = pd.Timestamp(since_date)
        index = splits.index
        if getattr(index, 'tz', None) is not None:
            index = index.tz_localize(None)
        after = splits[index > since]
        return float(after.prod()) if not after.empty else 1.0
    except (TypeError, ValueError) as exc:
        LOGGER.debug('split factor failed for %s: %s', since_date, exc)
        return 1.0


def realized_pnl(transactions_df: pd.DataFrame, splits=None, held_symbols=()) -> dict:
    """
    Walk every trade in chronological order and match sells against buys.

    Args:
        transactions_df: Classified transactions. Only ``Category == Trade``
            rows are read; sells arrive with a negative ``Quantity``.
        splits: ``{symbol: series_or_mapping}`` of split ratios. Omit when no
            holding has split — every factor then falls back to 1.0.
        held_symbols: Symbols currently in the portfolio, used to flag lots that
            were bought, never sold, and are not held either.

    Returns:
        Totals with gains and losses kept apart, per-symbol detail, and the two
        data-quality lists (``unmatched`` sells, ``orphan_lots``) that say where
        the number cannot be trusted.
    """
    empty = {
        'gains': 0.0, 'losses': 0.0, 'net': 0.0, 'matched_proceeds': 0.0,
        'by_symbol': {}, 'unmatched': {}, 'unmatched_shares': 0.0,
        'unmatched_proceeds': 0.0, 'orphan_lots': {}, 'sell_count': 0,
    }
    trades = _trade_rows(transactions_df)
    if trades.empty:
        return empty

    splits = splits or {}
    lots = {}          # symbol -> [[shares_on_today_basis, cost_per_share], ...]
    realized = {}      # symbol -> {'gain', 'loss', 'proceeds', 'shares'}
    unmatched = {}     # symbol -> {'shares', 'proceeds'}
    sell_count = 0

    # Zipped rather than itertuples: the latter mangles 'Total Value' into a
    # positional name, which is a silent rename waiting to pick the wrong column.
    for symbol, quantity, value, when in zip(
        trades['Symbol'], trades['Quantity'], trades['Total Value'], trades['Date'],
    ):
        symbol = str(symbol)
        quantity = float(quantity)
        value = abs(float(value))
        factor = cumulative_split_factor(splits.get(symbol), when)
        shares = abs(quantity) * factor
        if shares <= _EPSILON:
            continue
        price = value / shares          # per share, on today's split basis

        if quantity > 0:
            lots.setdefault(symbol, []).append([shares, price])
            continue

        sell_count += 1
        remaining = shares
        book = realized.setdefault(
            symbol, {'gain': 0.0, 'loss': 0.0, 'proceeds': 0.0, 'shares': 0.0}
        )
        open_lots = lots.setdefault(symbol, [])

        while remaining > _EPSILON and open_lots:
            lot = open_lots[0]
            taken = min(remaining, lot[0])
            profit = taken * (price - lot[1])
            if profit >= 0:
                book['gain'] += profit
            else:
                book['loss'] += profit
            book['proceeds'] += taken * price
            book['shares'] += taken
            lot[0] -= taken
            remaining -= taken
            if lot[0] <= _EPSILON:
                open_lots.pop(0)

        if remaining > _EPSILON:
            # No buy on record for these shares. Their basis is unknown, so
            # their profit is unknown — record and exclude rather than assume.
            entry = unmatched.setdefault(symbol, {'shares': 0.0, 'proceeds': 0.0})
            entry['shares'] += remaining
            entry['proceeds'] += remaining * price

    held = {str(s) for s in held_symbols}
    orphans = {
        symbol: round(sum(lot[0] for lot in open_lots), 4)
        for symbol, open_lots in lots.items()
        if symbol not in held and sum(lot[0] for lot in open_lots) > _EPSILON
    }

    gains = sum(book['gain'] for book in realized.values())
    losses = sum(book['loss'] for book in realized.values())
    return {
        'gains': round(gains, 2),
        'losses': round(losses, 2),
        'net': round(gains + losses, 2),
        'matched_proceeds': round(sum(b['proceeds'] for b in realized.values()), 2),
        'by_symbol': {
            symbol: {
                'gain': round(book['gain'], 2),
                'loss': round(book['loss'], 2),
                'net': round(book['gain'] + book['loss'], 2),
                'proceeds': round(book['proceeds'], 2),
                'shares': round(book['shares'], 4),
            }
            for symbol, book in sorted(
                realized.items(), key=lambda kv: kv[1]['gain'] + kv[1]['loss']
            )
        },
        'unmatched': {
            symbol: {
                'shares': round(entry['shares'], 4),
                'proceeds': round(entry['proceeds'], 2),
            }
            for symbol, entry in sorted(
                unmatched.items(), key=lambda kv: -kv[1]['proceeds']
            )
        },
        'unmatched_shares': round(sum(e['shares'] for e in unmatched.values()), 4),
        'unmatched_proceeds': round(sum(e['proceeds'] for e in unmatched.values()), 2),
        'orphan_lots': dict(sorted(orphans.items())),
        'sell_count': sell_count,
    }


def _trade_rows(transactions_df: pd.DataFrame) -> pd.DataFrame:
    """
    Usable trades, oldest first, with buys ahead of sells on the same day.

    The tie-break matters for a same-day round trip: process the sell first and
    its own buy is not yet on the books, so it reads as an unmatched sell and a
    stranded lot instead of a closed position.
    """
    required = {'Category', 'Symbol', 'Quantity', 'Total Value', 'Date'}
    if transactions_df.empty or not required.issubset(transactions_df.columns):
        return pd.DataFrame()

    trades = transactions_df[transactions_df['Category'] == classify.TRADE].copy()
    if trades.empty:
        return trades

    trades['Date'] = pd.to_datetime(trades['Date'], errors='coerce')
    trades['Quantity'] = pd.to_numeric(trades['Quantity'], errors='coerce')
    trades['Total Value'] = pd.to_numeric(trades['Total Value'], errors='coerce')
    trades['Symbol'] = trades['Symbol'].fillna('').astype(str).str.strip()

    trades = trades[
        (trades['Symbol'] != '')
        & trades['Date'].notna()
        & trades['Quantity'].notna()
        & (trades['Quantity'] != 0)
        & trades['Total Value'].notna()
    ]
    if trades.empty:
        return trades

    trades['_is_sell'] = (trades['Quantity'] < 0).astype(int)
    return trades.sort_values(['Date', '_is_sell'], kind='stable')
