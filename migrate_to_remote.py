"""
Copy the local ledger and holdings into the configured remote database.

Idempotent. Transactions go through :func:`ledger.merge`, whose unique dedup key
makes a repeat run insert nothing, so a partial migration is simply re-run.
Holdings are snapshot semantics and are replaced wholesale.

    .venv/bin/python scripts_migrate_to_remote.py --dry-run
    .venv/bin/python scripts_migrate_to_remote.py

Reads SUPABASE_DB_URL from .env. Nothing is deleted locally: the SQLite file
stays as a mirror, so an outage degrades to read-only rather than to nothing.
"""

import argparse
import sys

from dotenv import load_dotenv

load_dotenv('.env')

from portfolio.storage import db, holdings_store, ledger  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dry-run', action='store_true',
                        help='report what would move without writing')
    args = parser.parse_args()

    if not db.is_remote():
        print('SUPABASE_DB_URL is not set — nothing to migrate to.')
        return 1

    # Read everything from the local file first, with the remote forced off.
    real_dsn = db.dsn
    db.dsn = lambda: None
    db.reset_cache()
    ledger._schema_ready.clear()
    local_rows = ledger.load(seed=False)
    local_holdings = holdings_store.load()
    local_coverage = ledger.coverage()
    db.dsn = real_dsn
    db.reset_cache()
    ledger._schema_ready.clear()

    print(f'local ledger   : {local_coverage["rows"]:,} rows, '
          f'{local_coverage["start"]} → {local_coverage["end"]}, '
          f'{local_coverage["by_source"]}')
    print(f'local holdings : '
          f'{0 if not local_holdings else len(local_holdings["holdings"])} position(s)')

    if args.dry_run:
        print('\n--dry-run: nothing written.')
        return 0

    print('\nwriting to remote…')
    before = ledger.coverage()['rows']
    # _insert rather than merge: merge seeds from the local JSON stores when the
    # target is empty, which is right for a normal refresh and wrong here — the
    # rows being copied are already the finished article. Dedup still applies.
    ledger._insert(local_rows, source=ledger.SOURCE_ETRADE)
    remote = ledger.coverage()

    if local_holdings:
        holdings_store.save(
            local_holdings['holdings'], local_holdings['fetched_at'],
            local_holdings['reported_total'], local_holdings['account_balances'],
        )

    print(f'remote ledger  : {remote["rows"]:,} rows '
          f'({remote["rows"] - before:+,} this run), '
          f'{remote["start"]} → {remote["end"]}, {remote["by_source"]}')

    ok = remote['rows'] == local_coverage['rows']
    print('\nrow counts match:', 'yes' if ok else
          f'NO — local {local_coverage["rows"]}, remote {remote["rows"]}')
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
