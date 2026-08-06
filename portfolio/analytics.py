"""
Portfolio analytics.

Every key this module emits is a constant from :mod:`portfolio.schema`; nothing
here spells a report key inline. See that module for why.

**What is deliberately absent.** Earlier versions reported Sharpe and Sortino
ratios computed from each position's lifetime gain percentage. Those ratios are
defined over a *time series* of periodic returns; a cross-section of "how much is
each position up since I bought it" is not one. A position bought last week and
one held four years contribute equally, dispersion has no time unit, and the
resulting figure cannot be compared to a published Sharpe ratio for anything. The
raw dispersion is still reported as :data:`~portfolio.schema.GAIN_DISPERSION_PCT`,
labelled for what it actually is.
"""

import json
import logging

import numpy as np
import pandas as pd

from portfolio import paths, schema

LOGGER = logging.getLogger(__name__)

#: Fallback when config/sectors.json is missing. The shipped file is far richer
#: (198 symbols across 11 sectors) — edit that rather than this.
_DEFAULT_SECTOR_GROUPS = {
    'Tech': ['AAPL', 'MSFT', 'GOOGL', 'META', 'NVDA', 'TSLA', 'AMD', 'INTC'],
    'Finance': ['JPM', 'BAC', 'WFC', 'GS', 'BLK', 'SCHW'],
    'Healthcare': ['JNJ', 'UNH', 'PFE', 'ABBV', 'MRK', 'LLY'],
    'Consumer': ['WMT', 'HD', 'MCD', 'COST', 'AMZN'],
    'Energy': ['XOM', 'CVX', 'COP', 'MPC'],
    'Utilities': ['DUK', 'NEE', 'SO'],
    'ETFs/Index': ['SPY', 'IVV', 'VOO', 'QQQ', 'VTI', 'AGG', 'BND'],
}

#: Buckets for the gain distribution, as (label, low, high) with bounds in percent.
_GAIN_BUCKETS = [
    ('Major loss (< -50%)', -np.inf, -50),
    ('Moderate loss (-50% to -10%)', -50, -10),
    ('Small loss (-10% to 0%)', -10, 0),
    ('Up to +50%', 0, 50),
    ('Above +50%', 50, np.inf),
]


def _load_sector_groups() -> dict:
    """Load sector mappings from config/sectors.json, falling back to defaults."""
    sectors_path = paths.CONFIG_DIR / 'sectors.json'
    try:
        return json.loads(sectors_path.read_text())
    except FileNotFoundError:
        return _DEFAULT_SECTOR_GROUPS
    except (json.JSONDecodeError, OSError) as exc:
        LOGGER.warning('could not read %s (%s); using built-in sector defaults', sectors_path, exc)
        return _DEFAULT_SECTOR_GROUPS


class PortfolioAnalytics:
    """
    Computes the analytics report from holdings, transactions and cash flows.

    Args:
        consolidated_df: Holdings including the ``CASH`` row.
        transactions_df: Full classified transaction history, or None.
        cash_flows_df: External deposits and withdrawals only. Derived from
            ``transactions_df`` when omitted.
        beginning_market_value: Portfolio value on the first day of the transaction
            range, used as the Modified Dietz denominator base. Defaults to 0,
            which is correct when the range covers the account's whole life — the
            usual case, since the dashboard fetches from 2000 by default. Pass a
            real figure (:mod:`portfolio.history` can reconstruct one) when
            analysing a window that starts mid-life.
    """

    def __init__(self, consolidated_df, transactions_df=None, cash_flows_df=None,
                 beginning_market_value=0.0):
        from portfolio import classify  # local import keeps the module import-light

        self.beginning_market_value = float(beginning_market_value or 0.0)
        holdings = consolidated_df if consolidated_df is not None else pd.DataFrame()
        self.holdings = holdings[holdings['Symbol'] != 'CASH'].copy() if len(holdings) else holdings
        cash_rows = holdings[holdings['Symbol'] == 'CASH']['Market Value'] if len(holdings) else []
        self.cash = float(cash_rows.iloc[0]) if len(cash_rows) else 0.0

        self.transactions = transactions_df if transactions_df is not None else pd.DataFrame()
        if cash_flows_df is not None:
            self.cash_flows = cash_flows_df
        else:
            self.cash_flows = classify.get_cash_flows(self.transactions)
        self.income = classify.get_income(self.transactions)

    # ── Sections ──────────────────────────────────────────────────────────────

    def performance(self) -> dict:
        """
        Return figures, including the deposit-adjusted (Modified Dietz) return.

        **Unrealised return** compares positions to what was paid for them. Cash is
        excluded from both sides: it has no cost basis, so counting it in market
        value alone would report uninvested cash as investment gain.

        **Deposit-adjusted return** weights each cash flow by the fraction of the
        period it was invested, so contributions are not read as performance::

            R = (EMV - BMV - CF) / (BMV + Σ CF_i × W_i)

        BMV comes from ``beginning_market_value`` and defaults to 0 — correct when
        the transaction range covers the account's whole life, which is the
        dashboard's default. An earlier version substituted total cost basis for
        BMV; because cost basis is mostly *made of* the deposits that also appear
        in CF, the same money was subtracted twice and this account reported −45%
        against a real gain. The assumption in force travels with the number as
        :data:`~portfolio.schema.DEPOSIT_ADJUSTED_RETURN_BASIS`.
        """
        positions_value = float(self.holdings['Market Value'].sum()) if len(self.holdings) else 0.0
        cost_basis = float(self.holdings['Total Cost'].sum()) if len(self.holdings) else 0.0
        portfolio_value = positions_value + self.cash
        unrealised_gain = positions_value - cost_basis

        result = {
            schema.TOTAL_RETURN_DOLLARS: round(unrealised_gain, 2),
            schema.TOTAL_RETURN_PCT: (
                round(unrealised_gain / cost_basis * 100, 2) if cost_basis else 0.0
            ),
            schema.COST_BASIS: round(cost_basis, 2),
            schema.MARKET_VALUE: round(portfolio_value, 2),
            schema.AVG_POSITION_COST: (
                round(cost_basis / len(self.holdings), 2) if len(self.holdings) else 0.0
            ),
            schema.DEPOSIT_ADJUSTED_RETURN_PCT: None,
            schema.DEPOSIT_ADJUSTED_RETURN_BASIS: (
                'No external cash flows in range, so no adjustment was applied.'
            ),
        }

        flows = self._signed_cash_flows()
        if flows.empty:
            return result

        net_flow = float(flows['Total Value'].sum())
        dates = pd.to_datetime(flows['Date'])
        period_start = dates.min()
        total_days = max((pd.Timestamp.now() - period_start).days, 1)
        weights = (total_days - (dates - period_start).dt.days) / total_days
        weighted_flow = float((flows['Total Value'] * weights).sum())

        bmv = self.beginning_market_value
        denominator = bmv + weighted_flow
        if denominator:
            result[schema.DEPOSIT_ADJUSTED_RETURN_PCT] = round(
                (portfolio_value - bmv - net_flow) / denominator * 100, 2
            )
            basis = (
                f'Modified Dietz over {total_days:,} days from '
                f'{period_start:%b %Y}, weighting each deposit and withdrawal by '
                'how long it was invested.'
            )
            result[schema.DEPOSIT_ADJUSTED_RETURN_BASIS] = basis + (
                ' Assumes the portfolio was empty at the start of the range; if your '
                'transaction history does not reach account opening, this overstates '
                'the return.' if not bmv else
                f' Starting portfolio value taken as ${bmv:,.0f}.'
            )
        return result

    def concentration(self) -> dict:
        """Concentration measures. HHI runs 0–10000; higher means less diversified."""
        if self.holdings.empty:
            return {schema.TOTAL_POSITIONS: 0}

        weights = self.holdings['Percent of Portfolio'].astype(float)
        hhi = float((weights ** 2).sum())
        ordered = weights.sort_values(ascending=False)

        return {
            schema.HHI: round(hhi, 2),
            schema.HHI_INTERPRETATION: _interpret_hhi(hhi),
            schema.EFFECTIVE_POSITIONS: round(10000 / hhi, 2) if hhi else len(self.holdings),
            schema.TOTAL_POSITIONS: int(len(self.holdings)),
            schema.TOP_3_PCT: round(float(ordered.head(3).sum()), 2),
            schema.TOP_5_PCT: round(float(ordered.head(5).sum()), 2),
            schema.TOP_10_PCT: round(float(ordered.head(10).sum()), 2),
            schema.POSITION_WEIGHTS: {
                row['Symbol']: round(float(row['Percent of Portfolio']), 2)
                for _, row in self.holdings.sort_values(
                    'Percent of Portfolio', ascending=False
                ).iterrows()
            },
        }

    def sectors(self) -> dict:
        """Allocation by sector, using the mappings in config/sectors.json."""
        if self.holdings.empty:
            return {schema.SECTOR_VALUES: {}, schema.SECTOR_WEIGHTS_PCT: {}}

        groups = _load_sector_groups()
        allocation = {}
        for sector, symbols in groups.items():
            value = float(self.holdings[self.holdings['Symbol'].isin(symbols)]['Market Value'].sum())
            if value > 0:
                allocation[sector] = round(value, 2)

        classified = {symbol for symbols in groups.values() for symbol in symbols}
        unclassified = float(
            self.holdings[~self.holdings['Symbol'].isin(classified)]['Market Value'].sum()
        )
        if unclassified > 0:
            allocation['Unclassified'] = round(unclassified, 2)

        total = sum(allocation.values())
        weights = {k: round(v / total * 100, 2) for k, v in allocation.items()} if total else {}
        return {schema.SECTOR_VALUES: allocation, schema.SECTOR_WEIGHTS_PCT: weights}

    def cash_flows_summary(self) -> dict:
        """Deposit and withdrawal totals over the period."""
        flows = self._signed_cash_flows()
        if flows.empty:
            return {
                schema.DEPOSIT_COUNT: 0, schema.TOTAL_DEPOSITED: 0.0,
                schema.WITHDRAWAL_COUNT: 0, schema.TOTAL_WITHDRAWN: 0.0,
                schema.NET_CASH_FLOW: 0.0, schema.FLOWS_NEEDING_REVIEW: 0,
            }

        deposits = flows[flows['Category'] == 'Deposit']['Total Value']
        withdrawals = flows[flows['Category'] == 'Withdrawal']['Total Value']
        needs_review = (
            int(flows['Needs Review'].fillna(False).sum())
            if 'Needs Review' in flows.columns else 0
        )

        result = {
            schema.DEPOSIT_COUNT: int(len(deposits)),
            schema.TOTAL_DEPOSITED: round(float(deposits.sum()), 2),
            schema.WITHDRAWAL_COUNT: int(len(withdrawals)),
            schema.TOTAL_WITHDRAWN: round(abs(float(withdrawals.sum())), 2),
            schema.NET_CASH_FLOW: round(float(flows['Total Value'].sum()), 2),
            schema.FLOWS_NEEDING_REVIEW: needs_review,
        }
        if len(deposits):
            result[schema.LARGEST_DEPOSIT] = round(float(deposits.max()), 2)
            result[schema.LAST_DEPOSIT_DATE] = _date_string(flows, 'Deposit')
        if len(withdrawals):
            result[schema.LARGEST_WITHDRAWAL] = round(abs(float(withdrawals.min())), 2)
            result[schema.LAST_WITHDRAWAL_DATE] = _date_string(flows, 'Withdrawal')
        return result

    def income_summary(self) -> dict:
        """Dividend and interest income — money generated inside the account."""
        if self.income.empty:
            return {schema.TOTAL_INCOME: 0.0, schema.INCOME_COUNT: 0, schema.INCOME_BY_TYPE: {}}

        income = self.income.copy()
        income['Total Value'] = pd.to_numeric(income['Total Value'], errors='coerce').fillna(0)

        by_type = {}
        if 'Transaction Type' in income.columns:
            by_type = {
                str(name): round(float(group['Total Value'].sum()), 2)
                for name, group in income.groupby('Transaction Type')
            }

        return {
            schema.TOTAL_INCOME: round(float(income['Total Value'].sum()), 2),
            schema.INCOME_COUNT: int(len(income)),
            schema.INCOME_BY_TYPE: by_type,
            schema.LARGEST_INCOME: round(float(income['Total Value'].max()), 2),
            schema.LAST_INCOME_DATE: str(pd.to_datetime(income['Date']).max().date()),
        }

    def holdings_quality(self) -> dict:
        """How positions are doing: win rate, extremes, and the spread of outcomes."""
        if self.holdings.empty:
            return {schema.WINNERS: 0, schema.LOSERS: 0, schema.BREAKEVEN: 0,
                    schema.WIN_RATE_PCT: 0.0, schema.GAIN_DISTRIBUTION: {}}

        gains = self.holdings['Total Gain %'].astype(float)
        winners = int((gains > 0).sum())

        best = self.holdings.loc[gains.idxmax()]
        worst = self.holdings.loc[gains.idxmin()]

        return {
            schema.WINNERS: winners,
            schema.LOSERS: int((gains < 0).sum()),
            schema.BREAKEVEN: int((gains == 0).sum()),
            schema.WIN_RATE_PCT: round(winners / len(gains) * 100, 2),
            schema.GAIN_DISPERSION_PCT: round(float(np.std(gains)), 2) if len(gains) > 1 else 0.0,
            schema.GAIN_DISTRIBUTION: {
                label: int(((gains > low) & (gains <= high)).sum())
                for label, low, high in _GAIN_BUCKETS
            },
            schema.BEST_PERFORMER: {
                'Symbol': best['Symbol'],
                'Gain %': round(float(best['Total Gain %']), 2),
                'Market Value': round(float(best['Market Value']), 2),
            },
            schema.WORST_PERFORMER: {
                'Symbol': worst['Symbol'],
                'Gain %': round(float(worst['Total Gain %']), 2),
                'Market Value': round(float(worst['Market Value']), 2),
            },
        }

    def liquidity(self) -> dict:
        """Cash position in dollars and as a share of the portfolio."""
        positions = float(self.holdings['Market Value'].sum()) if len(self.holdings) else 0.0
        total = positions + self.cash
        return {
            schema.CASH_BALANCE: round(self.cash, 2),
            schema.CASH_PCT: round(self.cash / total * 100, 2) if total > 0 else 0.0,
            schema.TOTAL_PORTFOLIO_VALUE: round(total, 2),
        }

    def trading_activity(self) -> dict:
        """Buy/sell counts and turnover."""
        empty = {schema.BUY_COUNT: 0, schema.SELL_COUNT: 0, schema.TOTAL_BOUGHT: 0.0,
                 schema.TOTAL_SOLD: 0.0, schema.TURNOVER_PCT: 0.0, schema.AVG_TRADE_SIZE: 0.0}
        if self.transactions.empty or 'Transaction Type' not in self.transactions.columns:
            return empty

        amounts = pd.to_numeric(self.transactions['Total Value'], errors='coerce').abs()
        buys = self.transactions['Transaction Type'] == 'Bought'
        sells = self.transactions['Transaction Type'] == 'Sold'
        bought, sold = float(amounts[buys].sum()), float(amounts[sells].sum())
        trade_count = int(buys.sum() + sells.sum())

        positions = float(self.holdings['Market Value'].sum()) if len(self.holdings) else 0.0
        return {
            schema.BUY_COUNT: int(buys.sum()),
            schema.SELL_COUNT: int(sells.sum()),
            schema.TOTAL_BOUGHT: round(bought, 2),
            schema.TOTAL_SOLD: round(sold, 2),
            schema.TURNOVER_PCT: (
                round((bought + sold) / 2 / positions * 100, 2) if positions else 0.0
            ),
            schema.AVG_TRADE_SIZE: (
                round((bought + sold) / trade_count, 2) if trade_count else 0.0
            ),
        }

    def generate_full_report(self) -> dict:
        """The complete report, keyed by the section constants in :mod:`portfolio.schema`."""
        return {
            schema.PERFORMANCE: self.performance(),
            schema.CONCENTRATION: self.concentration(),
            schema.SECTORS: self.sectors(),
            schema.CASH_FLOWS: self.cash_flows_summary(),
            schema.INCOME: self.income_summary(),
            schema.HOLDINGS_QUALITY: self.holdings_quality(),
            schema.LIQUIDITY: self.liquidity(),
            schema.TRADING: self.trading_activity(),
        }

    # ── Internals ─────────────────────────────────────────────────────────────

    def _signed_cash_flows(self) -> pd.DataFrame:
        """
        Cash flows with a guaranteed sign convention: deposits positive,
        withdrawals negative. E*TRADE is not consistent about this, and a
        withdrawal that arrives positive would otherwise inflate contributions.
        """
        if self.cash_flows is None or self.cash_flows.empty:
            return pd.DataFrame(columns=['Date', 'Total Value', 'Category'])

        flows = self.cash_flows.copy()
        flows['Total Value'] = pd.to_numeric(flows['Total Value'], errors='coerce').fillna(0)
        withdrawals = flows['Category'] == 'Withdrawal'
        flows.loc[withdrawals, 'Total Value'] = -flows.loc[withdrawals, 'Total Value'].abs()
        deposits = flows['Category'] == 'Deposit'
        flows.loc[deposits, 'Total Value'] = flows.loc[deposits, 'Total Value'].abs()
        return flows


def _interpret_hhi(hhi: float) -> str:
    if hhi < 1500:
        return 'Well diversified'
    if hhi < 2500:
        return 'Moderately diversified'
    if hhi < 5000:
        return 'Concentrated'
    return 'Highly concentrated'


def _date_string(flows: pd.DataFrame, category: str) -> str:
    subset = pd.to_datetime(flows[flows['Category'] == category]['Date'])
    return str(subset.max().date())
