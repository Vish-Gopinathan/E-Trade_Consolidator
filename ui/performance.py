import streamlit as st
import plotly.graph_objects as go
import plotly.express as px
import pandas as pd
from ui.common import require_auth, render_sidebar_status

st.set_page_config(page_title='Analytics', page_icon='📉', layout='wide')
require_auth()

with st.sidebar:
    st.title('📈 Portfolio')
    render_sidebar_status()

st.title('📉 Analytics')

portfolio = st.session_state.get('portfolio')
if not portfolio:
    st.warning('No data loaded. Go to the home page and refresh.')
    st.stop()

report = portfolio.get('analytics_report') or {}
summary = portfolio.get('summary') or {}

if not report:
    st.info('No analytics data available.')
    st.stop()

# ── Performance ──────────────────────────────────────────────────────────────
st.subheader('Performance')
perf = report.get('Performance Metrics') or {}
p1, p2, p3, p4 = st.columns(4)

def _fmt(v, fmt='${:,.2f}'):
    if v is None:
        return '—'
    try:
        return fmt.format(float(v))
    except Exception:
        return str(v)

p1.metric('Market Value', _fmt(summary.get('Total Stock Market Value')))
p2.metric('Cost Basis', _fmt(summary.get('Total Cost Basis')))
p3.metric('Simple Return', _fmt(perf.get('Simple Return (%)'), '{:.2f}%'))
p4.metric('Deposit-Adj. Return', _fmt(perf.get('Modified Dietz Return (%)'), '{:.2f}%'))

st.markdown('---')

# ── Concentration ─────────────────────────────────────────────────────────────
conc = report.get('Concentration Analysis') or {}
st.subheader('Concentration')
c1, c2, c3 = st.columns(3)
hhi = conc.get('HHI Score')
c1.metric('HHI Score', f'{hhi:.1f}' if hhi else '—',
          help='0–1000 = diverse, 1000–1800 = moderate, 1800+ = concentrated')
c2.metric('Top 5 Weight', _fmt(conc.get('Top 5 Concentration (%)'), '{:.1f}%'))
c3.metric('Top 10 Weight', _fmt(conc.get('Top 10 Concentration (%)'), '{:.1f}%'))

top_n_data = conc.get('Top 5 Holdings') or conc.get('Top Holdings')
if top_n_data and isinstance(top_n_data, list):
    conc_df = pd.DataFrame(top_n_data)
    if 'Symbol' in conc_df.columns and 'Weight (%)' in conc_df.columns:
        fig = px.bar(conc_df, x='Symbol', y='Weight (%)', title='Top Holding Weights')
        fig.update_layout(margin=dict(t=30, b=0))
        st.plotly_chart(fig, use_container_width=True)

st.markdown('---')

# ── Risk metrics ──────────────────────────────────────────────────────────────
risk = report.get('Risk Metrics') or {}
st.subheader('Risk & Quality')
r1, r2, r3, r4 = st.columns(4)
r1.metric('Sharpe Ratio', _fmt(risk.get('Sharpe Ratio'), '{:.2f}'))
r2.metric('Sortino Ratio', _fmt(risk.get('Sortino Ratio'), '{:.2f}'))
r3.metric('Win Rate', _fmt(risk.get('Win Rate (%)'), '{:.1f}%'))
r4.metric('Volatility', _fmt(risk.get('Portfolio Volatility (%)'), '{:.2f}%'))

best = risk.get('Best Performer')
worst = risk.get('Worst Performer')
if best or worst:
    b1, b2 = st.columns(2)
    if best:
        b1.info(f'**Best performer:** {best}')
    if worst:
        b2.warning(f'**Worst performer:** {worst}')

quality = report.get('Holdings Quality') or {}
if quality and isinstance(quality, dict):
    buckets = {k: v for k, v in quality.items() if isinstance(v, (int, float))}
    if buckets:
        q_df = pd.DataFrame(list(buckets.items()), columns=['Category', 'Count'])
        fig2 = px.bar(q_df, x='Category', y='Count', title='Holdings Quality Distribution')
        fig2.update_layout(margin=dict(t=30, b=0))
        st.plotly_chart(fig2, use_container_width=True)

st.markdown('---')

# ── Sector analysis ───────────────────────────────────────────────────────────
sector = report.get('Sector Analysis') or {}
if sector:
    st.subheader('Sector Analysis')
    weights = sector.get('Sector Weights (%)') or {}
    if weights:
        s_df = pd.DataFrame(list(weights.items()), columns=['Sector', 'Weight (%)'])
        s_df = s_df.sort_values('Weight (%)', ascending=False)
        fig3 = px.bar(s_df, x='Sector', y='Weight (%)', title='Sector Weights')
        fig3.update_layout(margin=dict(t=30, b=0))
        st.plotly_chart(fig3, use_container_width=True)

# ── Full report expander ──────────────────────────────────────────────────────
with st.expander('Full analytics report (raw)'):
    st.json(report)
