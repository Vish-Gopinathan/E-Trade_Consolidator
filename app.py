"""
Dashboard entry point: authentication, navigation, and the E*TRADE refresh.

    streamlit run app.py

Page order and grouping come from :func:`st.navigation` at the bottom of this
file rather than from numeric filename prefixes, so reordering the app is a
one-line edit here.
"""

import datetime
import hashlib
import hmac
import io
import logging
import traceback

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

from portfolio import paths

load_dotenv(paths.ROOT / '.env')

from portfolio import analytics, classify, etrade, excel, schema  # noqa: E402
from portfolio.storage import accounts as account_map_store       # noqa: E402
from portfolio.storage import cache, snapshot                     # noqa: E402
from ui.common import get_secret, is_guest, money, render_sidebar_status  # noqa: E402

LOGGER = logging.getLogger(__name__)

_MAX_LOGIN_ATTEMPTS = 5
_LOCKOUT_MINUTES = 15
_SESSION_TIMEOUT_HOURS = 8

st.set_page_config(
    page_title='Portfolio Dashboard',
    page_icon='📈',
    layout='wide',
    initial_sidebar_state='expanded',
)


# ── Authentication ────────────────────────────────────────────────────────────

def _password_matches(entered: str, secret: str) -> bool:
    """
    Constant-time comparison against a stored password or salted hash.

    ``APP_PASSWORD_HASH`` holds ``salt$sha256(salt + password)``; a plain
    ``APP_PASSWORD`` still works so an existing install keeps running, but the
    hashed form keeps the password out of the environment in readable form.
    """
    if not entered or not secret:
        return False
    if '$' in secret:
        salt, expected = secret.split('$', 1)
        digest = hashlib.sha256((salt + entered).encode()).hexdigest()
        return hmac.compare_digest(digest, expected)
    return hmac.compare_digest(entered.encode(), secret.encode())


def _login_gate() -> None:
    """
    Render the password prompt and stop the script until it is satisfied.

    The attempt counter lives in ``st.session_state``, so it slows down a person
    guessing in one browser tab but does **not** survive a reload and is no
    defence against a scripted attacker. It is a speed bump. Anything reachable
    from the public internet needs a real authenticating proxy in front of it —
    see docs/GUIDE.md.
    """
    st.title('Portfolio Dashboard')

    locked_until = st.session_state.get('locked_until')
    if locked_until and datetime.datetime.now() < locked_until:
        minutes = int((locked_until - datetime.datetime.now()).total_seconds() // 60) + 1
        st.error(f'Too many failed attempts. Try again in {minutes} minute(s).')
        st.stop()

    app_secret = get_secret('APP_PASSWORD_HASH') or get_secret('APP_PASSWORD')
    guest_secret = get_secret('GUEST_PASSWORD')

    if not app_secret:
        st.error(
            'No app password is configured. Copy `.env.example` to `.env` and set '
            '`APP_PASSWORD_HASH`, or add it to Streamlit secrets.'
        )
        st.stop()

    st.markdown('Enter the app password to continue.')
    entered = st.text_input('Password', type='password', key='login_pwd')
    submitted = st.button('Log in', type='primary')

    if not (submitted or entered):
        st.stop()

    role = None
    if _password_matches(entered, app_secret):
        role = 'admin'
    elif guest_secret and _password_matches(entered, guest_secret):
        role = 'guest'

    if role:
        st.session_state.update(
            authenticated=True, role=role, login_attempts=0,
            last_activity=datetime.datetime.now(),
        )
        st.rerun()

    if entered:
        attempts = st.session_state.get('login_attempts', 0) + 1
        st.session_state.login_attempts = attempts
        if attempts >= _MAX_LOGIN_ATTEMPTS:
            st.session_state.locked_until = (
                datetime.datetime.now() + datetime.timedelta(minutes=_LOCKOUT_MINUTES)
            )
            st.error(f'Too many failed attempts. Locked for {_LOCKOUT_MINUTES} minutes.')
        else:
            st.error(
                f'Incorrect password. {_MAX_LOGIN_ATTEMPTS - attempts} attempt(s) '
                'remaining before lockout.'
            )
    st.stop()


def _enforce_session_timeout() -> None:
    """Clear the session after a long idle period."""
    last_activity = st.session_state.get('last_activity')
    if last_activity:
        idle_hours = (datetime.datetime.now() - last_activity).total_seconds() / 3600
        if idle_hours > _SESSION_TIMEOUT_HOURS:
            st.session_state.clear()
            st.warning('Session expired after inactivity. Please log in again.')
            st.stop()
    st.session_state.last_activity = datetime.datetime.now()


# ── Data refresh ──────────────────────────────────────────────────────────────

def _refresh_data(start_date, end_date) -> None:
    """
    Pull everything from E*TRADE and rebuild the session portfolio.

    Each step reports its own failure rather than collapsing into one generic
    error, because "connection failed" and "analytics failed" call for entirely
    different responses from the user.
    """
    import math

    auth_tokens = st.session_state['auth_tokens']

    with st.status('Refreshing portfolio data…', expanded=True) as status:
        st.write('🔗 Connecting to E\\*TRADE…')
        try:
            active_accounts, accounts_obj = etrade.fetch_active_accounts(auth_tokens)
        except Exception as exc:
            status.update(label='Connection failed', state='error')
            st.error(f'Could not fetch accounts: {exc}')
            return
        st.session_state['active_accounts'] = active_accounts
        st.session_state['accounts_obj'] = accounts_obj
        st.write(f'✅ {len(active_accounts)} active account(s)')

        st.write('📊 Fetching positions and balances…')
        try:
            frames, balances = [], []
            for key in active_accounts['accountIdKey']:
                frames.append(etrade.get_portfolio(accounts_obj, key))
                balances.append(etrade.get_account_totals(accounts_obj, key))

            positions = [f for f in frames if not f.empty]
            combined = pd.concat(positions, ignore_index=True) if positions else pd.DataFrame()

            # Cash is what E*TRADE reports as free cash, not a residual. Deriving
            # it by subtraction produced a negative number that clamped to zero.
            cash = sum(b['net_cash'] for b in balances)
            reported_total = sum(b['total_account_value'] for b in balances)
            holdings = etrade.consolidate_holdings(combined, cash=cash)
        except Exception as exc:
            status.update(label='Failed to fetch holdings', state='error')
            st.error(f'Could not fetch holdings: {exc}')
            return

        positions_value = float(combined['Market Value'].sum()) if not combined.empty else 0.0
        st.write(
            f'✅ {len(holdings[holdings["Symbol"] != "CASH"])} positions '
            f'({money(positions_value)}) · {money(cash)} cash'
        )

        drift = abs(reported_total - (positions_value + cash))
        if reported_total and drift > max(50.0, reported_total * 0.005):
            st.warning(
                f'E\\*TRADE reports a total account value of {money(reported_total)}, '
                f'but positions plus cash come to {money(positions_value + cash)} — '
                f'a {money(drift)} difference. Usually unsettled trades or a pending '
                'transfer; check the per-account breakdown on Overview.'
            )

        days = (end_date - start_date).days + 1
        chunks = max(1, math.ceil(days / 89))
        st.write(f'🔄 Fetching transactions ({start_date} → {end_date}, {chunks} window(s))…')
        progress = st.progress(0.0, text='Starting…')
        try:
            frames = []
            accounts = list(active_accounts.itertuples())
            for index, account in enumerate(accounts):
                progress.progress(
                    index / len(accounts),
                    text=f'Account {index + 1} of {len(accounts)}…',
                )
                frames.append(etrade.get_consolidated_transactions(
                    accounts_obj, account.accountIdKey, start_date, end_date,
                    account_label=getattr(account, 'accountName', None) or account.accountIdKey,
                ))
            progress.progress(1.0, text='All accounts fetched')

            frames = [f for f in frames if not f.empty]
            transactions = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
            if not transactions.empty:
                transactions = classify.reconcile_transfers(
                    transactions,
                    own_accounts=active_accounts.get('accountId', pd.Series(dtype=str)).tolist(),
                    account_map=account_map_store.load(),
                )
                transactions = transactions.sort_values(
                    'Date', ascending=False).reset_index(drop=True)
        except Exception as exc:
            status.update(label='Failed to fetch transactions', state='error')
            st.error(f'Could not fetch transactions: {exc}')
            return
        st.write(f'✅ {len(transactions):,} transaction(s)')

        st.write('🧮 Computing analytics…')
        try:
            cash_flows = classify.get_cash_flows(transactions)
            income = classify.get_income(transactions)
            report = analytics.PortfolioAnalytics(
                holdings, transactions, cash_flows
            ).generate_full_report()
            summary = etrade.portfolio_summary(holdings, cash=cash)
        except Exception as exc:
            status.update(label='Failed to compute analytics', state='error')
            st.error(f'Could not compute analytics: {exc}')
            st.exception(exc)
            return

        needing_review = report[schema.CASH_FLOWS].get(schema.FLOWS_NEEDING_REVIEW, 0)
        if needing_review:
            st.write(
                f'⚠️ {needing_review} transfer(s) counted as external because the '
                'counterparty account is unrecognised — resolve on Cash Flows'
            )
        status.update(label='Portfolio data refreshed', state='complete', expanded=False)

    portfolio = {
        'fetched_at': datetime.datetime.now().isoformat(),
        'holdings': holdings,
        'transactions': transactions,
        'cash_flows': cash_flows,
        'income': income,
        'analytics_report': report,
        'summary': summary,
        'reported_total': reported_total,
        'account_balances': [
            {k: v for k, v in b.items() if k != 'raw_computed'} for b in balances
        ],
    }
    st.session_state.portfolio = portfolio
    st.session_state.pop('_is_snapshot', None)

    try:
        cache.save_portfolio(portfolio)
    except Exception as exc:
        # Previously a bare `except: pass`, so a portfolio that failed to cache
        # looked identical to one that saved — and vanished on the next restart.
        LOGGER.exception('portfolio cache write failed')
        st.warning(f'Data loaded but could not be cached to disk: {exc}')

    st.rerun()


# ── Sidebar ───────────────────────────────────────────────────────────────────

@st.cache_data(show_spinner='Building workbook…')
def _build_workbook(kind: str, fetched_at: str) -> bytes:
    """
    Build an Excel workbook for download.

    Keyed on ``fetched_at`` so it is built once per dataset and served instantly
    afterwards. The portfolio is read from session state rather than passed in,
    because DataFrames are not hashable as cache keys.
    """
    portfolio = st.session_state.portfolio
    buffer = io.BytesIO()

    def _frame(key):
        frame = portfolio.get(key)
        return frame if frame is not None and not frame.empty else None

    if kind == 'holdings':
        excel.export_to_excel(
            portfolio['holdings'],
            transactions_df=_frame('transactions'),
            cash_flows_df=_frame('cash_flows'),
            income_df=_frame('income'),
            output=buffer,
        )
    else:
        excel.export_analytics_to_excel(
            portfolio['holdings'], portfolio.get('analytics_report') or {}, output=buffer,
        )
    return buffer.getvalue()


def _render_downloads() -> None:
    """
    Download buttons for both workbooks.

    These are plain download buttons, not a Generate button that reveals one. In
    that older two-step flow the download button was created inside
    ``if st.button(...)``, so the next rerun — including the one the download
    itself triggers — destroyed it before it could reliably be used.
    """
    portfolio = st.session_state.get('portfolio')
    if not portfolio or is_guest():
        return

    fetched_at = portfolio.get('fetched_at', '')
    file_date = (
        portfolio.get('snapshot_date') or fetched_at[:10]
        or datetime.date.today().isoformat()
    )

    st.markdown('---')
    st.markdown('**Download**')
    for kind, label, stem in (
        ('holdings', '⬇ Holdings & transactions', 'portfolio'),
        ('analytics', '⬇ Analytics', 'analytics'),
    ):
        try:
            st.download_button(
                label,
                _build_workbook(kind, fetched_at),
                f'{stem}_{file_date}.xlsx',
                mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                key=f'dl_{kind}',
                use_container_width=True,
            )
        except Exception:
            st.error(f'Could not build the {kind} workbook.')
            with st.expander('Details'):
                st.code(traceback.format_exc())


def _render_connection() -> None:
    """E*TRADE OAuth: get a URL, paste the verifier, connect."""
    connected = st.session_state.get('etrade_connected')
    with st.expander('🔌 E\\*TRADE Connection', expanded=not connected):
        if connected:
            st.success('Connected')
            if st.button('Disconnect', use_container_width=True):
                for key in ('etrade_connected', 'auth_tokens', 'active_accounts', 'accounts_obj'):
                    st.session_state.pop(key, None)
                st.rerun()
            return

        if st.button('Get authorization URL', use_container_width=True):
            try:
                url, oauth, key, secret = etrade.get_oauth_url()
                st.session_state.update(
                    _oauth_obj=oauth, _consumer_key=key,
                    _consumer_secret=secret, _oauth_url=url,
                )
            except Exception as exc:
                st.error(f'Could not start authorization: {exc}')

        if st.session_state.get('_oauth_url'):
            st.link_button('Authorize on E\\*TRADE ↗', st.session_state['_oauth_url'],
                           use_container_width=True)
            verifier = st.text_input('Verifier code', key='verifier_input')
            if st.button('Connect', type='primary', use_container_width=True) and verifier.strip():
                try:
                    st.session_state['auth_tokens'] = etrade.complete_oauth(
                        st.session_state['_oauth_obj'], verifier.strip(),
                        st.session_state['_consumer_key'],
                        st.session_state['_consumer_secret'],
                    )
                    st.session_state['etrade_connected'] = True
                    for key in ('_oauth_obj', '_consumer_key', '_consumer_secret', '_oauth_url'):
                        st.session_state.pop(key, None)
                    st.rerun()
                except Exception as exc:
                    st.error(f'Connection failed: {exc}')


def _render_snapshot_tools() -> None:
    """
    Save, export and restore a point-in-time snapshot.

    Snapshots used to be written to this repo through the GitHub Contents API,
    which would have published holdings and full transaction history to a public
    repository. They are local files now; the download/upload pair is how you move
    one between machines, under your control rather than automatically.
    """
    with st.expander('📅 Snapshot'):
        st.caption(
            'A saved point-in-time copy. Guests and cold starts see this. '
            'Stored locally — download it to keep a copy elsewhere.'
        )
        portfolio = st.session_state.get('portfolio')

        if portfolio and st.button('💾 Save current data as snapshot', use_container_width=True):
            try:
                snapshot.save(portfolio)
                st.session_state.portfolio['snapshot_date'] = datetime.date.today().isoformat()
                st.success(f'Snapshot saved — {datetime.date.today():%B %d, %Y}')
            except Exception as exc:
                LOGGER.exception('snapshot save failed')
                st.error(f'Could not save the snapshot: {exc}')

        if snapshot.exists():
            st.download_button(
                '⬇ Export snapshot file', snapshot.read_bytes(),
                f'portfolio_snapshot_{datetime.date.today().isoformat()}.json',
                mime='application/json', use_container_width=True,
            )

        uploaded = st.file_uploader('Restore a snapshot file', type='json')
        if uploaded is not None and st.button('Restore', use_container_width=True):
            try:
                st.session_state.portfolio = snapshot.load_bytes(uploaded.getvalue())
                st.session_state._is_snapshot = True
                st.success('Snapshot restored.')
                st.rerun()
            except Exception as exc:
                st.error(f'Could not read that snapshot: {exc}')


def _render_sidebar() -> None:
    st.title('📈 Portfolio')
    render_sidebar_status()

    if is_guest():
        st.info('👁️ Guest view — read-only')
        return

    st.markdown('---')
    _render_connection()

    if st.session_state.get('etrade_connected'):
        st.markdown('**Refresh from E\\*TRADE**')
        start_date = st.date_input(
            'From', value=datetime.date(2000, 1, 1),
            min_value=datetime.date(1990, 1, 1), max_value=datetime.date.today(),
            help='Reaching back past account opening is what makes the '
                 'deposit-adjusted return meaningful.',
        )
        end_date = st.date_input(
            'To', value=datetime.date.today(),
            min_value=datetime.date(1990, 1, 1), max_value=datetime.date.today(),
        )
        if st.button('🔄 Refresh data', type='primary', use_container_width=True):
            _refresh_data(start_date, end_date)

    _render_snapshot_tools()
    _render_downloads()


# ── Boot ──────────────────────────────────────────────────────────────────────

if st.session_state.get('authenticated'):
    _enforce_session_timeout()
else:
    _login_gate()

if 'portfolio' not in st.session_state:
    try:
        cached = cache.load_portfolio()
    except Exception:
        LOGGER.exception('cache read failed')
        cached = None
    if cached:
        st.session_state.portfolio = cached
    elif snapshot.exists():
        try:
            st.session_state.portfolio = snapshot.load()
            st.session_state._is_snapshot = True
        except Exception:
            LOGGER.exception('snapshot read failed')

with st.sidebar:
    _render_sidebar()

navigation = st.navigation({
    'Portfolio': [
        st.Page('ui/overview.py', title='Overview', icon='📈', default=True),
        st.Page('ui/holdings.py', title='Holdings', icon='📊'),
        st.Page('ui/history.py', title='Value Over Time', icon='📉'),
        st.Page('ui/performance.py', title='Performance', icon='🎯'),
    ],
    'Money': [
        st.Page('ui/cash_flows.py', title='Cash Flows & Income', icon='💵'),
        st.Page('ui/transactions.py', title='Transactions', icon='🔄'),
    ],
    'Research': [
        st.Page('ui/earnings.py', title='Earnings', icon='📅'),
        st.Page('ui/thesis.py', title='Thesis Tracker', icon='🧠'),
        st.Page('ui/what_if_hold.py', title='What-If: Hold', icon='🔮'),
    ],
})
navigation.run()
