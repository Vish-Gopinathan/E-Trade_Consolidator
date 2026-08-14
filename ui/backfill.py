"""
Backfill: entering the history E*TRADE will not serve.

The API returns roughly two years of transactions and the window slides forward
daily, so an older account has a permanent blind spot at the far end. The ledger
stops that spot from *growing*; this page is how it gets filled in.

The page opens with a worklist rather than a blank form, because the app already
knows exactly what is missing — a sell with no purchase behind it names itself.
"""

import datetime

import pandas as pd
import streamlit as st

from portfolio import schema
from portfolio.storage import ledger
from ui.common import is_guest, md, money, page_header, require_portfolio

page_header(
    'Backfill', '📥',
    'Fill in the history E\\*TRADE no longer serves, so the numbers can add up.',
)

portfolio = require_portfolio()
report = portfolio.get('analytics_report') or {}
section = report.get(schema.RECONCILIATION) or {}
coverage = portfolio.get('transactions_coverage') or {}

# The ledger holds the real account. Demo mode exists to be shown without
# credentials, so it must not read from it — the worklist above is computed from
# the fictional portfolio, but the editor and the "what I have added" table would
# otherwise reach straight past the fixture into real holdings.
demo_mode = bool(st.session_state.get('_demo_mode'))
read_only = demo_mode or is_guest()

if demo_mode:
    st.info(
        '🎭 Demo mode — the worklist below is computed from the fictional '
        'portfolio. Adding rows is disabled, and the real ledger is not read.'
    )
elif is_guest():
    st.info('Guests can see what is missing but cannot add rows.')

# ── Where things stand ────────────────────────────────────────────────────────

residual = section.get(schema.UNEXPLAINED_RESIDUAL) or 0.0
k1, k2, k3 = st.columns(3)
k1.metric('Unexplained', money(residual, 0))
k2.metric('History starts', section.get(schema.HISTORY_STARTS) or '—')
k3.metric('Hand-entered rows', section.get(schema.MANUAL_ROW_COUNT) or 0)

if coverage:
    st.caption(
        f'Ledger holds {coverage.get("rows", 0):,} row(s) from '
        f'{coverage.get("start", "?")} to {coverage.get("end", "?")}. '
        'The ledger only ever grows — a refresh can add history but never remove it.'
    )

# ── The worklist ──────────────────────────────────────────────────────────────

st.subheader('What is missing')

unmatched_count = section.get(schema.UNMATCHED_SELL_COUNT) or 0
orphans = section.get(schema.ORPHAN_LOTS) or {}

if not unmatched_count and not orphans and abs(residual) < 1:
    st.success('Nothing outstanding — every dollar is explained by recorded activity.')
else:
    if unmatched_count:
        st.markdown(
            f'**{unmatched_count} position(s) were sold with no purchase on record** — '
            f'{md(money(section.get(schema.UNMATCHED_SELL_PROCEEDS), 0))} of proceeds whose '
            'cost basis is unknown. Entering the original buys turns each one into a '
            'real realised gain or loss instead of a blank.'
        )
    if orphans:
        st.markdown(
            '**Bought but neither held nor sold:** '
            + ', '.join(f'`{symbol}` ({shares:,.4g} shares)' for symbol, shares in orphans.items())
            + ' — either a sale is missing, or the position left in a transfer.'
        )
    if abs(residual) >= 1:
        st.markdown(
            f'**{md(money(residual, 0))} unexplained.** Most likely an opening transfer of '
            'securities, or deposits, from before the history begins. Entering those '
            'opening positions as purchases is what closes it.'
        )

# ── Add rows ──────────────────────────────────────────────────────────────────

st.subheader('Add missing history')

_TYPES = ['Bought', 'Sold', 'Transfer', 'Dividend', 'Interest']
_BLANK = pd.DataFrame([
    {'Date': None, 'Transaction Type': 'Bought', 'Symbol': '', 'Quantity': None,
     'Price': None, 'Total Value': None, 'Account': ledger.UNATTRIBUTED, 'Description': ''}
])


def _to_ledger_rows(frame: pd.DataFrame, note: str) -> list:
    """Turn edited rows into ledger records, skipping blanks."""
    rows = []
    for row in frame.to_dict('records'):
        date, amount = row.get('Date'), row.get('Total Value')
        if not date or amount in (None, '') or pd.isna(amount):
            continue
        t_type = row.get('Transaction Type') or 'Transfer'
        quantity = row.get('Quantity')
        # Sells are stored negative, matching how E*TRADE reports them — the
        # realised-P&L walk keys the direction off the sign of the quantity.
        if t_type == 'Sold' and quantity not in (None, '') and not pd.isna(quantity):
            quantity = -abs(float(quantity))
        rows.append({
            'Date': date,
            'Security Name': row.get('Description') or f'{t_type} (entered by hand)',
            'Symbol': (row.get('Symbol') or '').strip().upper() or None,
            'Quantity': quantity,
            'Price': row.get('Price'),
            'Total Value': float(amount),
            'Transaction Type': t_type,
            'Account': row.get('Account') or ledger.UNATTRIBUTED,
            'Note': note,
        })
    return rows


tab_manual, tab_csv, tab_existing = st.tabs(['Type rows', 'Import a CSV', 'What I have added'])

with tab_manual:
    st.caption(
        'One row per transaction. **Total Value** is the dollar amount and is the '
        'only field that must be filled — quantity and price help match a sale to '
        'its purchase. Money in is positive; money out is negative.'
    )
    edited = st.data_editor(
        _BLANK, num_rows='dynamic', use_container_width=True, key='backfill_editor',
        column_config={
            'Date': st.column_config.DateColumn('Date', required=True),
            'Transaction Type': st.column_config.SelectboxColumn(
                'Type', options=_TYPES, required=True),
            'Symbol': st.column_config.TextColumn('Symbol', help='Blank for cash movements'),
            'Quantity': st.column_config.NumberColumn('Quantity', format='%.4f'),
            'Price': st.column_config.NumberColumn('Price', format='$%.2f'),
            'Total Value': st.column_config.NumberColumn('Total Value', format='$%.2f'),
            'Account': st.column_config.TextColumn('Account'),
            'Description': st.column_config.TextColumn('Description', width='medium'),
        },
    )
    if st.button('Add these rows', disabled=read_only, type='primary'):
        rows = _to_ledger_rows(edited, f'hand-entered {datetime.date.today()}')
        if not rows:
            st.warning('Nothing to add — every row needs at least a date and a total value.')
        else:
            try:
                added = ledger.add_manual(rows)
            except Exception as exc:
                st.error(f'Could not save: {exc}')
            else:
                skipped = len(rows) - added
                st.success(
                    f'Added {added} row(s).'
                    + (f' {skipped} already present and skipped.' if skipped else '')
                    + ' Refresh from Overview to see the effect.'
                )

with tab_csv:
    st.caption(
        'E\\*TRADE\'s website serves transaction downloads for periods the API will '
        'not — usually the fastest way to close a gap. Upload the CSV and check the '
        'preview before committing.'
    )
    upload = st.file_uploader('Transaction CSV', type=['csv'], disabled=read_only)
    if upload is not None:
        from portfolio import importer
        try:
            parsed, problems = importer.read_csv(upload)
        except Exception as exc:
            st.error(f'Could not read that file: {exc}')
        else:
            if problems:
                st.warning('\n\n'.join(f'• {p}' for p in problems))
            if parsed.empty:
                st.error('No usable rows found. Check the column names against the preview.')
            else:
                st.write(
                    f'**{len(parsed)} row(s)**, '
                    f'{parsed["Date"].min():%-d %b %Y} → {parsed["Date"].max():%-d %b %Y}'
                )
                st.dataframe(parsed.head(50), use_container_width=True, hide_index=True)
                if st.button('Import these rows', disabled=read_only, type='primary'):
                    try:
                        added = ledger.add_manual(
                            parsed.assign(Note=f'CSV import {datetime.date.today()}')
                                  .to_dict('records'),
                            source=ledger.SOURCE_CSV,
                        )
                    except Exception as exc:
                        st.error(f'Could not save: {exc}')
                    else:
                        st.success(
                            f'Imported {added} row(s). '
                            f'{len(parsed) - added} already present and skipped.'
                        )

with tab_existing:
    if demo_mode:
        st.info('Not available in demo mode — this reads the real ledger.')
        manual = pd.DataFrame()
    else:
        frame = ledger.load()
        manual = frame[frame['Source'].isin([ledger.SOURCE_MANUAL, ledger.SOURCE_CSV])] \
            if 'Source' in frame.columns else pd.DataFrame()
    if manual.empty and not demo_mode:
        st.info('Nothing entered by hand yet.')
    else:
        st.caption(
            'Rows you added. E\\*TRADE rows are not listed here and cannot be edited — '
            'only what you entered yourself.'
        )
        st.dataframe(
            manual[['Date', 'Transaction Type', 'Symbol', 'Quantity', 'Total Value',
                    'Account', 'Security Name', 'Source', 'Ledger ID']],
            use_container_width=True, hide_index=True,
        )
        target = st.selectbox(
            'Remove a row', manual['Ledger ID'].tolist(),
            format_func=lambda i: (
                f'#{i} — ' + str(manual[manual["Ledger ID"] == i]["Security Name"].iloc[0])[:60]
            ),
            index=None, placeholder='Choose a row to delete',
        )
        if target is not None and st.button('Delete', disabled=read_only):
            try:
                ledger.delete_row(int(target))
            except Exception as exc:
                st.error(f'Could not delete: {exc}')
            else:
                st.success(f'Removed row #{target}.')
                st.rerun()
