import sys
from pathlib import Path
import streamlit as st
import plotly.express as px
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))
from lib.common import require_auth, render_sidebar_status

st.set_page_config(page_title='Holdings', page_icon='📊', layout='wide')
require_auth()

with st.sidebar:
    st.title('📈 Portfolio')
    render_sidebar_status()

st.title('📊 Holdings')

portfolio = st.session_state.get('portfolio')
if not portfolio:
    st.warning('No data loaded. Go to the home page and refresh.')
    st.stop()

holdings_df = portfolio.get('holdings')
summary = portfolio.get('summary') or {}

if holdings_df is None or holdings_df.empty:
    st.info('No holdings data available.')
    st.stop()

# KPI row
total_value = summary.get('Total Portfolio Value', 0)
total_gain = summary.get('Total Unrealized Gain', 0)
gain_pct = summary.get('Total Unrealized Gain %', 0)
cash_pct = summary.get('Cash Percentage', 0)

col1, col2, col3, col4 = st.columns(4)
col1.metric('Portfolio Value', f'${total_value:,.2f}')
col2.metric('Unrealized Gain', f'${total_gain:,.2f}', f'{gain_pct:.2f}%')
col3.metric('Cash', f'{cash_pct:.1f}%')
col4.metric('# Holdings', summary.get('Total Stocks', '—'))

st.markdown('---')

# Holdings table
stocks = holdings_df[holdings_df['Symbol'] != 'CASH'].copy()

def _colorize_gain(val):
    color = 'color: green' if val > 0 else ('color: red' if val < 0 else '')
    return color

display_cols = [c for c in [
    'Symbol', 'Symbol Description', 'Quantity', 'Current Price',
    'Market Value', 'Total Cost', 'Total Gain', 'Total Gain %', 'Percent of Portfolio',
] if c in stocks.columns]

st.subheader('All Holdings')
styled = stocks[display_cols].sort_values('Market Value', ascending=False).style.applymap(
    _colorize_gain, subset=[c for c in ['Total Gain', 'Total Gain %'] if c in display_cols]
).format({
    'Market Value': '${:,.2f}',
    'Total Cost': '${:,.2f}',
    'Total Gain': '${:,.2f}',
    'Total Gain %': '{:.2f}%',
    'Current Price': '${:,.2f}',
    'Percent of Portfolio': '{:.2f}%',
}, na_rep='—')

st.dataframe(styled, use_container_width=True, hide_index=True)

st.markdown('---')

# Charts
chart_col1, chart_col2 = st.columns(2)

with chart_col1:
    st.subheader('Allocation by Symbol')
    top = stocks.nlargest(10, 'Market Value')[['Symbol', 'Market Value']].copy()
    remaining = stocks['Market Value'].sum() - top['Market Value'].sum()
    if remaining > 0:
        other_row = pd.DataFrame([{'Symbol': 'Other', 'Market Value': remaining}])
        top = pd.concat([top, other_row], ignore_index=True)
    fig = px.pie(top, values='Market Value', names='Symbol', hole=0.3)
    fig.update_traces(textposition='inside', textinfo='label+percent')
    fig.update_layout(showlegend=False, margin=dict(t=0, b=0, l=0, r=0))
    st.plotly_chart(fig, use_container_width=True)

with chart_col2:
    analytics_report = portfolio.get('analytics_report') or {}
    sector_data = analytics_report.get('Sector Analysis') or {}
    if sector_data and 'Sector Weights (%)' in sector_data:
        st.subheader('Sector Allocation')
        weights = sector_data['Sector Weights (%)']
        sector_df = pd.DataFrame(list(weights.items()), columns=['Sector', 'Weight (%)'])
        sector_df = sector_df.sort_values('Weight (%)', ascending=True)
        fig2 = px.bar(sector_df, x='Weight (%)', y='Sector', orientation='h')
        fig2.update_layout(margin=dict(t=0, b=0, l=0, r=0), yaxis_title=None)
        st.plotly_chart(fig2, use_container_width=True)
    else:
        st.subheader('Gain/Loss by Holding')
        gain_df = stocks[['Symbol', 'Total Gain']].sort_values('Total Gain', ascending=True)
        colors = ['red' if v < 0 else 'green' for v in gain_df['Total Gain']]
        fig2 = px.bar(gain_df, x='Total Gain', y='Symbol', orientation='h',
                      color='Total Gain', color_continuous_scale=['red', 'green'])
        fig2.update_layout(margin=dict(t=0, b=0, l=0, r=0), yaxis_title=None, coloraxis_showscale=False)
        st.plotly_chart(fig2, use_container_width=True)
