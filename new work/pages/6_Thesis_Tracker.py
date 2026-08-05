import sys
from pathlib import Path
import streamlit as st
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))
from lib.common import require_auth, render_sidebar_status, STATUS_COLORS
from lib.thesis_store import load_all, get, save_stock, STATUS_OPTIONS, STATUS_EMOJI

st.set_page_config(page_title='Thesis Tracker', page_icon='🧠', layout='wide')
require_auth()

with st.sidebar:
    st.title('📈 Portfolio')
    render_sidebar_status()

st.title('🧠 Thesis Tracker')

portfolio = st.session_state.get('portfolio')
if not portfolio:
    st.warning('No data loaded. Go to the home page and refresh.')
    st.stop()

holdings_df = portfolio.get('holdings')
if holdings_df is None or (hasattr(holdings_df, 'empty') and holdings_df.empty):
    st.info('No holdings data available.')
    st.stop()

symbols = sorted(holdings_df[holdings_df['Symbol'] != 'CASH']['Symbol'].unique().tolist())
thesis_data = load_all()

# ── Overview table ────────────────────────────────────────────────────────────
st.subheader('Thesis Overview')

overview_rows = []
for sym in symbols:
    entry = get(sym, thesis_data)
    status = entry.get('status', 'Unreviewed')
    emoji = STATUS_EMOJI.get(status, '⬜')
    last_note = ''
    notes = entry.get('notes') or []
    if notes:
        last_note = notes[-1].get('date', '')
    overview_rows.append({
        'Symbol': sym,
        'Status': f'{emoji} {status}',
        'Target Price': entry.get('target_price'),
        'Hold Period': entry.get('hold_period', ''),
        'Last Updated': entry.get('updated', ''),
        'Notes': len(notes),
    })

overview_df = pd.DataFrame(overview_rows)
st.dataframe(overview_df.style.format({'Target Price': '${:,.2f}'}, na_rep='—'),
             use_container_width=True, hide_index=True)

st.markdown('---')

# ── Per-stock editor ──────────────────────────────────────────────────────────
st.subheader('Edit Thesis')
selected = st.selectbox('Select a holding', options=symbols, key='thesis_select')

if selected:
    entry = get(selected, thesis_data)
    st.markdown(f'### {selected}')

    # Status
    current_status = entry.get('status', 'Unreviewed')
    status_idx = STATUS_OPTIONS.index(current_status) if current_status in STATUS_OPTIONS else 0
    new_status = st.selectbox(
        'Status',
        options=STATUS_OPTIONS,
        index=status_idx,
        format_func=lambda s: f'{STATUS_EMOJI.get(s, "")} {s}',
        key=f'status_{selected}',
    )

    col1, col2 = st.columns(2)
    with col1:
        new_thesis = st.text_area('Thesis (core investment case)', value=entry.get('thesis', ''),
                                  height=120, key=f'thesis_{selected}')
        new_catalysts = st.text_area('Key Catalysts', value=entry.get('catalysts', ''),
                                     height=100, key=f'catalysts_{selected}')
        new_hold = st.text_input('Expected Hold Period', value=entry.get('hold_period', ''),
                                 key=f'hold_{selected}', placeholder='e.g. 2–3 years, Long-term')

    with col2:
        new_rationale = st.text_area('Entry Rationale', value=entry.get('entry_rationale', ''),
                                     height=120, key=f'rationale_{selected}')
        tp = entry.get('target_price')
        new_target = st.number_input('Target Price ($)', value=float(tp) if tp else 0.0,
                                     min_value=0.0, step=1.0, format='%.2f',
                                     key=f'target_{selected}')

    # Notes log
    st.markdown('**Notes Log**')
    notes = entry.get('notes') or []
    if notes:
        for note in reversed(notes):
            with st.container(border=True):
                st.caption(note.get('date', ''))
                st.write(note.get('note', ''))
    else:
        st.caption('No notes yet.')

    new_note = st.text_area('Add a note', placeholder='What changed? What did you observe?',
                            key=f'note_{selected}', height=80)

    if st.button('Save', type='primary', key=f'save_{selected}'):
        updates = {
            'status': new_status,
            'thesis': new_thesis,
            'entry_rationale': new_rationale,
            'catalysts': new_catalysts,
            'target_price': new_target if new_target > 0 else None,
            'hold_period': new_hold,
        }
        save_stock(selected, updates, new_note)
        st.success(f'Saved thesis for {selected}.')
        st.rerun()
