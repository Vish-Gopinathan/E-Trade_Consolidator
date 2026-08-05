import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).parent.parent))
from lib.common import require_auth, render_sidebar_status, get_secret
from lib.news_fetcher import get_full_earnings_data, get_news

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

all_symbols = sorted(holdings_df[holdings_df['Symbol'] != 'CASH']['Symbol'].unique().tolist())

fmp_key = get_secret('FMP_API_KEY', '').strip()

earnings_tab, news_tab = st.tabs(['Earnings', 'News Feed'])

# ── Earnings ──────────────────────────────────────────────────────────────────

with earnings_tab:
    source_label = 'Financial Modeling Prep' if fmp_key else 'yfinance'
    col_src, col_btn = st.columns([3, 1])
    with col_src:
        st.caption(
            f'Data source: **{source_label}** — cached 4 hours. '
            f'{"Add FMP_API_KEY to secrets for richer revenue data." if not fmp_key else "Revenue estimates included."} '
            'Dates are estimates; verify with your broker.'
        )
    with col_btn:
        if st.button('🔄 Force Refresh', help='Clear the 4-hour cache and re-fetch from source'):
            get_full_earnings_data.clear()
            st.rerun()

    st.info(
        f'Fetching earnings data for {len(all_symbols)} holdings — '
        'first load may take 30–90 seconds. Results are cached for 4 hours.',
        icon='⏳',
    )
    with st.spinner(f'Loading… ({len(all_symbols)} symbols)'):
        raw = get_full_earnings_data(tuple(all_symbols), fmp_key=fmp_key)

    # Surface any FMP errors for debugging
    fmp_errors = {s: v['_fmp_error'] for s, v in raw.items() if v.get('_fmp_error')}
    if fmp_errors:
        with st.expander(f'⚠️ API errors ({len(fmp_errors)} symbols) — click to debug'):
            for sym, err in list(fmp_errors.items())[:5]:
                st.code(f'{sym}: {err}')

    # Split equities vs non-equities
    equities = {s: v for s, v in raw.items() if v.get('is_equity', True)}
    non_equities = [s for s, v in raw.items() if not v.get('is_equity', True)]

    if non_equities:
        st.caption(f'ETFs / funds excluded from earnings: {", ".join(sorted(non_equities))}')

    today = date.today()

    # ── Upcoming earnings ──────────────────────────────────────────────────────

    st.subheader('Upcoming Earnings')

    upcoming_rows = []
    for sym, data in equities.items():
        u = data.get('upcoming')
        if not u:
            continue
        try:
            dt = date.fromisoformat(u['date'])
        except Exception:
            continue
        days = (dt - today).days
        if days < -3:  # skip if more than 3 days past (estimation lag)
            continue
        when = u.get('time', '')
        when_label = {'bmo': 'Before open', 'bmc': 'Before open',
                      'amc': 'After close', 'dmh': 'During hours'}.get(when, when or '—')
        upcoming_rows.append({
            'Symbol': sym,
            'Date': dt,
            'Days Away': days,
            'EPS Estimate': u.get('eps_estimate'),
            'Revenue Est ($B)': (u.get('revenue_estimate') or 0) / 1e9 or None,
            'When': when_label,
            '_days': days,
        })

    if upcoming_rows:
        up_df = pd.DataFrame(upcoming_rows).sort_values('_days').drop(columns=['_days'])

        def _row_style(row):
            days = row['Days Away']
            if days <= 7:
                return ['background-color: rgba(220,50,50,0.15)'] * len(row)
            if days <= 30:
                return ['background-color: rgba(255,200,0,0.12)'] * len(row)
            return [''] * len(row)

        fmt = {
            'Date': lambda d: d.strftime('%Y-%m-%d') if pd.notna(d) else '—',
            'EPS Estimate': lambda v: f'${v:.2f}' if pd.notna(v) and v is not None else '—',
            'Revenue Est ($B)': lambda v: f'${v:.2f}B' if v else '—',
        }
        styled_up = up_df.style.apply(_row_style, axis=1).format(fmt, na_rep='—')
        st.dataframe(styled_up, use_container_width=True, hide_index=True)
    else:
        st.info('No upcoming earnings found for current holdings.')

    # ── Recent earnings ────────────────────────────────────────────────────────

    st.markdown('---')
    st.subheader('Recent Earnings (last 4 reported quarters per stock)')

    recent_rows = []
    cutoff = today - timedelta(days=365)
    for sym, data in equities.items():
        for r in data.get('recent', []):
            try:
                dt = date.fromisoformat(r['date'])
            except Exception:
                continue
            if dt < cutoff:
                continue
            eps_act = r.get('eps_actual')
            eps_est = r.get('eps_estimate')
            surprise = r.get('surprise_pct')
            rev_act = r.get('revenue_actual')
            rev_est = r.get('revenue_estimate')
            price_chg = r.get('price_chg_pct')

            # Beat / Miss indicator
            if surprise is not None:
                beat = '✅ Beat' if surprise >= 0 else '❌ Miss'
            elif eps_act is not None and eps_est is not None:
                beat = '✅ Beat' if eps_act >= eps_est else '❌ Miss'
            else:
                beat = '—'

            recent_rows.append({
                'Symbol': sym,
                'Date': dt,
                'Actual EPS': eps_act,
                'Est EPS': eps_est,
                'Surprise %': surprise,
                'Beat/Miss': beat,
                'Stock Reaction': price_chg,
                'Revenue Act ($B)': rev_act / 1e9 if rev_act else None,
                'Revenue Est ($B)': rev_est / 1e9 if rev_est else None,
            })

    if recent_rows:
        rec_df = pd.DataFrame(recent_rows).sort_values('Date', ascending=False)

        # Remove revenue columns if all empty (yfinance path)
        if rec_df['Revenue Act ($B)'].isna().all():
            rec_df = rec_df.drop(columns=['Revenue Act ($B)', 'Revenue Est ($B)'])

        def _color_surprise(val):
            if val is None or (isinstance(val, float) and pd.isna(val)):
                return ''
            return 'color: #27ae60' if val >= 0 else 'color: #c0392b'

        def _color_reaction(val):
            if val is None or (isinstance(val, float) and pd.isna(val)):
                return ''
            return 'color: #27ae60' if val >= 0 else 'color: #c0392b'

        fmt_rec = {
            'Date': lambda d: d.strftime('%Y-%m-%d') if pd.notna(d) else '—',
            'Actual EPS': lambda v: f'${v:.2f}' if pd.notna(v) and v is not None else '—',
            'Est EPS': lambda v: f'${v:.2f}' if pd.notna(v) and v is not None else '—',
            'Surprise %': lambda v: f'{v:+.1f}%' if pd.notna(v) and v is not None else '—',
            'Stock Reaction': lambda v: f'{v:+.2f}%' if pd.notna(v) and v is not None else '—',
        }
        if 'Revenue Act ($B)' in rec_df.columns:
            fmt_rec['Revenue Act ($B)'] = lambda v: f'${v:.2f}B' if pd.notna(v) and v else '—'
            fmt_rec['Revenue Est ($B)'] = lambda v: f'${v:.2f}B' if pd.notna(v) and v else '—'

        color_cols = [c for c in ['Surprise %', 'Stock Reaction'] if c in rec_df.columns]
        styled_rec = rec_df.style.format(fmt_rec, na_rep='—')
        if 'Surprise %' in color_cols:
            styled_rec = styled_rec.map(_color_surprise, subset=['Surprise %'])
        if 'Stock Reaction' in color_cols:
            styled_rec = styled_rec.map(_color_reaction, subset=['Stock Reaction'])

        st.dataframe(styled_rec, use_container_width=True, hide_index=True)

        no_data = [s for s, d in equities.items() if not d.get('recent') and not d.get('upcoming')]
        if no_data:
            st.caption(f'No earnings data found for: {", ".join(sorted(no_data))}')
    else:
        st.info('No recent earnings data found. Try fetching newer data from the home page.')

# ── News Feed ─────────────────────────────────────────────────────────────────

with news_tab:
    st.subheader('Stock News Feed')
    selected = st.selectbox('Select a holding', options=all_symbols)

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
