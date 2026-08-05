import hmac
import os
import sys
import datetime
from pathlib import Path

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / '.env')

sys.path.insert(0, str(Path(__file__).parent))

from lib.etrade_session import get_oauth_url, complete_oauth
from lib.data_cache import save_portfolio, load_portfolio
from lib.common import render_sidebar_status, get_secret

_MAX_LOGIN_ATTEMPTS = 5
_LOCKOUT_MINUTES = 15
_SESSION_TIMEOUT_HOURS = 8

st.set_page_config(
    page_title='Portfolio Dashboard',
    page_icon='📈',
    layout='wide',
    initial_sidebar_state='expanded',
)

# ── Helper ────────────────────────────────────────────────────────────────────

def _refresh_data(start_date, end_date):
    import math
    import consolidator as c
    import analytics as a

    auth_tokens = st.session_state['auth_tokens']

    with st.status('Refreshing portfolio data…', expanded=True) as status:

        # ── Step 1: accounts ─────────────────────────────────────────────────
        st.write('🔗 Connecting to E-Trade…')
        try:
            active_accounts, accounts_obj = c.fetch_active_accounts(auth_tokens)
            st.session_state['active_accounts'] = active_accounts
            st.session_state['accounts_obj'] = accounts_obj
        except Exception as e:
            status.update(label='Connection failed', state='error', expanded=True)
            st.error(f'Failed to fetch accounts: {e}')
            return

        n_accounts = len(active_accounts)
        st.write(f'✅ Found {n_accounts} active account(s)')

        # ── Step 2: holdings ─────────────────────────────────────────────────
        st.write('📊 Fetching holdings and cash balances…')
        try:
            all_holdings = []
            total_account_balance = 0.0  # authoritative total — matches E*TRADE website
            for key in active_accounts['accountIdKey']:
                df = c.get_portfolio(accounts_obj, key)
                all_holdings.append(df)
                totals = c.get_account_totals(accounts_obj, key)
                total_account_balance += totals['account_balance']

            combined_df = pd.concat(all_holdings, ignore_index=True)
            # Derive cash as the residual: account total minus what positions already account for
            total_positions_value = combined_df['Market Value'].sum()
            cash_for_display = max(total_account_balance - total_positions_value, 0.0)

            holdings_df = c.consolidate_holdings(combined_df, cash=cash_for_display)
        except Exception as e:
            status.update(label='Failed to fetch holdings', state='error', expanded=True)
            st.error(f'Failed to fetch holdings: {e}')
            return

        n_holdings = len(holdings_df[holdings_df['Symbol'] != 'CASH']) if not holdings_df.empty else 0
        st.write(f'✅ Holdings loaded — {n_holdings} positions, ${cash_for_display:,.2f} cash')

        # ── Step 3: transactions with progress bar ────────────────────────────
        days = (end_date - start_date).days + 1
        n_chunks = max(1, math.ceil(days / 89))
        total_steps = n_accounts * n_chunks
        st.write(
            f'🔄 Fetching transactions ({start_date} → {end_date}, '
            f'{n_chunks} date chunk(s) × {n_accounts} account(s))…'
        )
        progress = st.progress(0, text='Starting transaction fetch…')

        try:
            all_txn_frames = []
            for acct_idx, key in enumerate(active_accounts['accountIdKey']):
                progress.progress(
                    acct_idx / n_accounts,
                    text=f'Account {acct_idx + 1}/{n_accounts} — fetching transactions…',
                )
                acct_txns = c.get_consolidated_transactions(accounts_obj, key, start_date, end_date)
                all_txn_frames.append(acct_txns)
                progress.progress(
                    (acct_idx + 1) / n_accounts,
                    text=f'Account {acct_idx + 1}/{n_accounts} — done',
                )

            progress.progress(1.0, text='All accounts processed')

            if all_txn_frames:
                transactions_df = pd.concat(all_txn_frames, ignore_index=True)
                if not transactions_df.empty and 'Date' in transactions_df.columns:
                    transactions_df = transactions_df.sort_values('Date', ascending=False).reset_index(drop=True)
            else:
                transactions_df = pd.DataFrame()

        except Exception as e:
            status.update(label='Failed to fetch transactions', state='error', expanded=True)
            st.error(f'Failed to fetch transactions: {e}')
            return

        n_txns = len(transactions_df)
        st.write(f'✅ Transactions loaded — {n_txns:,} record(s)')

        # ── Step 4: analytics ─────────────────────────────────────────────────
        st.write('🧮 Computing analytics…')
        try:
            cash_flows_df = c.get_cash_flows(transactions_df)
            income_df = None
            if not transactions_df.empty and 'Category' in transactions_df.columns:
                income_rows = transactions_df[transactions_df['Category'] == 'Income'].copy()
                if not income_rows.empty:
                    income_df = income_rows[['Date', 'Security Name', 'Total Value', 'Transaction Type']].rename(
                        columns={'Security Name': 'Description'}
                    )

            analytics = a.PortfolioAnalytics(
                holdings_df,
                transactions_df if not transactions_df.empty else None,
                cash_flows_df if not cash_flows_df.empty else None,
            )
            analytics_report = analytics.generate_full_report()
            summary = c.portfolio_summary(holdings_df, cash=cash_for_display)
        except Exception as e:
            status.update(label='Failed to compute analytics', state='error', expanded=True)
            st.error(f'Failed to compute analytics: {e}')
            return

        st.write('✅ Analytics complete')
        status.update(label='Portfolio data refreshed!', state='complete', expanded=False)

    fetched_at = datetime.datetime.now().isoformat()
    portfolio = {
        'fetched_at': fetched_at,
        'holdings': holdings_df,
        'transactions': transactions_df,
        'cash_flows': cash_flows_df,
        'income': income_df if income_df is not None else pd.DataFrame(),
        'analytics_report': analytics_report,
        'summary': summary,
    }
    st.session_state.portfolio = portfolio

    try:
        save_portfolio(
            holdings_df, transactions_df, cash_flows_df,
            income_df if income_df is not None else pd.DataFrame(),
            analytics_report, summary, fetched_at,
        )
    except Exception:
        pass

    st.rerun()


# ── Session timeout check ─────────────────────────────────────────────────────

if st.session_state.get('authenticated'):
    last_activity = st.session_state.get('last_activity')
    if last_activity:
        idle_seconds = (datetime.datetime.now() - last_activity).total_seconds()
        if idle_seconds > _SESSION_TIMEOUT_HOURS * 3600:
            for k in list(st.session_state.keys()):
                del st.session_state[k]
            st.warning('Session expired after inactivity. Please log in again.')
    st.session_state.last_activity = datetime.datetime.now()

# ── Password gate ────────────────────────────────────────────────────────────

APP_PASSWORD = get_secret('APP_PASSWORD')

if not st.session_state.get('authenticated'):
    st.title('Portfolio Dashboard')

    # Brute-force lockout
    locked_until = st.session_state.get('locked_until')
    if locked_until and datetime.datetime.now() < locked_until:
        remaining_min = int((locked_until - datetime.datetime.now()).total_seconds() // 60) + 1
        st.error(f'Too many failed attempts. Try again in {remaining_min} minute(s).')
        st.stop()

    st.markdown('Please enter the app password to continue.')
    pwd = st.text_input('Password', type='password', key='login_pwd')
    if st.button('Login') or pwd:
        if APP_PASSWORD and hmac.compare_digest(pwd.encode(), APP_PASSWORD.encode()):
            st.session_state.authenticated = True
            st.session_state.login_attempts = 0
            st.session_state.last_activity = datetime.datetime.now()
            st.rerun()
        elif pwd:
            attempts = st.session_state.get('login_attempts', 0) + 1
            st.session_state.login_attempts = attempts
            if attempts >= _MAX_LOGIN_ATTEMPTS:
                st.session_state.locked_until = (
                    datetime.datetime.now() + datetime.timedelta(minutes=_LOCKOUT_MINUTES)
                )
                st.error(f'Too many failed attempts. Locked for {_LOCKOUT_MINUTES} minutes.')
            else:
                remaining = _MAX_LOGIN_ATTEMPTS - attempts
                st.error(f'Incorrect password. {remaining} attempt(s) remaining before lockout.')
    st.stop()

# ── Load cache on first run ──────────────────────────────────────────────────

if 'portfolio' not in st.session_state:
    cached = load_portfolio()
    if cached:
        st.session_state.portfolio = cached

# ── Sidebar: E-Trade connection + refresh ────────────────────────────────────

with st.sidebar:
    st.title('📈 Portfolio')

    render_sidebar_status()

    st.markdown('---')
    with st.expander('🔌 E-Trade Connection', expanded=not st.session_state.get('etrade_connected')):
        if not st.session_state.get('etrade_connected'):
            if st.button('Get Authorization URL'):
                try:
                    url, oauth_obj, ck, cs = get_oauth_url()
                    st.session_state['_oauth_obj'] = oauth_obj
                    st.session_state['_consumer_key'] = ck
                    st.session_state['_consumer_secret'] = cs
                    st.session_state['_oauth_url'] = url
                except Exception as e:
                    st.error(f'Failed to get OAuth URL: {e}')

            if st.session_state.get('_oauth_url'):
                st.link_button('Authorize on E-Trade ↗', st.session_state['_oauth_url'])
                verifier = st.text_input('Paste verifier code', key='verifier_input')
                if st.button('Connect') and verifier.strip():
                    try:
                        tokens = complete_oauth(
                            st.session_state['_oauth_obj'],
                            verifier.strip(),
                            st.session_state['_consumer_key'],
                            st.session_state['_consumer_secret'],
                        )
                        st.session_state['auth_tokens'] = tokens
                        st.session_state['etrade_connected'] = True
                        for k in ('_oauth_obj', '_consumer_key', '_consumer_secret', '_oauth_url'):
                            st.session_state.pop(k, None)
                        st.success('Connected!')
                        st.rerun()
                    except Exception as e:
                        st.error(f'Connection failed: {e}')
        else:
            st.success('Connected to E-Trade')
            if st.button('Disconnect'):
                for k in ('etrade_connected', 'auth_tokens', 'active_accounts', 'accounts_obj'):
                    st.session_state.pop(k, None)
                st.rerun()

    if st.session_state.get('etrade_connected'):
        st.markdown('---')
        st.markdown('**Refresh Data**')
        default_start = datetime.date(datetime.date.today().year, 1, 1)
        start_date = st.date_input('Start date', value=default_start, key='refresh_start')
        end_date = st.date_input('End date', value=datetime.date.today(), key='refresh_end')

        if st.button('🔄 Refresh Data', type='primary'):
            _refresh_data(start_date, end_date)

# ── Home page ─────────────────────────────────────────────────────────────────

st.title('Portfolio Dashboard')

portfolio = st.session_state.get('portfolio')

if not portfolio:
    st.info('No data loaded. Connect to E-Trade and click **Refresh Data**, or check that `data/portfolio_cache.json` exists.')
    st.stop()

summary = portfolio.get('summary') or {}
fetched_at = portfolio.get('fetched_at', '')

if fetched_at:
    ts = fetched_at[:19].replace('T', ' ')
    label = '🟢 Live data' if st.session_state.get('etrade_connected') else '🟡 Cached data'
    st.caption(f'{label} — last updated {ts}')

# KPI row
total_value = summary.get('Total Portfolio Value', 0)
total_gain = summary.get('Total Unrealized Gain', 0)
gain_pct = summary.get('Total Unrealized Gain %', 0)
cash_pct = summary.get('Cash Percentage', 0)
analytics_report = portfolio.get('analytics_report') or {}
perf = analytics_report.get('Performance Metrics') or {}
dietz = perf.get('Modified Dietz Return (%)')

col1, col2, col3, col4, col5 = st.columns(5)
col1.metric('Portfolio Value', f'${total_value:,.2f}')
col2.metric('Unrealized Gain', f'${total_gain:,.2f}', f'{gain_pct:.2f}%')
col3.metric('Cash', f'{cash_pct:.1f}%')
col4.metric('Holdings', summary.get('Total Stocks', '—'))
if dietz is not None:
    col5.metric('Deposit-Adj. Return', f'{dietz:.2f}%')

st.markdown('---')

# Top holdings preview
holdings_df = portfolio.get('holdings')
if holdings_df is not None and not holdings_df.empty:
    st.subheader('Top Holdings')
    display_cols = [c for c in [
        'Symbol', 'Symbol Description', 'Market Value', 'Total Gain', 'Total Gain %', 'Percent of Portfolio'
    ] if c in holdings_df.columns]
    display = holdings_df[holdings_df['Symbol'] != 'CASH'][display_cols].sort_values(
        'Market Value', ascending=False
    ).head(10)
    st.dataframe(display, use_container_width=True, hide_index=True)

# Analytics snapshot
if analytics_report:
    st.markdown('---')
    st.subheader('Analytics Snapshot')
    cols = st.columns(3)
    conc = analytics_report.get('Concentration Analysis') or {}
    risk = analytics_report.get('Risk Metrics') or {}

    with cols[0]:
        st.markdown('**Concentration**')
        hhi = conc.get('HHI Score')
        if hhi:
            st.metric('HHI Score', f'{hhi:.1f}')
        top5 = conc.get('Top 5 Concentration (%)')
        if top5:
            st.metric('Top 5 Weight', f'{top5:.1f}%')

    with cols[1]:
        st.markdown('**Risk / Performance**')
        sharpe = risk.get('Sharpe Ratio')
        if sharpe is not None:
            st.metric('Sharpe Ratio', f'{sharpe:.2f}')
        win_rate = risk.get('Win Rate (%)')
        if win_rate is not None:
            st.metric('Win Rate', f'{win_rate:.1f}%')

    with cols[2]:
        st.markdown('**Income**')
        inc_summary = analytics_report.get('Income Summary') or {}
        total_income = inc_summary.get('Total Income')
        if total_income:
            st.metric('Total Income', f'${total_income:,.2f}')
        n_inc = inc_summary.get('Number of Income Transactions')
        if n_inc:
            st.metric('Income Transactions', n_inc)
