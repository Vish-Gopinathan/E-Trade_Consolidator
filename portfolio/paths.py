"""
Canonical filesystem locations.

Every module resolves paths through here rather than counting ``parent`` hops from
its own ``__file__``. Those hop counts silently break the moment a module moves
between directories, and the failure mode is a store that reads empty rather than
an import error — exactly the kind of bug that hides for a day.
"""

from pathlib import Path

#: Repository root — the directory holding app.py, cli.py and requirements.txt.
ROOT = Path(__file__).resolve().parent.parent

#: Runtime state written by the app: portfolio cache, price and earnings stores,
#: thesis notes, the transfer account map. Gitignored — this is real financial data.
DATA_DIR = ROOT / 'data'

#: Checked-in configuration the user is expected to edit (sector mappings).
CONFIG_DIR = ROOT / 'config'


def data_file(name: str) -> Path:
    """Return the path to a file in ``data/``, creating the directory if needed."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return DATA_DIR / name
