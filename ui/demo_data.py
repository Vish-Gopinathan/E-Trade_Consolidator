"""
Demo portfolio: a Berkshire-shaped book scaled to personal size.

Holdings, transactions and theses are fictional. Prices, splits and earnings come
from live market data, so the charts move like the real thing.

The analytics report is produced by the **real engine**
(:class:`portfolio.analytics.PortfolioAnalytics`), not by hand-written fixtures.
An earlier version built its own report with its own key names, which is how the
pages came to be written against keys the engine never emitted: the demo looked
correct while the live dashboard rendered em dashes. Demo data now differs from
live data only in where the numbers come from.
"""

import datetime

import pandas as pd
import streamlit as st

from portfolio import analytics, classify

# ── Holdings config ────────────────────────────────────────────────────────────
# (symbol, description, shares, avg_cost, sector, date_acquired)

_HOLDINGS = [
    ('AAPL', 'Apple Inc.',              500,  150.00, 'Technology',      '2020-02-14'),
    ('BAC',  'Bank of America Corp',   1200,   29.00, 'Financials',      '2018-11-09'),
    ('AXP',  'American Express Co',     180,  130.00, 'Financials',      '2020-09-11'),
    ('KO',   'Coca-Cola Co',            600,   43.00, 'Consumer Staples','2010-06-15'),
    ('CVX',  'Chevron Corp',            120,  148.00, 'Energy',          '2022-01-24'),
    ('OXY',  'Occidental Petroleum',    280,   55.00, 'Energy',          '2022-02-28'),
    ('MCO',  "Moody's Corp",             30,  275.00, 'Financials',      '2001-10-22'),
    ('KHC',  'Kraft Heinz Co',          450,   36.00, 'Consumer Staples','2016-07-08'),
    ('VRSN', 'VeriSign Inc',             20,  178.00, 'Technology',      '2012-06-15'),
    ('BK',   'Bank of New York Mellon', 100,   44.00, 'Financials',      '2010-09-15'),
]

_CASH = 285_000.0  # dry powder — always keep a war chest

# ── Sold positions (for What-If analysis) ─────────────────────────────────────
# (date, symbol, description, qty, price_per_share)

_SELLS = [
    ('2018-04-15', 'IBM',  'Intl Business Machines',  800,  145.00),
    ('2023-01-12', 'TSMC', 'Taiwan Semiconductor',    100,   83.00),
    ('2024-02-08', 'AAPL', 'Apple Inc.',              200,  185.00),
    ('2022-06-20', 'KHC',  'Kraft Heinz Co',          150,   36.00),
    ('2019-08-30', 'WFC',  'Wells Fargo & Co',        500,   46.00),
]

# ── Dividend income ────────────────────────────────────────────────────────────
# (symbol, description, quarterly_div_per_share, div_type)

_DIVS = [
    ('KO',  'Coca-Cola Co',              0.485, 'Qualified Dividend'),
    ('BAC', 'Bank of America Corp',      0.24,  'Qualified Dividend'),
    ('AXP', 'American Express Co',       0.70,  'Qualified Dividend'),
    ('CVX', 'Chevron Corp',              1.63,  'Qualified Dividend'),
    ('OXY', 'Occidental Petroleum',      0.22,  'Qualified Dividend'),
    ('MCO', "Moody's Corp",              0.85,  'Qualified Dividend'),
    ('BK',  'Bank of New York Mellon',   0.42,  'Qualified Dividend'),
    ('KHC', 'Kraft Heinz Co',            0.40,  'Qualified Dividend'),
]

# ── Investment thesis ─────────────────────────────────────────────────────────

DEMO_THESIS = {
    'AAPL': {
        'status': 'On Track',
        'thesis': (
            "Apple is not a technology company in the traditional sense — it is a consumer products "
            "company with an unassailable brand and one of the most loyal customer bases ever created. "
            "The iPhone ecosystem (hardware + software + services) creates switching costs so high that "
            "customers effectively pay a tax to remain in Apple's world. Services revenue is the crown "
            "jewel: high-margin, recurring, and growing 15%+ annually with almost no incremental capital."
        ),
        'entry_rationale': (
            "Bought in 2020 after recognizing that the market was pricing Apple as a cyclical hardware "
            "company rather than the consumer-loyalty machine it actually is. Services was just beginning "
            "to be understood by the market."
        ),
        'catalysts': (
            "Services revenue hitting 30%+ of total. India becoming meaningful. Vision Pro establishing "
            "a new platform. Continued buybacks retiring ~5% of shares annually."
        ),
        'target_price': 275.00,
        'hold_period': 'Long-term (5+ years)',
        'notes': [
            {'date': '2024-02-08', 'note': 'Trimmed 200 shares at $185. Still the largest position. Valuation was stretched at 30x while growth slowed.'},
            {'date': '2024-07-15', 'note': 'Services revenue Q3 2024 +14% YoY. AI integration should accelerate upgrade cycle. Thesis intact.'},
        ],
        'updated': '2024-07-15',
    },
    'KO': {
        'status': 'On Track',
        'thesis': (
            "I have been drinking Coca-Cola since I was 6 years old. The moat here is built over "
            "135 years — a brand recognized in every country on earth. Pricing power is extraordinary: "
            "KO has raised prices consistently for decades without losing material volume. "
            "The dividend has been raised for 62 consecutive years. This is not a growth stock. "
            "It is a compounding machine."
        ),
        'entry_rationale': (
            "Started buying in the late 1980s. Added through every downturn because the business "
            "never changes: people drink Coke regardless of the economy."
        ),
        'catalysts': "Emerging market volume. Premium product mix. Continued pricing power.",
        'target_price': 80.00,
        'hold_period': 'Forever',
        'notes': [
            {'date': '2023-10-20', 'note': 'Q3 2023: pricing up 9%, volume flat. Exactly right — taking price, keeping customers.'},
        ],
        'updated': '2023-10-20',
    },
    'BAC': {
        'status': 'On Track',
        'thesis': (
            "Bank of America is the best-managed large US bank. Brian Moynihan has executed "
            "flawlessly on expense discipline since the post-GFC cleanup. The interest rate environment "
            "is a tailwind: BAC's deposit base is enormous and sticky, meaning rate increases flow "
            "almost entirely to the bottom line. Trading at ~1.1x book for a bank earning 12%+ ROE is attractive."
        ),
        'entry_rationale': (
            "Built position 2018-2020. BAC has ~$1.9T in deposits, much of which reprices slowly. "
            "When rates rise, net interest income explodes. The math was obvious."
        ),
        'catalysts': "Rate environment stabilization. Buybacks. Wealth management via Merrill Lynch.",
        'target_price': 55.00,
        'hold_period': '3–5 years',
        'notes': [
            {'date': '2024-01-17', 'note': 'Q4 2023: NII $14.1B, sustainable level. Consumer deposits normalizing. Thesis intact.'},
        ],
        'updated': '2024-01-17',
    },
    'AXP': {
        'status': 'On Track',
        'thesis': (
            "American Express serves a different customer than Visa/Mastercard: affluent spenders who "
            "travel frequently and value status. This means higher transaction size, lower credit losses, "
            "and the ability to charge merchants a premium interchange because AXP holders spend more. "
            "The spend-centric model is fundamentally superior in a downturn."
        ),
        'entry_rationale': "Held since the 1960s Salad Oil Scandal. The brand and network only get stronger.",
        'catalysts': "Millennial/Gen-Z card acquisition. Travel and entertainment recovery. International.",
        'target_price': 280.00,
        'hold_period': 'Long-term',
        'notes': [
            {'date': '2024-04-22', 'note': 'Q1 2024: Millennial/Gen Z now 75% of new accounts. This is not a legacy business.'},
        ],
        'updated': '2024-04-22',
    },
    'OXY': {
        'status': 'Watch',
        'thesis': (
            "Occidental has the best acreage in the Permian Basin and the lowest break-even cost "
            "structure of any major US oil producer. Vicki Hollub has transformed the balance sheet "
            "post-COVID. At $60 oil, OXY generates substantial free cash flow. At $80, it is a cash "
            "machine. We also hold warrants from the Anadarko financing."
        ),
        'entry_rationale': "2022: oil supply/demand imbalance persisting longer than the market expected. OXY offered the highest operational leverage with management we trust.",
        'catalysts': "Oil above $70 sustained. Permian production growth. Carbon capture proving commercial.",
        'target_price': 90.00,
        'hold_period': '3–5 years',
        'notes': [
            {'date': '2024-05-10', 'note': 'Q1 2024 beat. CrownRock acquisition closing. Integration risk is the main watch item.'},
        ],
        'updated': '2024-05-10',
    },
    'MCO': {
        'status': 'On Track',
        'thesis': (
            "Moody's is a toll booth on global capital formation. Every bond issued anywhere in the "
            "world needs a rating. The duopoly with S&P is essentially unbreakable. With ~75% operating "
            "margins and a fast-growing analytics segment, MCO is one of the best businesses ever created."
        ),
        'entry_rationale': "Bought in 2001 at the IPO. Recognized immediately: an unregulated monopoly on critical financial infrastructure.",
        'catalysts': "Debt issuance volume recovery. MA analytics growing 10%+. Private credit expansion.",
        'target_price': None,
        'hold_period': 'Forever',
        'notes': [
            {'date': '2024-02-15', 'note': 'Q4 2023: issuance volumes recovering strongly. Will not sell.'},
        ],
        'updated': '2024-02-15',
    },
    'CVX': {
        'status': 'On Track',
        'thesis': (
            "Chevron is the highest-quality major oil company — conservative balance sheet, disciplined "
            "capital allocation, and shareholder-friendly management. The Hess acquisition adds world-class "
            "Guyana assets producing for 20+ years. In a world where energy transition takes longer than "
            "idealists expect, owning the best-run conventional energy company is rational."
        ),
        'entry_rationale': "2022: added CVX as the conservative, dividend-paying energy anchor alongside OXY's operational leverage.",
        'catalysts': "Hess/Guyana integration. Continued $15B+ annual buybacks. Oil stability above $70.",
        'target_price': 200.00,
        'hold_period': '5+ years',
        'notes': [],
        'updated': '2023-08-01',
    },
    'VRSN': {
        'status': 'On Track',
        'thesis': (
            "VeriSign has the US government-granted monopoly on .com and .net domain registrations. "
            "That is the entire business. They cannot lose market share — there is no competition. "
            "They can raise prices 7% per year per their ICANN contract. Operating margins are ~65%. "
            "This requires almost no capital. It is as close to a perfect business as I have seen."
        ),
        'entry_rationale': "2012: recognized that the .com monopoly was durable for decades. Every website needs a domain. The internet is not going away.",
        'catalysts': "Domain count growth. Annual ICANN price increases through 2030+. Buybacks 3-5% annually.",
        'target_price': None,
        'hold_period': 'Forever',
        'notes': [],
        'updated': '2023-01-15',
    },
    'KHC': {
        'status': 'Watch',
        'thesis': (
            "Kraft Heinz was a mistake in terms of price paid but not in terms of business quality. "
            "The underlying brands (Heinz ketchup, Kraft mac & cheese) have genuine consumer loyalty. "
            "The problem was overpaying and underestimating private-label competition. At current prices, "
            "the dividend yield and brand assets offer reasonable value."
        ),
        'entry_rationale': "Formed in the Heinz acquisition in 2016. We overpaid. Writing it down in 2019 was painful but honest.",
        'catalysts': "Volume recovery post price increases. Emerging market distribution. New CEO executing better.",
        'target_price': 42.00,
        'hold_period': '3+ years',
        'notes': [
            {'date': '2024-05-02', 'note': 'Q1 2024: volumes improving. Emerging markets showing strength. Partial trim in 2022 reduced exposure.'},
        ],
        'updated': '2024-05-02',
    },
    'BK': {
        'status': 'On Track',
        'thesis': (
            "Bank of New York Mellon is the world's largest custody bank — essential infrastructure "
            "holding and servicing $47T+ in assets. Custody is a sticky, fee-based business with minimal "
            "credit risk. BK benefits from rising markets (AUC fees), rising rates (NII), and growing "
            "institutional complexity."
        ),
        'entry_rationale': "2010: attractive post-GFC. Custody banking is a non-disruptable oligopoly alongside State Street and Northern Trust.",
        'catalysts': "AUC growth. Technology modernization improving margins. Buybacks at attractive valuations.",
        'target_price': 65.00,
        'hold_period': '3–5 years',
        'notes': [],
        'updated': '2022-09-10',
    },
}

# ── Price fetching ─────────────────────────────────────────────────────────────

@st.cache_data(ttl=300, show_spinner=False)
def _fetch_live_prices(symbols: tuple) -> dict:
    import yfinance as yf
    prices = {}
    for sym in symbols:
        try:
            p = yf.Ticker(sym).fast_info.last_price
            prices[sym] = float(p) if p else None
        except Exception:
            prices[sym] = None
    return prices


# ── Transaction helpers ────────────────────────────────────────────────────────

def _row(date_str, sym, desc, txn_type, category, qty, price, value):
    return {
        'Date': pd.Timestamp(date_str),
        'Security Name': desc,
        'Transaction Type': txn_type,
        'Category': category,
        'Quantity': qty,
        'Price': price,
        'Total Value': value,
        'Symbol': sym,
    }


def _build_transactions() -> pd.DataFrame:
    rows = []

    # Deposits.
    #
    # These must roughly fund the holdings: total deposits ≈ cost basis (~$225k)
    # plus the cash balance (~$285k). They previously summed to $1.33M against a
    # $705k portfolio, which is not a demo of anything — deposit-adjusted return
    # correctly reported -76%, because on those numbers the account really had
    # lost most of the money put into it.
    for d, amt in [
        ('2010-01-15', 95_000), ('2012-03-20', 55_000), ('2015-06-10', 75_000),
        ('2018-01-08', 115_000), ('2020-04-02', 95_000), ('2022-01-20', 65_000),
    ]:
        rows.append(_row(d, '', 'BROKERAGE DEPOSIT', 'Deposit', 'Deposit', None, None, float(amt)))

    # Buy tranches per holding
    buys = {
        'AAPL': [('2020-02-14', 300, 153.00), ('2020-03-23', 200, 142.00)],
        'BAC':  [('2018-11-09', 800, 27.50), ('2018-12-24', 400, 23.80)],
        'AXP':  [('2020-09-11', 180, 130.00)],
        'KO':   [('2010-06-15', 200, 25.80), ('2015-08-24', 200, 38.40), ('2020-03-18', 200, 44.60)],
        'CVX':  [('2022-01-24', 120, 118.50)],
        'OXY':  [('2022-02-28', 140, 47.30), ('2022-04-11', 140, 62.70)],
        'MCO':  [('2001-10-22', 30, 14.50)],
        'KHC':  [('2016-07-08', 450, 91.50)],
        'VRSN': [('2012-06-15', 20, 34.00)],
        'BK':   [('2010-09-15', 100, 23.80)],
    }
    sym_to_desc = {sym: desc for sym, desc, *_ in _HOLDINGS}
    for sym, tranches in buys.items():
        desc = sym_to_desc.get(sym, sym)
        for d, qty, price in tranches:
            rows.append(_row(d, sym, desc, 'Bought', 'Trade', float(qty), price, float(qty) * price))

    # Sells
    for d, sym, desc, qty, price in _SELLS:
        rows.append(_row(d, sym, desc, 'Sold', 'Trade', -float(qty), price, -float(qty) * price))

    # Quarterly dividends (8 quarters back)
    sym_shares = {sym: shares for sym, _, shares, *_ in _HOLDINGS}
    today = datetime.date.today()
    for sym, desc, div_per_share, div_type in _DIVS:
        shares = sym_shares.get(sym, 0)
        amount = div_per_share * shares
        for q in range(8):
            div_date = today - datetime.timedelta(days=90 * q + 30)
            rows.append(_row(div_date.isoformat(), sym, desc, div_type, 'Income', None, None, round(amount, 2)))

    df = pd.DataFrame(rows).sort_values('Date', ascending=False).reset_index(drop=True)
    return df


def _classified(txns_df: pd.DataFrame) -> pd.DataFrame:
    """Run demo transactions through the same classification the live app uses."""
    frame = txns_df.copy()
    frame['Ref ID'] = frame['Security Name'].map(classify.parse_ref_id)
    frame['Counterparty'] = frame['Security Name'].map(classify.parse_counterparty)
    frame['Account'] = 'Demo Brokerage'
    return classify.reconcile_transfers(frame)


# ── Main public builder ────────────────────────────────────────────────────────

def build_demo_portfolio() -> dict:
    """Return a portfolio dict ready to store in st.session_state.portfolio."""
    symbols = tuple(sym for sym, *_ in _HOLDINGS)
    prices = _fetch_live_prices(symbols)

    rows = []
    for sym, desc, shares, avg_cost, sector, acquired in _HOLDINGS:
        current_price = prices.get(sym) or avg_cost
        market_val = shares * current_price
        total_cost = shares * avg_cost
        gain = market_val - total_cost
        gain_pct = (gain / total_cost * 100) if total_cost else 0
        rows.append({
            'Symbol': sym,
            'Symbol Description': desc,
            'Current Price': current_price,
            'Quantity': float(shares),
            'Date Acquired': pd.Timestamp(acquired),
            'Price Paid': avg_cost,
            'Total Cost': total_cost,
            'Market Value': market_val,
            'Total Gain': gain,
            'Total Gain %': gain_pct,
            'Percent of Portfolio': 0.0,
        })

    holdings_df = pd.DataFrame(rows)
    total_securities = float(holdings_df['Market Value'].sum())
    total_portfolio = total_securities + _CASH

    holdings_df['Percent of Portfolio'] = holdings_df['Market Value'] / total_portfolio * 100
    holdings_df = holdings_df.sort_values('Market Value', ascending=False).reset_index(drop=True)

    cash_row = pd.DataFrame([{
        'Symbol': 'CASH',
        'Symbol Description': 'Cash & Equivalents',
        'Current Price': 1.0,
        'Quantity': _CASH,
        'Date Acquired': pd.NaT,
        'Price Paid': 1.0,
        'Total Cost': _CASH,
        'Market Value': _CASH,
        'Total Gain': 0.0,
        'Total Gain %': 0.0,
        'Percent of Portfolio': _CASH / total_portfolio * 100,
    }])
    holdings_df = pd.concat([holdings_df, cash_row], ignore_index=True)

    txns_df = _classified(_build_transactions())
    cash_flows_df = classify.get_cash_flows(txns_df)
    income_df = classify.get_income(txns_df)

    stocks_only = holdings_df[holdings_df['Symbol'] != 'CASH']
    total_cost_basis = float(stocks_only['Total Cost'].sum())
    total_gain = float(stocks_only['Total Gain'].sum())

    analytics_report = analytics.PortfolioAnalytics(
        holdings_df, txns_df, cash_flows_df,
    ).generate_full_report()

    summary = {
        'Total Stocks': len(stocks_only),
        'Total Stock Market Value': round(total_securities, 2),
        'Cash': round(_CASH, 2),
        'Total Portfolio Value': round(total_portfolio, 2),
        'Total Cost Basis': round(total_cost_basis, 2),
        'Total Unrealized Gain': round(total_gain, 2),
        'Total Unrealized Gain %': round((total_gain / total_cost_basis * 100) if total_cost_basis else 0, 2),
        'Cash Percentage': round(_CASH / total_portfolio * 100, 2),
        'Largest Holdings': stocks_only.head(3)[['Symbol', 'Market Value', 'Percent of Portfolio']].to_dict('records'),
    }

    return {
        'fetched_at': datetime.datetime.now().isoformat(),
        'holdings': holdings_df,
        'transactions': txns_df,
        'cash_flows': cash_flows_df,
        'income': income_df,
        'analytics_report': analytics_report,
        'summary': summary,
    }
