"""
Resolve E*TRADE trade descriptions to ticker symbols.

Newer transaction pulls carry a ``Symbol`` column straight from the API, but
older cached pulls only have the security description ("SHOPIFY INC CL A
UNSOLICITED TRADE"), and positions that were fully sold are not in current
holdings to match against. Resolution therefore runs a chain:

    1. the transaction's own Symbol column, when populated
    2. a saved manual mapping (data/symbol_map.json) — always wins over guesses
    3. exact, then token-containment, then fuzzy match against current holdings
    4. nothing — the caller surfaces it for the user to map by hand

Nothing is ever guessed silently. For descriptions that reach step 4, the UI can
ask yfinance for candidate tickers and then check each candidate against the
prices actually paid on the trade dates (``verify_candidate``) — a ticker whose
daily high/low range brackets every executed price is a match by evidence, not
by name similarity. The user still confirms before anything is saved.
"""

import difflib
import json
import re

import pandas as pd

from portfolio import paths

MAP_PATH = paths.DATA_DIR / 'symbol_map.json'

# E*TRADE appends order-routing noise to the security name
_NOISE = (
    ' UNSOLICITED TRADE', ' SOLICITED TRADE', ' UNSOLICITED', ' SOLICITED',
)

# Corporate-form and share-class words that differ between the transaction
# description and the holdings description for the same security
_FILLER = re.compile(
    r'\b(COM|COMMON|STK|CAP|SHS|NEW|THE|INC|CORP|CORPORATION|COMPANY|CO|LTD|LIMITED|'
    r'PLC|SA|NV|AG|HLDGS|HOLDINGS|GROUP|GRP|ETF|TR|TRUST|FUND|'
    r'CL|CLASS|A|B|C|SUB|VTG|ADR|ADS|SPON|SERIES|SER|I|1)\b'
)


def normalise(desc: str) -> str:
    """Strip routing noise, punctuation and corporate-form filler for matching."""
    s = (desc or '').upper()
    for noise in _NOISE:
        s = s.replace(noise, '')
    s = re.sub(r'[^A-Z0-9& ]', ' ', s)
    s = _FILLER.sub(' ', s)
    return ' '.join(s.split())


# ── Manual map persistence ────────────────────────────────────────────────────

def load_map() -> dict:
    if MAP_PATH.exists():
        try:
            return json.loads(MAP_PATH.read_text())
        except Exception:
            return {}
    return {}


def save_map(mapping: dict):
    MAP_PATH.parent.mkdir(parents=True, exist_ok=True)
    clean = {k: v.strip().upper() for k, v in mapping.items() if v and v.strip()}
    MAP_PATH.write_text(json.dumps(clean, indent=2, sort_keys=True))


# ── Resolution ────────────────────────────────────────────────────────────────

def build_holdings_index(holdings_df) -> dict:
    """{normalised description: symbol} for everything currently held."""
    index = {}
    if holdings_df is None or holdings_df.empty:
        return index
    for _, row in holdings_df.iterrows():
        sym = str(row.get('Symbol') or '').strip()
        if not sym or sym.upper() in ('CASH', 'TOTAL'):
            continue
        desc = row.get('Symbol Description')
        if desc:
            index[normalise(desc)] = sym
        # A description that is just the ticker is a useful match too
        index.setdefault(normalise(sym), sym)
    return index


def _token_match(key: str, index: dict):
    """
    Best holdings entry whose words contain all the words of ``key``.

    Descriptions of the same security differ by extra words far more often than
    by spelling — "VANECK RARE EARTH/STRATEGIC" against "VANECK RARE EARTH AND
    STRATEGIC METALS ETF" — which character-ratio matching handles badly. A
    containment score copes, provided the winner is clearly ahead of the runner-up.
    """
    words = set(key.split())
    if len(words) < 2:
        return None

    scored = []
    for cand, sym in index.items():
        cand_words = set(cand.split())
        if not cand_words:
            continue
        overlap = len(words & cand_words)
        scored.append((overlap / len(words), overlap, cand, sym))
    if not scored:
        return None

    scored.sort(reverse=True)
    best = scored[0]
    if best[0] < 0.75 or best[1] < 2:
        return None
    # Ambiguous when a second candidate scores just as well
    if len(scored) > 1 and scored[1][0] >= best[0]:
        return None
    return best[3]


def resolve_trades(trades_df, holdings_df, manual_map=None):
    """
    Add a ``Symbol`` column to a trades DataFrame.

    Returns (trades_with_symbol, unresolved_descriptions, source_by_description)
    where ``source_by_description`` records how each symbol was arrived at
    ('api', 'manual', 'exact', 'token', 'fuzzy') so the UI can show what was
    inferred rather than read from the API.
    """
    manual_map = {k: v.upper() for k, v in (manual_map or {}).items()}
    holdings_index = build_holdings_index(holdings_df)

    df = trades_df.copy()
    if 'Symbol' not in df.columns:
        df['Symbol'] = ''
    df['Symbol'] = df['Symbol'].fillna('').astype(str).str.strip().str.upper()

    resolved, sources = {}, {}

    # Descriptions that already carry an API symbol on at least one row
    for desc, group in df.groupby('Security Name'):
        api_syms = [s for s in group['Symbol'].unique() if s]
        if api_syms:
            resolved[desc] = api_syms[0]
            sources[desc] = 'api'

    unresolved = []
    for desc in df['Security Name'].unique():
        if desc in resolved:
            continue
        if desc in manual_map:
            resolved[desc] = manual_map[desc]
            sources[desc] = 'manual'
            continue
        key = normalise(desc)
        if key in holdings_index:
            resolved[desc] = holdings_index[key]
            sources[desc] = 'exact'
            continue
        token_sym = _token_match(key, holdings_index)
        if token_sym:
            resolved[desc] = token_sym
            sources[desc] = 'token'
            continue
        match = difflib.get_close_matches(key, list(holdings_index), n=1, cutoff=0.85)
        if match:
            resolved[desc] = holdings_index[match[0]]
            sources[desc] = 'fuzzy'
            continue
        unresolved.append(desc)

    df['Symbol'] = df['Security Name'].map(resolved).fillna('')
    return df, sorted(unresolved), sources


# ── Suggestions for the manual-mapping UI ─────────────────────────────────────

_US_EXCHANGES = {'NMS', 'NYQ', 'NGM', 'ASE', 'PCX', 'BTS', 'NCM', 'NYS', 'ARCA'}


def _search_queries(description: str) -> list:
    """
    Several phrasings of one name, because Yahoo's search is uneven about
    brokerage-style descriptions. "TJX COS INC NEW" finds only foreign listings
    while "TJX" finds the real one, and "E L F BEAUTY" finds currency pairs while
    "ELF BEAUTY" finds the company. Every query's hits are pooled and then
    settled by price evidence, so a query that returns junk costs nothing.
    """
    base = normalise(description)
    if not base:
        return []
    words = base.split()

    queries = [base]

    # Collapse runs of single letters: "E L F BEAUTY" → "ELF BEAUTY"
    collapsed, buf = [], []
    for w in words:
        if len(w) == 1:
            buf.append(w)
            continue
        if buf:
            collapsed.append(''.join(buf))
            buf = []
        collapsed.append(w)
    if buf:
        collapsed.append(''.join(buf))
    if collapsed != words:
        queries.append(' '.join(collapsed))

    if len(words) > 2:
        queries.append(' '.join(words[:2]))
    if len(words) > 1 and len(words[0]) >= 3:
        queries.append(words[0])

    return list(dict.fromkeys(q for q in queries if q))


def suggest_symbols(description: str, limit: int = 5) -> list:
    """
    Best-effort ticker suggestions from yfinance search, US listings first.
    Returns [(symbol, name, exchange)]. Never raises — an empty list just means
    the user types the ticker themselves.
    """
    import yfinance as yf

    seen, results = set(), []
    for q in _search_queries(description):
        try:
            quotes = yf.Search(q, max_results=10).quotes or []
        except Exception:
            quotes = []
        for item in quotes:
            sym = item.get('symbol')
            if not sym or sym in seen:
                continue
            seen.add(sym)
            results.append({
                'symbol': sym,
                'name': item.get('shortname') or item.get('longname') or '',
                'exchange': item.get('exchange') or '',
                'type': item.get('quoteType') or '',
            })

    # Name similarity leads the ranking. Broad queries like the "ISHARES" in
    # "ISHARES MSCI INDIA ETF" return dozens of same-family funds, and ordering
    # by exchange alone lets an unrelated one (iShares MSCI South Korea) crowd
    # the real match out of the shortlist before price checking ever sees it.
    target = set(normalise(description).split())

    def rank(r):
        name_words = set(normalise(r['name']).split())
        overlap = len(target & name_words) / len(target) if target else 0.0
        return (
            -round(overlap, 3),
            0 if r['exchange'] in _US_EXCHANGES else 1,
            0 if r['type'] in ('EQUITY', 'ETF') else 1,
            len(r['symbol']),
        )

    results.sort(key=rank)
    return [(r['symbol'], r['name'], r['exchange']) for r in results[:limit]]


# ── Verification against executed trade prices ────────────────────────────────

def verify_candidate(symbol: str, trades: pd.DataFrame, tolerance: float = 0.02) -> dict:
    """
    Check a candidate ticker against the prices actually paid.

    Every trade has a date and an execution price. If the candidate is the right
    security, each execution price must fall inside that ticker's high/low range
    for the day (widened slightly for odd-lot and extended-hours fills). A name
    can look convincing and still be the wrong listing; a price range that
    brackets all twelve of your fills is hard to get wrong.

    Yahoo's history is split-adjusted, so each day's range is scaled back up by
    the splits that happened since. Without that, every position that later split
    fails: CrowdStrike fills at $415 look nothing like the $103 the back-adjusted
    series shows, until the 4:1 split is undone.

    Returns {'checked', 'matched', 'score', 'worst_gap_pct', 'error'}.
    """
    import yfinance as yf

    out = {'checked': 0, 'matched': 0, 'score': 0.0, 'worst_gap_pct': None, 'error': ''}
    sample = trades.dropna(subset=['Date', 'Price'])
    if sample.empty:
        out['error'] = 'no priced trades'
        return out

    days = pd.to_datetime(sample['Date']).dt.normalize()
    try:
        # Runs to today, not just to the last trade, so splits after the final
        # fill are still visible and can be undone.
        hist = yf.Ticker(symbol).history(
            start=(days.min() - pd.Timedelta(days=5)).date().isoformat(),
            end=(pd.Timestamp.today() + pd.Timedelta(days=1)).date().isoformat(),
            auto_adjust=False,
            actions=True,
        )
    except Exception as exc:
        out['error'] = str(exc)[:120]
        return out

    if hist is None or hist.empty or 'Close' not in hist.columns:
        out['error'] = 'no price history'
        return out

    idx = pd.DatetimeIndex(hist.index)
    if idx.tz is not None:
        idx = idx.tz_localize(None)
    hist = hist.copy()
    hist.index = idx.normalize()

    splits = pd.Series(dtype=float)
    if 'Stock Splits' in hist.columns:
        splits = hist['Stock Splits']
        splits = splits[(splits.notna()) & (splits != 0)]

    gaps = []
    for day, paid in zip(days, pd.to_numeric(sample['Price'], errors='coerce')):
        if pd.isna(paid) or paid <= 0 or day not in hist.index:
            continue
        row = hist.loc[day]
        if isinstance(row, pd.DataFrame):
            row = row.iloc[0]
        # Undo every split that took effect after this trade
        factor = float(splits[splits.index > day].prod()) if not splits.empty else 1.0
        low = float(row.get('Low', row['Close'])) * factor * (1 - tolerance)
        high = float(row.get('High', row['Close'])) * factor * (1 + tolerance)
        out['checked'] += 1
        if low <= paid <= high:
            out['matched'] += 1
            gaps.append(0.0)
        else:
            ref = float(row['Close']) * factor
            gaps.append(abs(paid - ref) / ref * 100 if ref else 100.0)

    if out['checked']:
        out['score'] = out['matched'] / out['checked']
        out['worst_gap_pct'] = max(gaps) if gaps else None
    else:
        out['error'] = 'no overlapping trading days'
    return out


def auto_map(trades_df, unresolved, max_candidates: int = 8, progress=None) -> pd.DataFrame:
    """
    Propose a ticker for each unresolved description and score it against the
    executed trade prices.

    Returns a review table — nothing is written. Rows with a score of 1.0 are
    safe to accept in bulk; anything lower needs a human look.
    """
    rows = []
    total = max(len(unresolved), 1)
    for n, desc in enumerate(unresolved):
        if progress:
            progress(n / total, f'Identifying {desc[:40]}…')
        sub = trades_df[trades_df['Security Name'] == desc]
        best = None
        for sym, name, exch in suggest_symbols(desc, limit=max_candidates):
            check = verify_candidate(sym, sub)
            cand = {
                'Description': desc,
                'Proposed Symbol': sym,
                'Matched Name': name,
                'Exchange': exch,
                'Trades Checked': check['checked'],
                'Price Matches': check['matched'],
                'Confidence': check['score'],
                'Note': check['error'],
            }
            if best is None or cand['Confidence'] > best['Confidence']:
                best = cand
            if check['score'] == 1.0 and check['checked'] > 0:
                break
        if best is None:
            best = {
                'Description': desc, 'Proposed Symbol': '', 'Matched Name': '',
                'Exchange': '', 'Trades Checked': 0, 'Price Matches': 0,
                'Confidence': 0.0, 'Note': 'no candidates found',
            }
        rows.append(best)

    if progress:
        progress(1.0, 'Identification complete')
    df = pd.DataFrame(rows)
    return df.sort_values('Confidence', ascending=False).reset_index(drop=True) if not df.empty else df


def unmapped_summary(trades_df, unresolved) -> pd.DataFrame:
    """Trade count and gross traded value per unresolved description, biggest first."""
    if not unresolved:
        return pd.DataFrame(columns=['Description', 'Trades', 'Gross Traded ($)'])
    sub = trades_df[trades_df['Security Name'].isin(unresolved)].copy()
    sub['_abs'] = pd.to_numeric(sub['Total Value'], errors='coerce').abs()
    out = sub.groupby('Security Name').agg(
        Trades=('Security Name', 'size'),
        **{'Gross Traded ($)': ('_abs', 'sum')},
    ).reset_index().rename(columns={'Security Name': 'Description'})
    return out.sort_values('Gross Traded ($)', ascending=False).reset_index(drop=True)
