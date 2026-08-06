"""
Cash Flows & Income, plus the Transfer Review that decides what counts as which.

The three tabs answer three different questions:

* **Cash flows** — how much money you put in and took out.
* **Income** — what the portfolio paid you while you held it.
* **Transfer review** — the transfers the app could not classify on its own, and
  what tagging each one would change.

The review tab exists because the alternative is guessing. A transfer to an
account the app has never seen is counted as external, which is the safe default,
but only the account holder knows whether that account is theirs.
"""

import pandas as pd
import plotly.express as px
import streamlit as st

from portfolio import classify, schema
from portfolio.storage import accounts as account_map_store
from ui import theme
from ui.common import is_guest, money, page_header, require_portfolio, signed_money

page_header('Cash Flows & Income', '💵')

portfolio = require_portfolio()
colours = theme.palette()
report = portfolio.get('analytics_report') or {}
transactions = portfolio.get('transactions')
cash_flows = portfolio.get('cash_flows')

needs_review = 0
if transactions is not None and not transactions.empty and 'Needs Review' in transactions.columns:
    needs_review = int(transactions['Needs Review'].fillna(False).sum())

flows_tab, income_tab, review_tab = st.tabs([
    'Cash Flows', 'Income',
    f'Transfer Review{f" ({needs_review})" if needs_review else ""}',
])

# ── Cash flows ────────────────────────────────────────────────────────────────

with flows_tab:
    if cash_flows is None or cash_flows.empty:
        st.info('No deposits or withdrawals in the loaded date range.')
    else:
        frame = cash_flows.copy()
        frame['Date'] = pd.to_datetime(frame['Date'], errors='coerce')
        frame['Total Value'] = pd.to_numeric(frame['Total Value'], errors='coerce').fillna(0)

        flow_summary = report.get(schema.CASH_FLOWS, {})
        deposited = flow_summary.get(schema.TOTAL_DEPOSITED,
                                     frame[frame['Total Value'] > 0]['Total Value'].sum())
        withdrawn = flow_summary.get(schema.TOTAL_WITHDRAWN,
                                     abs(frame[frame['Total Value'] < 0]['Total Value'].sum()))

        m1, m2, m3 = st.columns(3)
        m1.metric('Deposited', money(deposited, 0))
        m2.metric('Withdrawn', money(withdrawn, 0))
        m3.metric('Net contributed', money(deposited - withdrawn, 0))

        if needs_review:
            st.warning(
                f'{needs_review} transfer(s) are counted as external only because the '
                'counterparty account is unrecognised. Resolve them in **Transfer Review**.',
                icon='⚠️',
            )

        st.markdown('##### Money in, cumulatively')
        ordered = frame.sort_values('Date')
        ordered['Cumulative'] = ordered['Total Value'].cumsum()
        fig = px.area(ordered, x='Date', y='Cumulative')
        fig.update_traces(
            line_color=colours['primary'], line_width=2,
            fillcolor=f'rgba(42,120,214,0.12)' if not colours['dark']
            else 'rgba(57,135,229,0.16)',
            hovertemplate='%{x|%b %d, %Y}<br>$%{y:,.0f} contributed to date<extra></extra>',
        )
        theme.apply_layout(fig, colours, y_prefix='$')
        fig.update_layout(height=300, xaxis_title=None, yaxis_title=None, hovermode='x unified')
        fig.update_yaxes(tickformat=',.0f')
        st.plotly_chart(fig, use_container_width=True)

        st.markdown('##### Every deposit and withdrawal')
        columns = [c for c in ['Date', 'Description', 'Total Value', 'Category', 'Needs Review']
                   if c in frame.columns]
        st.dataframe(
            frame[columns].sort_values('Date', ascending=False).style
            .map(lambda v: f'color: {colours["positive"] if v > 0 else colours["negative"]}'
                 if isinstance(v, (int, float)) and v else '',
                 subset=['Total Value'])
            .format({'Total Value': signed_money, 'Date': '{:%b %d, %Y}'}, na_rep='—'),
            use_container_width=True, hide_index=True,
        )

        internal = (
            transactions[transactions['Category'] == classify.INTERNAL]
            if transactions is not None and 'Category' in transactions.columns
            else pd.DataFrame()
        )
        if not internal.empty:
            with st.expander(
                f'{len(internal)} transfer(s) excluded as internal — why they were excluded'
            ):
                st.caption(
                    'Money moving between your own accounts is not a contribution or a '
                    'withdrawal, so it is kept out of the totals above. Shown here so '
                    'nothing is silently dropped.'
                )
                columns = [c for c in ['Date', 'Security Name', 'Total Value', 'Account',
                                       'Classification Note'] if c in internal.columns]
                st.dataframe(
                    internal[columns].sort_values('Date', ascending=False)
                    .rename(columns={'Security Name': 'Description',
                                     'Classification Note': 'Why'})
                    .style.format({'Total Value': '${:,.2f}', 'Date': '{:%b %d, %Y}'},
                                  na_rep='—'),
                    use_container_width=True, hide_index=True,
                )

# ── Income ────────────────────────────────────────────────────────────────────

with income_tab:
    income = portfolio.get('income')
    if income is None or income.empty:
        st.info('No dividends or interest in the loaded date range.')
    else:
        frame = income.copy()
        frame['Date'] = pd.to_datetime(frame['Date'], errors='coerce')
        frame['Total Value'] = pd.to_numeric(frame['Total Value'], errors='coerce').fillna(0)

        m1, m2, m3 = st.columns(3)
        m1.metric('Total income', money(frame['Total Value'].sum(), 0))
        m2.metric('Payments', len(frame))
        m3.metric('Largest payment', money(frame['Total Value'].max()))

        st.markdown('##### Income by month')
        monthly = frame.copy()
        monthly['Month'] = monthly['Date'].dt.to_period('M').dt.to_timestamp()
        group = 'Transaction Type' if 'Transaction Type' in monthly.columns else None
        grouped = (
            monthly.groupby(['Month', group])['Total Value'].sum().reset_index()
            if group else monthly.groupby('Month')['Total Value'].sum().reset_index()
        )
        fig = px.bar(
            grouped, x='Month', y='Total Value', color=group,
            color_discrete_sequence=colours['categorical'],
        )
        fig.update_traces(
            marker_cornerradius=3, marker_line_color=colours['surface'], marker_line_width=1,
            hovertemplate='%{x|%b %Y}: $%{y:,.2f}<extra>%{fullData.name}</extra>',
        )
        theme.apply_layout(fig, colours, y_prefix='$')
        fig.update_layout(height=320, xaxis_title=None, yaxis_title=None, barmode='stack')
        fig.update_yaxes(tickformat=',.0f')
        st.plotly_chart(fig, use_container_width=True)

        if group:
            st.markdown('##### By type')
            by_type = frame.groupby(group).agg(
                Total=('Total Value', 'sum'), Payments=('Total Value', 'count'),
            ).reset_index().sort_values('Total', ascending=False)
            st.dataframe(
                by_type.style.format({'Total': '${:,.2f}'}),
                use_container_width=True, hide_index=True,
            )

        st.markdown('##### Every payment')
        columns = [c for c in ['Date', 'Description', 'Transaction Type', 'Total Value']
                   if c in frame.columns]
        st.dataframe(
            frame[columns].sort_values('Date', ascending=False).style.format(
                {'Total Value': '${:,.2f}', 'Date': '{:%b %d, %Y}'}, na_rep='—'),
            use_container_width=True, hide_index=True,
        )

# ── Transfer review ───────────────────────────────────────────────────────────

with review_tab:
    st.markdown(
        'E\\*TRADE describes a move between your own accounts and a withdrawal to an '
        'outside account almost identically. Where two legs share a reference number '
        'and cancel out, the app pairs them and excludes both automatically. What is '
        'left is below — tag each account once and the answer is remembered.'
    )

    if transactions is None or transactions.empty:
        st.info('No transactions loaded.')
    elif is_guest():
        st.info('Guest view — tagging is read-only.')
    else:
        pending = classify.unresolved_counterparties(transactions)
        current_map = account_map_store.load()

        if pending.empty:
            st.success('Nothing to review — every transfer has been resolved.')
        else:
            st.caption(
                'Each row is currently counted as an **external** cash flow. Tag it as '
                'yours to exclude it from deposits and withdrawals.'
            )
            for _, row in pending.iterrows():
                account = row['Counterparty']
                left, right = st.columns([3, 2])
                with left:
                    st.markdown(
                        f'**Account ending {account}** — {row["Transfers"]} transfer(s), '
                        f'net {money(row["Net Amount"])}  \n'
                        f'<span style="opacity:.7">'
                        f'{pd.Timestamp(row["First"]):%b %Y} to '
                        f'{pd.Timestamp(row["Last"]):%b %Y}</span>',
                        unsafe_allow_html=True,
                    )
                with right:
                    choice = st.radio(
                        f'Account {account}',
                        ['Not yet decided', 'My own account', 'Someone else / a bank'],
                        key=f'review_{account}', horizontal=False,
                        label_visibility='collapsed',
                    )
                    if choice != 'Not yet decided' and st.button(
                        'Save', key=f'save_{account}', use_container_width=True
                    ):
                        account_map_store.tag(
                            account,
                            account_map_store.INTERNAL if choice == 'My own account'
                            else account_map_store.EXTERNAL,
                        )
                        st.success(
                            f'Saved. Refresh from E\\*TRADE to apply it to the figures.'
                        )
                st.markdown('---')

        if current_map:
            with st.expander('Accounts you have already tagged'):
                tagged = pd.DataFrame(
                    [{'Account': f'…{k}', 'Treated as': v} for k, v in sorted(current_map.items())]
                )
                st.dataframe(tagged, use_container_width=True, hide_index=True)
                st.caption(
                    'To change one, tag it again from the list above after the next refresh.'
                )
