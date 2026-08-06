"""
Daily portfolio value reconstructed from transaction history.

E*TRADE reports only the portfolio as it stands today. This page rebuilds what it
was worth at the close of every day by walking share counts backwards from
today's holdings through the transaction feed and valuing them with daily closes.
See portfolio/history.py for the methodology.
"""

import json
from datetime import date, timedelta

import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
import streamlit as st
from ui.common import is_guest, page_header, require_portfolio, signed_money
from portfolio.storage import prices as ps
from portfolio import symbols as sr
from portfolio import history as ph
from ui import theme as vt
from portfolio import paths

page_header(
    'Portfolio Value Over Time', '📉',
    "End-of-day value for every trading day, rebuilt from your transaction history. "
    "Share counts are walked backwards from today's holdings, so the most recent "
    "figures are exact and any gap in the transaction feed surfaces as a residual "
    "in the earliest dates — see **Data quality** below.",
)

portfolio = require_portfolio()
transactions_df = require_portfolio('transactions')
holdings_df = portfolio.get('holdings')

anchor = ph.anchor_date(portfolio)

# ── Symbol resolution ─────────────────────────────────────────────────────────

all_trades = ph.trade_rows(transactions_df)
if all_trades.empty:
    st.info('No buy/sell transactions found, so there is no position history to rebuild.')
    st.stop()

manual_map = sr.load_map()
trades, unresolved, sources = sr.resolve_trades(all_trades, holdings_df, manual_map)

first_txn = pd.to_datetime(all_trades['Date']).min().date()


def _sector_groups():
    path = paths.CONFIG_DIR / 'sectors.json'
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}


# ── Controls ──────────────────────────────────────────────────────────────────

st.markdown('### Range')
preset_col, start_col, end_col, opts_col = st.columns([1.4, 1, 1, 1.4])

with preset_col:
    preset = st.selectbox(
        'Period',
        ['Since first trade', 'Year to date', 'Last 12 months', 'Last 6 months',
         'Last 3 months', 'Last month', 'Custom'],
        index=0,
    )

_preset_start = {
    'Since first trade': first_txn,
    'Year to date': date(anchor.year, 1, 1),
    'Last 12 months': anchor - timedelta(days=365),
    'Last 6 months': anchor - timedelta(days=182),
    'Last 3 months': anchor - timedelta(days=91),
    'Last month': anchor - timedelta(days=30),
}
default_start = max(_preset_start.get(preset, first_txn), first_txn - timedelta(days=1))

# Keying on the preset forces the widgets to rebuild when it changes; Streamlit
# otherwise keeps the first value a keyed date_input was given.
with start_col:
    start_date = st.date_input(
        'From', value=default_start, min_value=first_txn - timedelta(days=365),
        max_value=anchor, disabled=(preset != 'Custom'), key=f'hist_start_{preset}',
    )
with end_col:
    end_date = st.date_input(
        'To', value=anchor, min_value=first_txn, max_value=anchor,
        disabled=(preset != 'Custom'), key=f'hist_end_{preset}',
    )

if preset != 'Custom':
    start_date, end_date = default_start, anchor

with opts_col:
    include_cash = st.checkbox('Include cash balance', value=True)
    show_contributions = st.checkbox('Overlay net deposits', value=True)

if start_date >= end_date:
    st.error('The start date must come before the end date.')
    st.stop()

# ── Build (cached by input signature) ─────────────────────────────────────────

signature = (
    portfolio.get('fetched_at', ''), str(start_date), str(end_date),
    len(trades), json.dumps(manual_map, sort_keys=True),
)

if st.session_state.get('_hist_signature') != signature:
    symbols = sorted(
        set(ph.current_shares(holdings_df).index) | (set(trades['Symbol']) - {''})
    )
    bar = st.progress(0.0, text='Preparing…')
    try:
        store = ps.ensure(
            symbols, start_date, anchor,
            progress=lambda f, m: bar.progress(min(f, 1.0), text=m),
        )
        store = ps.ensure_metadata(
            symbols, progress=lambda f, m: bar.progress(min(f, 1.0), text=m),
        )
        bar.progress(1.0, text='Rebuilding daily values…')
        result = ph.reconstruct(portfolio, trades, store, start_date, end_date, ps)
    finally:
        bar.empty()

    st.session_state['_hist_signature'] = signature
    st.session_state['_hist_result'] = result
    st.session_state['_hist_store'] = store

result = st.session_state['_hist_result']
store = st.session_state['_hist_store']

if result.total.empty:
    st.warning('No trading days fall inside the selected range.')
    st.stop()

series = result.total if include_cash else result.positions_total
stats = ph.period_stats(result, include_cash=include_cash)
attributes = ph.symbol_attributes(result.symbols, store, holdings_df, _sector_groups())

# ── Headline numbers ──────────────────────────────────────────────────────────

st.markdown('---')
k1, k2, k3, k4, k5 = st.columns(5)
k1.metric('Value on ' + end_date.strftime('%b %d, %Y'), f"${stats['end_value']:,.0f}")

# A percentage against a near-empty opening balance is arithmetically true and
# completely uninformative — "+18,360%" says the account started at nothing.
pct = stats['change_pct']
meaningful_pct = (
    pct is not None and stats['start_value'] > 0.10 * max(stats['end_value'], 1)
)
k2.metric(
    'Change over period',
    f"${stats['change']:,.0f}",
    f'{pct:.1f}%' if meaningful_pct else None,
    help=None if meaningful_pct else 'No percentage shown: the portfolio was near zero at the start of this period.',
)
k3.metric(
    'Net deposits', f"${stats['net_contributions']:,.0f}",
    help='External money in minus money out over the period. Internal transfers between your own accounts are excluded.',
)
k4.metric(
    'Market gain', f"${stats['market_gain']:,.0f}",
    help='Change in value not explained by deposits or withdrawals.',
)
k5.metric(
    'Max drawdown',
    f"{stats['max_drawdown_pct']:.1f}%" if stats['max_drawdown_pct'] is not None else '—',
    help='Largest peak-to-trough fall in total value within the period. This tracks the '
         'balance, not performance — a withdrawal counts as a fall, and early drops look '
         'severe while the account is still small.',
)

# ── Main chart ────────────────────────────────────────────────────────────────

theme = vt.palette()

fig = go.Figure()
fig.add_trace(go.Scatter(
    x=series.index, y=series.values, name='Portfolio value',
    mode='lines', line=dict(color=theme['primary'], width=2),
    fill='tozeroy',
    fillcolor='rgba(57,135,229,0.16)' if theme['dark'] else 'rgba(42,120,214,0.12)',
    hovertemplate='%{x|%b %d, %Y}<br><b>$%{y:,.0f}</b><extra></extra>',
))

if show_contributions:
    contrib = result.contributions
    # Re-base so the line starts level with the portfolio at the window's open —
    # what matters is money added *during* the period, not since inception.
    rebased = contrib - contrib.iloc[0] + series.iloc[0]
    fig.add_trace(go.Scatter(
        x=rebased.index, y=rebased.values, name='Cost basis (start + net deposits)',
        mode='lines', line=dict(color=theme['reference'], width=1.5, dash='dot'),
        hovertemplate='%{x|%b %d, %Y}<br>$%{y:,.0f}<extra></extra>',
    ))

fig.update_layout(
    height=440, margin=dict(t=40, b=20, l=0, r=0),
    hovermode='x unified',
    yaxis=dict(title='', tickprefix='$', tickformat=',.0f',
               gridcolor=theme['grid'], zeroline=False),
    xaxis=dict(title='', gridcolor=theme['grid'], showgrid=False),
    legend=dict(orientation='h', yanchor='bottom', y=1.0, x=0),
)
st.plotly_chart(fig, use_container_width=True)

peak_note = (
    f"Peak **${stats['peak_value']:,.0f}** on {stats['peak_date']:%b %d, %Y} · "
    f"low **${stats['trough_value']:,.0f}** on {stats['trough_date']:%b %d, %Y} · "
    f"{len(series):,} trading days"
)
st.caption(peak_note)

# ── Breakdown ─────────────────────────────────────────────────────────────────

st.markdown('---')
st.markdown('### Breakdown')

b1, b2 = st.columns(2)
with b1:
    dimension = st.selectbox('Break out by', ['Symbol', 'Sector', 'Asset Type', 'Status'])
with b2:
    view_mode = st.selectbox('Show as', ['Dollar value', 'Percent of portfolio'])

available = sorted(attributes[dimension].unique()) if dimension != 'Symbol' else sorted(result.symbols)
chosen = st.multiselect(
    f'Filter {dimension.lower()}s (leave empty for all)', available, default=[],
)

values = result.values
if chosen:
    if dimension == 'Symbol':
        keep = [s for s in values.columns if s in chosen]
    else:
        keep = [s for s in values.columns if attributes.loc[s, dimension] in chosen]
    values = values[keep]

if values.empty or values.abs().to_numpy().sum() == 0:
    st.info('Nothing to show for the current filter.')
else:
    grouped = ph.group_values(values, attributes, dimension)

    # Slots come from the ranking over every category in the dimension, not just
    # the visible ones, so hiding a series leaves the rest their colours.
    universe = ph.rank_categories(result.values, attributes, dimension)
    colours = vt.colour_map(
        list(grouped.columns), universe, theme['categorical'], theme['other'],
    )

    plot_df = grouped.copy()
    if view_mode == 'Percent of portfolio':
        totals = plot_df.sum(axis=1).replace(0, pd.NA)
        plot_df = plot_df.div(totals, axis=0).fillna(0.0) * 100

    plot_df.index.name = 'Date'
    long = plot_df.reset_index().melt(
        id_vars='Date', var_name=dimension, value_name='Value'
    )

    area = px.area(long, x='Date', y='Value', color=dimension, color_discrete_map=colours)
    area.update_traces(hovertemplate='%{y:,.2f}<extra>%{fullData.name}</extra>')
    # A 2px edge in the surface colour holds neighbouring bands apart
    vt.style_stacked_area(area, colours, theme['surface'])
    area.update_layout(
        height=440, margin=dict(t=70, b=20, l=0, r=0), hovermode='x unified',
        yaxis=dict(
            title='',
            tickprefix='$' if view_mode == 'Dollar value' else '',
            ticksuffix='' if view_mode == 'Dollar value' else '%',
            tickformat=',.0f', gridcolor=theme['grid'], zeroline=False,
        ),
        xaxis=dict(title='', showgrid=False),
        legend=dict(title='', orientation='h', yanchor='bottom', y=1.0, x=0),
    )
    st.plotly_chart(area, use_container_width=True)

    if ph.OTHER_LABEL in grouped.columns:
        pooled = len([c for c in ph.collapse_by(values, attributes, dimension).columns]) - (len(grouped.columns) - 1)
        st.caption(
            f'The chart carries eight distinct colours, so the {pooled} smallest '
            f'{dimension.lower()}s by peak value are pooled into "Other". '
            'The table below lists every one separately — or use the filter above '
            'to bring specific ones onto the chart.'
        )

    # Every category listed separately — this is the table view that backs the
    # pooled "Other" band, and the readable fallback for the lighter hues.
    detail_grouped = ph.collapse_by(values, attributes, dimension)
    detail_grouped = detail_grouped.loc[:, detail_grouped.abs().sum() > 0]
    first_row, last_row = detail_grouped.iloc[0], detail_grouped.iloc[-1]
    table = pd.DataFrame({
        dimension: detail_grouped.columns,
        'Value at start': first_row.values,
        'Value at end': last_row.values,
        'Change': (last_row - first_row).values,
    })
    table['Share of portfolio'] = (
        table['Value at end'] / table['Value at end'].sum() * 100
        if table['Value at end'].sum() else 0
    )
    table = table.sort_values('Value at end', ascending=False)
    st.dataframe(
        table.style.format({
            'Value at start': '${:,.0f}', 'Value at end': '${:,.0f}',
            'Change': lambda v: signed_money(v, 0), 'Share of portfolio': '{:.1f}%',
        }),
        use_container_width=True, hide_index=True,
    )

# ── Daily data ────────────────────────────────────────────────────────────────

st.markdown('---')
st.markdown('### Daily values')

freq_label = st.radio(
    'Granularity', ['Daily', 'Weekly', 'Monthly'], horizontal=True, index=0,
)
_freq = {'Daily': None, 'Weekly': 'W-FRI', 'Monthly': 'ME'}[freq_label]

daily = pd.DataFrame({
    'Positions': result.positions_total,
    'Cash': result.cash,
    'Total': result.total,
    'Net deposits (cumulative)': result.contributions,
})
if _freq:
    daily = daily.resample(_freq).last().dropna(how='all')

daily.insert(0, 'Date', daily.index.date)
daily['Day change'] = daily['Total'].diff()

st.dataframe(
    daily.iloc[::-1].style.format({
        'Positions': '${:,.2f}', 'Cash': '${:,.2f}', 'Total': '${:,.2f}',
        'Net deposits (cumulative)': '${:,.2f}', 'Day change': signed_money,
    }, na_rep='—'),
    use_container_width=True, hide_index=True, height=320,
)

st.download_button(
    '⬇ Download this table (CSV)',
    daily.to_csv(index=False).encode(),
    f'portfolio_history_{start_date}_{end_date}.csv',
    mime='text/csv',
)

with st.expander('Per-symbol daily detail'):
    sym_choice = st.selectbox('Symbol', sorted(result.symbols))
    detail = pd.DataFrame({
        'Date': result.shares.index.date,
        'Shares (today basis)': result.shares[sym_choice].values,
        'Shares held then': result.shares_actual[sym_choice].values,
        'Close': result.prices[sym_choice].values,
        'Market value': result.values[sym_choice].values,
    })
    detail = detail[detail['Shares (today basis)'].abs() > 1e-9]
    if detail.empty:
        st.caption('No days on which this position was held inside the selected range.')
    else:
        same = (detail['Shares (today basis)'] - detail['Shares held then']).abs().max() < 1e-6
        if same:
            detail = detail.drop(columns=['Shares held then'])
        st.dataframe(
            detail.iloc[::-1].style.format({
                'Shares (today basis)': '{:,.4g}', 'Shares held then': '{:,.4g}',
                'Close': '${:,.2f}', 'Market value': '${:,.2f}',
            }),
            use_container_width=True, hide_index=True, height=300,
        )
        if not same:
            st.caption(
                'The two share columns differ because of stock splits. Closing prices '
                'from Yahoo are split-adjusted, so valuation uses the today-basis count; '
                '"shares held then" is the raw number in the account at the time.'
            )

# ── Data quality ──────────────────────────────────────────────────────────────

st.markdown('---')
diag = result.diagnostics
residual = diag.get('residual_shares', pd.Series(dtype=float))
recon = diag.get('reconciliation', {})
issues = int(len(residual) > 0) + int(bool(diag.get('unpriced_symbols'))) + int(bool(unresolved))

with st.expander(f'🔍 Data quality{f" — {issues} thing(s) to know" if issues else " — all clear"}'):
    if recon:
        diff = recon.get('difference', 0.0)
        reported = recon.get('reported_total', 0.0)
        st.markdown(
            f"**Reconciliation on {anchor:%b %d, %Y}** — rebuilt "
            f"${recon.get('rebuilt_total', 0):,.2f} against E\\*TRADE's reported "
            f"${reported:,.2f}, a difference of ${diff:,.2f} "
            f"({(diff / reported * 100 if reported else 0):+.2f}%)."
        )
        st.caption(
            'Share counts are taken from E\\*TRADE and are exact. A small difference here '
            'is the price source: E\\*TRADE reports the price at the moment you refreshed, '
            'while this page uses the official daily close.'
        )
    else:
        st.caption(
            f'The selected window ends before {anchor:%b %d, %Y}, so there is no live '
            'snapshot to reconcile against. Extend the range to today to check the totals.'
        )

    if len(residual):
        st.markdown('---')
        st.markdown(
            f"**Positions implied on {diag.get('residual_date', first_txn):%b %d, %Y}, "
            'before any transaction in your history**'
        )
        st.caption(
            'Walking backwards should empty the account. Shares left over mean the '
            'transaction feed does not explain everything you hold — usually shares '
            'transferred in from another broker, or transactions E\\*TRADE did not '
            'return. These positions are carried flat back to the start of the chart, '
            'which overstates the earliest values by the amount shown.'
        )
        res_df = residual.rename('Shares').to_frame()
        px_now = result.prices.iloc[-1]
        res_df['Value at latest price'] = [
            (residual[s] * px_now.get(s, float('nan'))) for s in residual.index
        ]
        st.dataframe(
            res_df.style.format({'Shares': '{:,.4g}', 'Value at latest price': '${:,.2f}'},
                                na_rep='—'),
            use_container_width=True,
        )

    if diag.get('unpriced_symbols'):
        st.markdown('---')
        st.warning(
            'No price history available for: '
            + ', '.join(diag['unpriced_symbols'])
            + '. These positions are valued at zero (typically delisted, or the ticker changed).'
        )

    if diag.get('negative_share_symbols'):
        st.markdown('---')
        st.warning(
            'Negative share counts appeared for: '
            + ', '.join(diag['negative_share_symbols'])
            + '. That means more shares were sold than the transaction history accounts '
              'for. Those days are valued at zero rather than as a negative position.'
        )

    if diag.get('missing_price_days'):
        st.caption(
            f"{diag['missing_price_days']:,} position-days had no published price yet "
            f"({', '.join(diag.get('partial_price_symbols', [])[:8])}) and count as zero."
        )

    inferred = {d: s for d, s in sources.items() if s in ('token', 'fuzzy')}
    if inferred:
        st.markdown('---')
        st.markdown('**Tickers inferred by name matching**')
        st.caption('These came from matching the description against your current holdings, not from the API.')
        st.dataframe(
            pd.DataFrame([
                {'Description': d, 'Symbol': trades[trades['Security Name'] == d]['Symbol'].iloc[0],
                 'Method': s}
                for d, s in inferred.items()
            ]),
            use_container_width=True, hide_index=True,
        )

# ── Unmapped securities ───────────────────────────────────────────────────────

if unresolved:
    st.markdown('---')
    unmapped = sr.unmapped_summary(all_trades, unresolved)
    missing_value = unmapped['Gross Traded ($)'].sum()
    st.warning(
        f'**{len(unresolved)} securities could not be matched to a ticker** '
        f'(${missing_value:,.0f} of gross trading activity). Their trades are ignored, '
        'so the history above is incomplete — most visibly, positions you have since '
        'sold never appear.',
        icon='⚠️',
    )
    st.caption(
        'Transaction pulls from E\\*TRADE now include the ticker directly, so refreshing '
        'from the home page is the cleanest fix. Otherwise, map them here — the mapping '
        'is saved and reused.'
    )

    if is_guest():
        st.info('Guest view — mapping is read-only.')
        st.dataframe(unmapped, use_container_width=True, hide_index=True)
    else:
        with st.expander(f'🔧 Map {len(unresolved)} securities to tickers', expanded=False):
            st.markdown(
                '**Identify automatically** looks each name up and then checks the '
                'candidate against the prices you actually paid: a ticker whose daily '
                'high/low range brackets every one of your fills is almost certainly '
                'the right security. Confidence is the fraction of your trades that '
                'passed that check — review anything below 100%.'
            )
            if st.button('🔎 Identify automatically', type='primary'):
                bar = st.progress(0.0, text='Starting…')
                try:
                    proposals = sr.auto_map(
                        all_trades, unresolved,
                        progress=lambda f, m: bar.progress(min(f, 1.0), text=m),
                    )
                finally:
                    bar.empty()
                st.session_state['_hist_proposals'] = proposals

            proposals = st.session_state.get('_hist_proposals')
            if proposals is not None and not proposals.empty:
                editable = proposals[[
                    'Description', 'Proposed Symbol', 'Matched Name',
                    'Trades Checked', 'Price Matches', 'Confidence', 'Note',
                ]].copy()
                edited = st.data_editor(
                    editable,
                    column_config={
                        'Description': st.column_config.TextColumn(disabled=True, width='large'),
                        'Proposed Symbol': st.column_config.TextColumn(
                            'Ticker', help='Edit or clear any row before saving.', width='small',
                        ),
                        'Matched Name': st.column_config.TextColumn(disabled=True),
                        'Trades Checked': st.column_config.NumberColumn(disabled=True, width='small'),
                        'Price Matches': st.column_config.NumberColumn(disabled=True, width='small'),
                        'Confidence': st.column_config.ProgressColumn(
                            min_value=0.0, max_value=1.0, format='percent',
                        ),
                        'Note': st.column_config.TextColumn(disabled=True),
                    },
                    hide_index=True, use_container_width=True, key='_hist_editor',
                )

                save1, save2 = st.columns([1, 3])
                with save1:
                    if st.button('💾 Save mappings'):
                        updated = dict(manual_map)
                        for _, row in edited.iterrows():
                            sym = str(row['Proposed Symbol'] or '').strip().upper()
                            if sym:
                                updated[row['Description']] = sym
                        sr.save_map(updated)
                        st.session_state.pop('_hist_signature', None)
                        st.session_state.pop('_hist_proposals', None)
                        st.success(f'Saved {len(updated)} mapping(s). Rebuilding…')
                        st.rerun()
                with save2:
                    verified = int((edited['Confidence'] >= 1.0).sum())
                    st.caption(
                        f'{verified} of {len(edited)} proposals matched every executed '
                        'price. Clear the ticker on any row you do not want saved.'
                    )
            else:
                st.dataframe(unmapped, use_container_width=True, hide_index=True)

            st.markdown('---')
            st.markdown('**Add a mapping by hand**')
            m1, m2, m3 = st.columns([3, 1, 1])
            with m1:
                pick = st.selectbox('Security', unresolved, key='_hist_manual_desc')
            with m2:
                ticker = st.text_input('Ticker', key='_hist_manual_sym').strip().upper()
            with m3:
                st.markdown('<div style="height:28px"></div>', unsafe_allow_html=True)
                if st.button('Add') and ticker:
                    updated = dict(manual_map)
                    updated[pick] = ticker
                    sr.save_map(updated)
                    st.session_state.pop('_hist_signature', None)
                    st.rerun()

# ── Methodology ───────────────────────────────────────────────────────────────

with st.expander('📐 How this is calculated'):
    st.markdown(
        f"""
**Share counts.** Today's holdings come straight from E\\*TRADE. From there the
walk runs backwards one day at a time:

`shares(day − 1) = (shares(day) − net shares traded that day) ÷ split ratio that day`

Anchoring on today rather than building forward from an empty account means
recent values are exact by construction, and anything the transaction feed fails
to explain accumulates in the distant past where it is visible — that is the
residual reported under Data quality.

**Prices.** Official daily closes, split-adjusted but not dividend-adjusted.
Because the prices are split-adjusted, share counts are converted to today's
share basis before valuation. Dividends are deliberately left out of the price
series: they were paid to you as cash and are already in the cash balance, so
adjusting for them as well would count them twice. Non-trading days are not
plotted; a position held through a market holiday keeps its last close.

**Cash.** Reconstructed the same way, from today's balance backwards: buys
consume cash, sells and dividends produce it, deposits and withdrawals move it.
Transfers between your own accounts appear as offsetting pairs and net to zero.

**Net deposits** counts only external money in and out, so the gap between the
two lines on the chart is investment performance rather than saving.

Data covers **{first_txn:%B %d, %Y}** (first transaction) to
**{anchor:%B %d, %Y}** (last refresh).
        """
    )

with st.expander('🗄️ Price cache'):
    cov = ps.coverage(store)
    st.caption(
        f'{len(cov)} symbols cached locally so the history does not re-download on '
        'every visit. New days are fetched incrementally.'
    )
    if cov:
        cov_df = pd.DataFrame(
            [{'Symbol': s, 'From': v[0], 'To': v[1], 'Days': v[2]} for s, v in sorted(cov.items())]
        )
        st.dataframe(cov_df, use_container_width=True, hide_index=True, height=240)
    if not is_guest() and st.button('♻️ Clear price cache and refetch'):
        ps.clear()
        st.session_state.pop('_hist_signature', None)
        st.rerun()
