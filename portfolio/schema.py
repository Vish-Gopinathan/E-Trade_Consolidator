"""
The analytics report contract.

:meth:`portfolio.analytics.PortfolioAnalytics.generate_full_report` returns a
nested dict, and the pages read values out of it by key. When the two sides used
string literals they drifted: the pages asked for ``'Simple Return (%)'`` and
``'Sector Weights (%)'`` while analytics emitted ``'Total Return (%)'`` and
``'Sector Allocation (%)'``. Every lookup missed, every metric rendered as an
em dash, and nothing raised — the Analytics page looked merely thin rather than
broken, and it kept working in demo mode because the demo data happened to use
the page's spelling.

So the key names live here and nowhere else. Producers and consumers both import
these constants; ``tests/test_schema.py`` fails the build if a report key appears
that is not defined in this module.

**Units.** Percentages are whole numbers — ``30.11`` means 30.11%, not 0.3011.
Dollar values are plain floats. Anything that can be unavailable is ``None``
rather than a string, so formatting code can test for it.
"""

# ── Section names (top-level keys of the report) ──────────────────────────────

PERFORMANCE = 'Performance'
CONCENTRATION = 'Concentration'
SECTORS = 'Sectors'
CASH_FLOWS = 'Cash Flows'
INCOME = 'Income'
HOLDINGS_QUALITY = 'Holdings Quality'
LIQUIDITY = 'Liquidity'
TRADING = 'Trading Activity'
RECONCILIATION = 'Reconciliation'

SECTIONS = (
    PERFORMANCE, CONCENTRATION, SECTORS, CASH_FLOWS,
    INCOME, HOLDINGS_QUALITY, LIQUIDITY, TRADING, RECONCILIATION,
)

# ── Performance ───────────────────────────────────────────────────────────────

TOTAL_RETURN_DOLLARS = 'Total Return ($)'
TOTAL_RETURN_PCT = 'Total Return (%)'
COST_BASIS = 'Total Cost Basis'
MARKET_VALUE = 'Current Market Value'
AVG_POSITION_COST = 'Avg Cost Per Position'

#: Modified Dietz return: investment performance with the timing of deposits and
#: withdrawals factored out, so contributions are not read as gains. ``None`` when
#: there are no cash flows to adjust for.
DEPOSIT_ADJUSTED_RETURN_PCT = 'Deposit-Adjusted Return (%)'

#: Plain-language statement of what the number above assumes. Always displayed
#: beside it — the approximation is material and the reader deserves to know.
DEPOSIT_ADJUSTED_RETURN_BASIS = 'Deposit-Adjusted Return Basis'

#: Money-weighted annual growth rate (XIRR): the constant yearly rate that turns
#: each deposit, on the day it was made, into today's balance. Distinct from
#: DEPOSIT_ADJUSTED_RETURN_PCT, which is cumulative over the whole period and so
#: says nothing about pace. None when the history is too short to annualise.
ANNUALISED_RETURN_PCT = 'Annualised Return (%)'
ANNUALISED_RETURN_BASIS = 'Annualised Return Basis'

# ── Concentration ─────────────────────────────────────────────────────────────

HHI = 'HHI Score'                          # 0–10000; higher is more concentrated
HHI_INTERPRETATION = 'HHI Interpretation'
EFFECTIVE_POSITIONS = 'Effective Positions'
TOTAL_POSITIONS = 'Total Positions'
TOP_3_PCT = 'Top 3 Weight (%)'
TOP_5_PCT = 'Top 5 Weight (%)'
TOP_10_PCT = 'Top 10 Weight (%)'
POSITION_WEIGHTS = 'Position Weights (%)'  # {symbol: weight}, largest first

# ── Sectors ───────────────────────────────────────────────────────────────────

SECTOR_VALUES = 'Sector Allocation ($)'    # {sector: dollars}
SECTOR_WEIGHTS_PCT = 'Sector Weights (%)'  # {sector: percent of portfolio}

# ── Cash flows ────────────────────────────────────────────────────────────────

DEPOSIT_COUNT = 'Number of Deposits'
TOTAL_DEPOSITED = 'Total Deposited'
WITHDRAWAL_COUNT = 'Number of Withdrawals'
TOTAL_WITHDRAWN = 'Total Withdrawn'        # positive magnitude
NET_CASH_FLOW = 'Net Cash Flow'
LARGEST_DEPOSIT = 'Largest Deposit'
LARGEST_WITHDRAWAL = 'Largest Withdrawal'
LAST_DEPOSIT_DATE = 'Most Recent Deposit'
LAST_WITHDRAWAL_DATE = 'Most Recent Withdrawal'

#: Count of transfers counted as external only because their counterparty was
#: unrecognised. Non-zero means the numbers above are provisional.
FLOWS_NEEDING_REVIEW = 'Transfers Needing Review'

# ── Income ────────────────────────────────────────────────────────────────────

TOTAL_INCOME = 'Total Income'
INCOME_COUNT = 'Number of Income Transactions'
INCOME_BY_TYPE = 'Income by Type'          # {type: dollars}
LARGEST_INCOME = 'Largest Single Payment'
LAST_INCOME_DATE = 'Most Recent Income'

# ── Holdings quality ──────────────────────────────────────────────────────────

WINNERS = 'Winning Positions'
LOSERS = 'Losing Positions'
BREAKEVEN = 'Breakeven Positions'
WIN_RATE_PCT = 'Win Rate (%)'
BEST_PERFORMER = 'Best Performer'          # {'Symbol', 'Gain %', 'Market Value'}
WORST_PERFORMER = 'Worst Performer'
GAIN_DISTRIBUTION = 'Gain Distribution'    # {bucket label: position count}

#: Spread of position-level gain percentages. Descriptive only — this is a
#: cross-section of lifetime returns, not a time series, so it must not be fed
#: into Sharpe or Sortino. Those ratios were removed for exactly this reason.
GAIN_DISPERSION_PCT = 'Gain Dispersion (Std Dev %)'

# ── Liquidity ─────────────────────────────────────────────────────────────────

CASH_BALANCE = 'Cash Balance'
CASH_PCT = 'Cash (%)'
TOTAL_PORTFOLIO_VALUE = 'Total Portfolio Value'

# ── Trading activity ──────────────────────────────────────────────────────────

BUY_COUNT = 'Number of Buys'
SELL_COUNT = 'Number of Sells'
TOTAL_BOUGHT = 'Total Amount Bought'
TOTAL_SOLD = 'Total Amount Sold'
TURNOVER_PCT = 'Portfolio Turnover (%)'
AVG_TRADE_SIZE = 'Avg Transaction Size'


# ── Reconciliation ────────────────────────────────────────────────────────────
#
# The identity that ties the whole account together::
#
#     Portfolio Value = Net Deposits + Realised P&L + Income + Unrealised P&L
#
# Anything left over is money the transaction history cannot account for, and it
# is reported rather than absorbed. E*TRADE serves about two years; an account
# older than that has activity — an opening in-kind transfer, early deposits —
# that simply is not in the feed.

NET_DEPOSITS = 'Net Deposits'              # deposits less withdrawals
REALIZED_GAINS = 'Realised Gains'          # positive lot outcomes only
REALIZED_LOSSES = 'Realised Losses'        # negative, kept as a negative number
REALIZED_NET = 'Realised P&L'
REALIZED_BY_SYMBOL = 'Realised P&L by Symbol'
UNREALIZED_GAINS = 'Unrealised Gains'      # winning positions only
UNREALIZED_LOSSES = 'Unrealised Losses'    # negative, kept as a negative number
RECONCILED_SUBTOTAL = 'Accounted For'
UNEXPLAINED_RESIDUAL = 'Unexplained'
HISTORY_STARTS = 'History Starts'

#: Sells whose matching buy is not in the history. Their cost basis is unknown,
#: so their profit is excluded from :data:`REALIZED_NET` rather than assumed —
#: treating basis as zero would report the full proceeds as gain.
UNMATCHED_SELL_COUNT = 'Sells Without Cost Basis'
UNMATCHED_SELL_PROCEEDS = 'Proceeds Without Cost Basis'

#: Bought, never sold, and not currently held — the position went somewhere the
#: transaction feed does not explain.
ORPHAN_LOTS = 'Unexplained Open Lots'

#: How many rows were entered by hand rather than fetched. A figure resting
#: partly on manual entry must say so.
MANUAL_ROW_COUNT = 'Hand-Entered Rows'

#: Contributions against the annual IRS limit, keyed by **tax year** — which is
#: not the calendar year of payment, since a contribution made in January can be
#: designated for the year before. Read from E*TRADE's own marker rows, so it
#: survives the contribution being reclassified as an internal transfer.
IRA_CONTRIBUTIONS_BY_YEAR = 'IRA Contributions by Tax Year'

REALIZED_BASIS = 'Realised P&L Basis'
RECONCILIATION_BASIS = 'Reconciliation Basis'


def all_keys() -> set:
    """
    Every key this module defines.

    Used by ``tests/test_schema.py`` to assert that no report key escapes the
    contract — the regression guard for the drift described above.
    """
    return {
        value for name, value in globals().items()
        if name.isupper() and isinstance(value, str)
    }
