import sys
from pathlib import Path
import streamlit as st
import pandas as pd
from datetime import date

sys.path.insert(0, str(Path(__file__).parent.parent))
from lib.common import require_auth, render_sidebar_status
from lib.news_fetcher import get_earnings_dates, get_news

st.set_page_config(page_title='News & Earnings', page_icon='📰', layout='wide')
require_auth()

with st.sidebar:
    st.title('📈 Portfolio')
    render_sidebar_status()

st.title('📰 News & Earnings')

portfolio = st.session_state.get('portfolio')
if not portfolio:
    st.warning('No data loaded. Go to the home page and refresh.')
    st.stop()

holdings_df = portfolio.get('holdings')
if holdings_df is None or (hasattr(holdings_df, 'empty') and holdings_df.empty):
    st.info('No holdings data available.')
    st.stop()

symbols = sorted(holdings_df[holdings_df['Symbol'] != 'CASH']['Symbol'].unique().tolist())

earnings_tab, news_tab = st.tabs(['Earnings Calendar', 'News Feed'])

# ── Earnings Calendar ─────────────────────────────────────────────────────────
with earnings_tab:
    st.subheader('Upcoming Earnings Dates')
    st.caption('Sourced from yfinance — cached for 1 hour. Dates are estimates; verify with your broker.')

    with st.spinner('Fetching earnings dates…'):
        earnings = get_earnings_dates(tuple(symbols))

    rows = []
    today = date.today()
    for sym, dt in earnings.items():
        if dt is None:
            days_away = None
            status = 'Unknown'
        else:
            days_away = (dt - today).days
            if days_away < 0:
                status = 'Past'
            elif days_away < 7:
                status = 'This week'
            elif days_away < 30:
                status = 'This month'
            else:
                status = f'In {days_away} days'
        rows.append({'Symbol': sym, 'Next Earnings': str(dt) if dt else '—', 'Days Away': days_away, 'Status': status})

    df = pd.DataFrame(rows)
    df_known = df[df['Days Away'].notna()].sort_values('Days Away')
    df_unknown = df[df['Days Away'].isna()]
    df_sorted = pd.concat([df_known, df_unknown]).reset_index(drop=True)

    def _row_color(row):
        days = row.get('Days Away')
        if days is None or days < 0:
            return [''] * len(row)
        if days < 7:
            return ['background-color: #ffe0e0'] * len(row)
        if days < 30:
            return ['background-color: #fff3cd'] * len(row)
        return [''] * len(row)

    styled = df_sorted[['Symbol', 'Next Earnings', 'Status']].style.apply(
        _row_color, axis=1
    )
    st.dataframe(styled, use_container_width=True, hide_index=True)

# ── News Feed ─────────────────────────────────────────────────────────────────
with news_tab:
    st.subheader('Stock News Feed')
    selected = st.selectbox('Select a holding', options=symbols)

    if selected:
        with st.spinner(f'Fetching news for {selected}…'):
            articles = get_news(selected)

        if not articles:
            st.info(f'No recent news found for {selected}.')
        else:
            st.markdown(f'**{len(articles)} recent articles for {selected}**')
            for art in articles:
                pub_str = ''
                if art.get('published_at'):
                    try:
                        pub_str = art['published_at'].strftime('%b %d, %Y %H:%M')
                    except Exception:
                        pub_str = str(art['published_at'])
                title = art.get('title', 'No title')
                link = art.get('link', '')
                publisher = art.get('publisher', '')
                with st.container(border=True):
                    if link:
                        st.markdown(f'**[{title}]({link})**')
                    else:
                        st.markdown(f'**{title}**')
                    st.caption(f'{publisher}  ·  {pub_str}')
