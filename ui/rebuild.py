"""
Shared access to the reconstructed daily history.

Two pages need the same expensive thing — Value Over Time and Performance both
rest on :func:`portfolio.history.reconstruct` — and rebuilding it twice would
cost a second pass over the price store and, worse, invite the two pages to
disagree. The repository has been bitten by exactly that: when the same figure is
computed in two places, the drift is silent and the reader has no way to tell
which number is wrong.

So the resolution step and the rebuild live here, and both pages call them. The
result is cached in session state under a caller-supplied key, so a page with its
own date controls does not invalidate the other page's cache on every rerun.
"""

import json
from dataclasses import dataclass, field
from datetime import date

import pandas as pd
import streamlit as st

from portfolio import history as ph
from portfolio import symbols as sr
from portfolio.storage import prices as ps


@dataclass
class Resolution:
    """Trade rows with tickers attached, and what could not be attached."""

    all_trades: pd.DataFrame
    trades: pd.DataFrame
    unresolved: list = field(default_factory=list)
    sources: dict = field(default_factory=dict)
    manual_map: dict = field(default_factory=dict)
    first_trade: date | None = None

    @property
    def empty(self) -> bool:
        return self.all_trades is None or self.all_trades.empty


def resolve(portfolio: dict) -> Resolution:
    """Trade rows from the portfolio, with symbols resolved against the map."""
    all_trades = ph.trade_rows(portfolio.get('transactions'))
    if all_trades.empty:
        return Resolution(all_trades=all_trades, trades=all_trades)

    manual_map = sr.load_map()
    trades, unresolved, sources = sr.resolve_trades(
        all_trades, portfolio.get('holdings'), manual_map
    )
    return Resolution(
        all_trades=all_trades, trades=trades, unresolved=unresolved,
        sources=sources, manual_map=manual_map,
        first_trade=pd.to_datetime(all_trades['Date']).min().date(),
    )


def daily_history(portfolio: dict, resolution: Resolution, start: date, end: date,
                  *, key: str, label: str = 'Rebuilding daily values…'):
    """
    Reconstruct the daily value history, cached in session state under ``key``.

    Prices are fetched up to the holdings anchor rather than to ``end``: the
    store is shared, and a narrower window would make a later page asking for
    more days refetch what it could have had for free.

    Returns:
        ``(HistoryResult, price_store)``.
    """
    anchor = ph.anchor_date(portfolio)
    signature = (
        portfolio.get('fetched_at', ''), str(start), str(end),
        len(resolution.trades), json.dumps(resolution.manual_map, sort_keys=True),
    )
    if st.session_state.get(f'{key}_signature') == signature:
        return st.session_state[f'{key}_result'], st.session_state[f'{key}_store']

    symbols = sorted(
        set(ph.current_shares(portfolio.get('holdings')).index)
        | (set(resolution.trades['Symbol']) - {''})
    )
    bar = st.progress(0.0, text='Preparing…')
    try:
        store = ps.ensure(
            symbols, start, anchor,
            progress=lambda f, m: bar.progress(min(f, 1.0), text=m),
        )
        store = ps.ensure_metadata(
            symbols, progress=lambda f, m: bar.progress(min(f, 1.0), text=m),
        )
        bar.progress(1.0, text=label)
        result = ph.reconstruct(portfolio, resolution.trades, store, start, end, ps)
    finally:
        bar.empty()

    st.session_state[f'{key}_signature'] = signature
    st.session_state[f'{key}_result'] = result
    st.session_state[f'{key}_store'] = store
    return result, store


def invalidate(key: str) -> None:
    """Drop a cached rebuild so the next render recomputes it."""
    st.session_state.pop(f'{key}_signature', None)
