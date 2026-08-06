"""
Point-in-time portfolio snapshots.

A snapshot is what guests see and what a cold start falls back to when no cache
exists. It uses the same serialisation as :mod:`portfolio.storage.cache`, so a
snapshot file and a cache file are interchangeable in shape.

**Local only, deliberately.** An earlier version pushed snapshots to this GitHub
repository through the Contents API — bypassing ``.gitignore``, into a repo that
is public. Holdings, cost basis and full transaction history would have been
world-readable. Moving a snapshot between machines is now an explicit
download-then-upload the user performs, not something the app does on its own.
"""

import json

from portfolio import paths
from portfolio.storage.cache import from_jsonable, to_jsonable

SNAPSHOT_FILE = paths.DATA_DIR / 'month_end_snapshot.json'


def exists() -> bool:
    return SNAPSHOT_FILE.exists()


def save(portfolio: dict, snapshot_date=None) -> None:
    """Write the current portfolio as the snapshot, stamped with today's date."""
    import datetime

    payload = dict(portfolio)
    payload['snapshot_date'] = (snapshot_date or datetime.date.today()).isoformat() \
        if not isinstance(snapshot_date, str) else snapshot_date
    SNAPSHOT_FILE.parent.mkdir(parents=True, exist_ok=True)
    SNAPSHOT_FILE.write_text(json.dumps(to_jsonable(payload), indent=2))


def load() -> dict:
    """Read the stored snapshot."""
    return from_jsonable(json.loads(SNAPSHOT_FILE.read_text()))


def load_bytes(raw: bytes) -> dict:
    """Read a snapshot from an uploaded file's bytes."""
    return from_jsonable(json.loads(raw.decode()))


def read_bytes() -> bytes:
    """Raw snapshot file contents, for download."""
    return SNAPSHOT_FILE.read_bytes()
