import json
from datetime import date
from pathlib import Path

_DATA_DIR = Path(__file__).parent.parent / 'data'
_THESIS_FILE = _DATA_DIR / 'thesis.json'

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
    'On Track': '🟢',
    'Watch': '🟡',
    'At Risk': '🟠',
    'Broken': '🔴',
    'Exited': '⚫',
    'Unreviewed': '⬜',
}


def load_all() -> dict:
    if not _THESIS_FILE.exists():
        return {}
    with open(_THESIS_FILE) as f:
        return json.load(f)


def save_all(thesis_data: dict):
    _DATA_DIR.mkdir(exist_ok=True)
    with open(_THESIS_FILE, 'w') as f:
        json.dump(thesis_data, f, indent=2)


def get(symbol: str, thesis_data: dict | None = None) -> dict:
    if thesis_data is None:
        thesis_data = load_all()
    return {**_DEFAULT, **thesis_data.get(symbol, {})}


def save_stock(symbol: str, updates: dict, new_note: str = ''):
    thesis_data = load_all()
    current = get(symbol, thesis_data)
    current.update(updates)
    current['updated'] = date.today().isoformat()
    if new_note and new_note.strip():
        current.setdefault('notes', [])
        current['notes'].append({'date': date.today().isoformat(), 'note': new_note.strip()})
    thesis_data[symbol] = current
    save_all(thesis_data)
