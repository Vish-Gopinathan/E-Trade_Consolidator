"""
User decisions about transfer counterparties.

When money moves to ``TRANSFER TO XXXXX1607``, only the account holder knows
whether 1607 is another account of theirs (an internal move that should not count
as a withdrawal) or somewhere else entirely (a real withdrawal). The app asks once
in the Transfer Review panel and records the answer here, so the same transfer is
never classified two different ways on two different days.

Keyed by the last four digits, which is all E*TRADE reveals of a counterparty.
"""

import json

from portfolio import paths

MAP_PATH = paths.DATA_DIR / 'account_map.json'

INTERNAL = 'internal'
EXTERNAL = 'external'


def load() -> dict:
    """Return ``{'1607': 'external', ...}``. Empty when nothing has been tagged."""
    if not MAP_PATH.exists():
        return {}
    try:
        data = json.loads(MAP_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return {}
    return {str(k)[-4:]: v for k, v in data.items() if v in (INTERNAL, EXTERNAL)}


def save(account_map: dict) -> None:
    """Overwrite the stored map."""
    MAP_PATH.parent.mkdir(parents=True, exist_ok=True)
    MAP_PATH.write_text(json.dumps(account_map, indent=2, sort_keys=True))


def tag(last4: str, kind: str) -> dict:
    """Record one decision and return the updated map."""
    if kind not in (INTERNAL, EXTERNAL):
        raise ValueError(f'kind must be {INTERNAL!r} or {EXTERNAL!r}, got {kind!r}')
    account_map = load()
    account_map[str(last4)[-4:]] = kind
    save(account_map)
    return account_map


def forget(last4: str) -> dict:
    """Drop one decision so the app asks about it again."""
    account_map = load()
    account_map.pop(str(last4)[-4:], None)
    save(account_map)
    return account_map
