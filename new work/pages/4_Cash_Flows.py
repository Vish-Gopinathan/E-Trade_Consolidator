import sys
from pathlib import Path
import streamlit as st
import plotly.express as px
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))
from lib.common import require_auth, render_sidebar_status

st.set_page_config(page_title='Cash Flows & Income', page_icon='💵', layout='wide')
require_auth()

with st.sidebar:
    st.title('📈 Portfolio')
    render_sidebar_status()

st.title('💵 Cash Flows & Income')

portfolio = st.session_state.get('portfolio')
if not portfolio:
    st.warning('No data loaded. Go to the home page and refresh.')
    st.stop()

cash_tab, income_tab = st.tabs(['Cash Flows', 'Income'])

# ── Cash Flows tab ────────────────────────────────────────────────────────────
with cash_tab:
    cf = portfolio.get('cash_flows')
    if cf is None or (hasattr(cf, 'empty') and cf.empty):
        st.info('No cash flow data available.')
    else:
        df = cf.copy()
        if 'Date' in df.columns:
            df['Date'] = pd.to_datetime(df['Date'], errors='coerce')
        if 'Total Value' in df.columns:
            df['Total Value'] = pd.to_numeric(df['Total Value'], errors='coerce').fillna(0)

        deposits = df[df['Total Value'] > 0]['Total Value'].sum()
        withdrawals = df[df['Total Value'] < 0]['Total Value'].sum()
        net = deposits + withdrawals

        m1, m2, m3 = st.columns(3)
        m1.metric('Total Deposits', f'${deposits:,.2f}')
        m2.metric('Total Withdrawals', f'${withdrawals:,.2f}')
        m3.metric('Net Cash Flow', f'${net:,.2f}')

        # Cumulative line chart
        if 'Date' in df.columns and df['Date'].notna().any():
            sorted_df = df.sort_values('Date')
            sorted_df['Cumulative'] = sorted_df['Total Value'].cumsum()
            fig = px.line(sorted_df, x='Date', y='Cumulative',
                          title='Cumulative Net Cash Flow Over Time',
                          labels={'Cumulative': 'Cumulative ($)'})
            fig.update_layout(margin=dict(t=40, b=0))
            st.plotly_chart(fig, use_container_width=True)

        st.markdown('---')
        st.subheader('Cash Flow Transactions')
        display_cols = [c for c in ['Date', 'Description', 'Total Value', 'Category'] if c in df.columns]
        fmt = {}
        if 'Total Value' in df.columns:
            fmt['Total Value'] = '${:,.2f}'

        def _color_flow(val):
            if isinstance(val, (int, float)):
                return 'color: green' if val > 0 else 'color: red'
            return ''

        st.dataframe(
            df[display_cols].sort_values('Date', ascending=False).style.map(
                _color_flow, subset=['Total Value'] if 'Total Value' in display_cols else []
            ).format(fmt, na_rep='—'),
            use_container_width=True,
            hide_index=True,
        )

# ── Income tab ────────────────────────────────────────────────────────────────
with income_tab:
    income = portfolio.get('income')
    if income is None or (hasattr(income, 'empty') and income.empty):
        st.info('No income data available.')
    else:
        inc = income.copy()
        if 'Date' in inc.columns:
            inc['Date'] = pd.to_datetime(inc['Date'], errors='coerce')
        if 'Total Value' in inc.columns:
            inc['Total Value'] = pd.to_numeric(inc['Total Value'], errors='coerce').fillna(0)

        total_income = inc['Total Value'].sum()
        n_txns = len(inc)
        largest = inc['Total Value'].max()

        m1, m2, m3 = st.columns(3)
        m1.metric('Total Income', f'${total_income:,.2f}')
        m2.metric('# Transactions', n_txns)
        m3.metric('Largest Payment', f'${largest:,.2f}')

        # Monthly bar chart
        if 'Date' in inc.columns and inc['Date'].notna().any():
            monthly = inc.copy()
            monthly['Month'] = monthly['Date'].dt.to_period('M').astype(str)
            group_col = 'Transaction Type' if 'Transaction Type' in monthly.columns else None

            if group_col:
                agg = monthly.groupby(['Month', group_col])['Total Value'].sum().reset_index()
                fig = px.bar(agg, x='Month', y='Total Value', color=group_col,
                             title='Monthly Income by Type',
                             labels={'Total Value': 'Income ($)'})
            else:
                agg = monthly.groupby('Month')['Total Value'].sum().reset_index()
                fig = px.bar(agg, x='Month', y='Total Value',
                             title='Monthly Income',
                             labels={'Total Value': 'Income ($)'})

            fig.update_layout(margin=dict(t=40, b=0))
            st.plotly_chart(fig, use_container_width=True)

        # By type breakdown
        if 'Transaction Type' in inc.columns:
            st.markdown('---')
            st.subheader('By Type')
            by_type = inc.groupby('Transaction Type').agg(
                Total=('Total Value', 'sum'),
                Count=('Total Value', 'count'),
            ).reset_index()
            st.dataframe(by_type.style.format({'Total': '${:,.2f}'}, na_rep='—'),
                         use_container_width=True, hide_index=True)

        st.markdown('---')
        st.subheader('Income Transactions')
        display_cols = [c for c in ['Date', 'Description', 'Transaction Type', 'Total Value'] if c in inc.columns]
        st.dataframe(
            inc[display_cols].sort_values('Date', ascending=False).style.format(
                {'Total Value': '${:,.2f}'}, na_rep='—'
            ),
            use_container_width=True,
            hide_index=True,
        )
