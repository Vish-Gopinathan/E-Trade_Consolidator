"""
GitHub Contents API-backed snapshot storage.

On Streamlit Cloud the local filesystem is ephemeral, so month-end snapshots
are written to a file in the GitHub repo and fetched from there on cold starts.

If GITHUB_TOKEN / GITHUB_REPO secrets are absent, save/load are no-ops and the
caller falls back to disk-only behaviour (works locally and within a session).
"""

import base64
import json
import requests

SNAPSHOT_PATH = 'new work/data/month_end_snapshot.json'
_API_BASE = 'https://api.github.com'


def _headers(token: str) -> dict:
    return {
        'Authorization': f'token {token}',
        'Accept': 'application/vnd.github+json',
    }


def _get_file_meta(owner: str, repo: str, token: str) -> dict | None:
    """Return the current file metadata (including sha) or None if it doesn't exist."""
    url = f'{_API_BASE}/repos/{owner}/{repo}/contents/{SNAPSHOT_PATH}'
    resp = requests.get(url, headers=_headers(token), timeout=10)
    if resp.status_code == 200:
        return resp.json()
    return None


def save_snapshot(portfolio_dict: dict, get_secret_fn) -> bool:
    """
    Write portfolio_dict as JSON to the GitHub repo.
    Returns True on success, False if not configured or on error.
    """
    token = get_secret_fn('GITHUB_TOKEN', '')
    repo = get_secret_fn('GITHUB_REPO', '')
    if not token or not repo:
        return False

    try:
        owner, repo_name = repo.split('/', 1)
    except ValueError:
        return False

    try:
        import pandas as pd

        def _serialize(obj):
            if isinstance(obj, pd.DataFrame):
                return obj.to_dict('records')
            if isinstance(obj, pd.Timestamp):
                return obj.isoformat()
            raise TypeError(f'Not serializable: {type(obj)}')

        content_json = json.dumps(portfolio_dict, default=_serialize, ensure_ascii=False)
        content_b64 = base64.b64encode(content_json.encode()).decode()

        existing = _get_file_meta(owner, repo_name, token)
        payload: dict = {
            'message': f'chore: update month-end snapshot ({portfolio_dict.get("snapshot_date", "unknown")})',
            'content': content_b64,
        }
        if existing:
            payload['sha'] = existing['sha']

        url = f'{_API_BASE}/repos/{owner}/{repo_name}/contents/{SNAPSHOT_PATH}'
        resp = requests.put(url, headers=_headers(token), json=payload, timeout=15)
        return resp.status_code in (200, 201)
    except Exception:
        return False


def load_snapshot(get_secret_fn) -> dict | None:
    """
    Fetch and decode the snapshot JSON from the GitHub repo.
    Returns None if not configured, file doesn't exist, or on error.
    """
    token = get_secret_fn('GITHUB_TOKEN', '')
    repo = get_secret_fn('GITHUB_REPO', '')
    if not token or not repo:
        return None

    try:
        owner, repo_name = repo.split('/', 1)
    except ValueError:
        return None

    try:
        meta = _get_file_meta(owner, repo_name, token)
        if not meta:
            return None
        content_b64 = meta.get('content', '')
        content_json = base64.b64decode(content_b64).decode()
        raw = json.loads(content_json)

        import pandas as pd

        for key in ('holdings', 'transactions', 'cash_flows', 'income'):
            if key in raw and isinstance(raw[key], list):
                raw[key] = pd.DataFrame(raw[key])

        return raw
    except Exception:
        return None
