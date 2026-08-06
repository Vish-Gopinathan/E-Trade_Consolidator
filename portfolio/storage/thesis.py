"""
Investment thesis notes, kept per symbol.

Two files, deliberately. Demo mode writes to ``thesis_demo.json`` while the real
app uses ``thesis.json``; they used to share one path, so launching the demo
locally overwrote every hand-written note about a real position with fixtures.
Which file is in play is decided by :func:`_path`, not by the caller remembering.
"""

import json
import logging
from datetime import date

from portfolio import paths

LOGGER = logging.getLogger(__name__)

THESIS_FILE = paths.DATA_DIR / 'thesis.json'
DEMO_THESIS_FILE = paths.DATA_DIR / 'thesis_demo.json'

_DEFAULT = {
    'status': 'Unreviewed',
    'thesis': '',
    'entry_rationale': '',
    'catalysts': '',
    'target_price': None,
    'hold_period': '',
    'notes': [],
    'updated': None,
}

STATUS_OPTIONS = ['Unreviewed', 'On Track', 'Watch', 'At Risk', 'Broken', 'Exited']

STATUS_EMOJI = {
    'On Track': '🟢', 'Watch': '🟡', 'At Risk': '🟠',
    'Broken': '🔴', 'Exited': '⚫', 'Unreviewed': '⬜',
}


def _path(demo: bool | None = None):
    """
    Which thesis file to use.

    ``demo=None`` asks Streamlit whether demo mode is active, so ordinary callers
    cannot write demo fixtures over real notes by forgetting to pass a flag.
    """
    if demo is None:
        try:
            import streamlit as st
            demo = bool(st.session_state.get('_demo_mode'))
        except Exception:
            demo = False
    return DEMO_THESIS_FILE if demo else THESIS_FILE


def load_all(demo: bool | None = None) -> dict:
    """All stored theses, keyed by symbol."""
    path = _path(demo)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        LOGGER.warning('thesis file unreadable (%s); treating as empty', exc)
        return {}


def save_all(thesis_data: dict, demo: bool | None = None) -> None:
    """Overwrite the thesis file."""
    path = _path(demo)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(thesis_data, indent=2, sort_keys=True))


def get(symbol: str, thesis_data: dict | None = None, demo: bool | None = None) -> dict:
    """One symbol's thesis, with defaults filled in for anything unset."""
    if thesis_data is None:
        thesis_data = load_all(demo)
    return {**_DEFAULT, **thesis_data.get(symbol, {})}


def save_stock(symbol: str, updates: dict, new_note: str = '', demo: bool | None = None) -> None:
    """Update one symbol's thesis, optionally appending a dated note."""
    thesis_data = load_all(demo)
    current = get(symbol, thesis_data, demo)
    current.update(updates)
    current['updated'] = date.today().isoformat()
    if new_note and new_note.strip():
        current.setdefault('notes', [])
        current['notes'].append({'date': date.today().isoformat(), 'note': new_note.strip()})
    thesis_data[symbol] = current
    save_all(thesis_data, demo)
