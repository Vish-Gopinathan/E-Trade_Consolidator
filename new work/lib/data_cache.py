import json
import pandas as pd
from pathlib import Path

_DATA_DIR = Path(__file__).parent.parent / 'data'
_CACHE_FILE = _DATA_DIR / 'portfolio_cache.json'

_DATE_COLS = {'Date', 'Date Acquired'}


def save_portfolio(holdings_df, transactions_df, cash_flows_df, income_df, analytics_report, summary, fetched_at):
    _DATA_DIR.mkdir(exist_ok=True)
    cache = {
        'fetched_at': fetched_at,
        'holdings': _df_to_records(holdings_df),
        'transactions': _df_to_records(transactions_df),
        'cash_flows': _df_to_records(cash_flows_df),
        'income': _df_to_records(income_df),
        'analytics_report': _make_serialisable(analytics_report or {}),
        'summary': _make_serialisable(summary or {}),
    }
    with open(_CACHE_FILE, 'w') as f:
        json.dump(cache, f, indent=2)


def load_portfolio():
    if not _CACHE_FILE.exists():
        return None
    with open(_CACHE_FILE) as f:
        cache = json.load(f)
    cache['holdings'] = _records_to_df(cache.get('holdings', []))
    cache['transactions'] = _records_to_df(cache.get('transactions', []))
    cache['cash_flows'] = _records_to_df(cache.get('cash_flows', []))
    cache['income'] = _records_to_df(cache.get('income', []))
    return cache


def _df_to_records(df):
    if df is None or (hasattr(df, 'empty') and df.empty):
        return []
    return json.loads(df.to_json(orient='records', date_format='iso', default_handler=str))


def _records_to_df(records):
    if not records:
        return pd.DataFrame()
    df = pd.DataFrame(records)
    for col in df.columns:
        if col in _DATE_COLS:
            df[col] = pd.to_datetime(df[col], errors='coerce')
        else:
            df[col] = pd.to_numeric(df[col], errors='ignore')
    return df


def _make_serialisable(obj):
    if isinstance(obj, dict):
        return {k: _make_serialisable(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_make_serialisable(v) for v in obj]
    if isinstance(obj, float) and obj != obj:
        return None
    return obj
