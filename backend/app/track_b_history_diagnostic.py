"""Opt-in, bounded diagnostics for the unchanged metadata inventory.

Never serialize exceptions, database values, hashes, paths or report payloads.
Run in a dedicated process: temporary instrumentation is not thread safe.
"""
import argparse
from contextlib import ExitStack
from datetime import datetime
import json
from pathlib import Path
from unittest.mock import patch

from . import track_b_history as h


def diagnose(*, research_db, production_db, decision_at):
    events = []

    def event(stage, reason, **counts):
        # All callers supply fixed identifiers and integer counts only.
        events.append({'stage': stage, 'reason_code': reason, 'counts': counts})

    paths = {'research': Path(research_db), 'production': Path(production_db)}
    try:
        h._utc(decision_at)
        h.validate_paths(paths['research'], paths['production'])
    except Exception:
        event('input', 'INPUT_INVALID')
        return {'events': events}

    before, after = {}, {}
    fingerprint = h.fingerprint
    # Attempt both hashes independently, including after a failed initial hash.
    for name, path in paths.items():
        try:
            before[name] = fingerprint(path)
            event(name + '.fingerprint.before', 'FINGERPRINT_OK')
        except Exception:
            event(name + '.fingerprint.before', 'FINGERPRINT_READ_FAILED')

    current = {'database': 'research', 'phase': 'schema', 'count': 0}
    original_connect, original_read = h.duckdb.connect, h._read
    original_chains = h._chains

    class Connection:
        def __init__(self, db): self.db = db
        def __enter__(self): self.db.__enter__(); return self
        def __exit__(self, *args): return self.db.__exit__(*args)
        def execute(self, sql, *args):
            if sql.startswith('SELECT count(*)') and ' WHERE length(' in sql:
                current['phase'] = 'metadata_cell'
            elif sql.startswith('SELECT count(*)'):
                current['phase'] = 'row_count'
            elif 'SELECT min(try_cast' in sql:
                current['phase'] = 'date_metadata'
            elif sql.startswith('SELECT "'):
                current['phase'] = 'projection'
            else:
                current['phase'] = 'schema'
            self.cursor = self.db.execute(sql, *args)
            return self
        def fetchone(self):
            row = self.cursor.fetchone()
            if current['phase'] == 'row_count': current['count'] = row[0]
            return row
        def fetchall(self): return self.cursor.fetchall()
        def fetchmany(self, n): return self.cursor.fetchmany(n)

    def connect(path, **kwargs):
        current['database'] = next(name for name, p in paths.items() if str(p) == path)
        try:
            return Connection(original_connect(path, **kwargs))
        except Exception:
            event(current['database'] + '.connect', 'METADATA_CONNECTION_FAILED')
            raise

    def read(db, table, decision=None):
        stage = current['database'] + '.' + table
        current.update(phase='schema', count=0)
        try:
            summary, rows = original_read(db, table, decision)
        except h.InventoryError as exc:
            reason = ('MARKET_SQL_RESOURCE_LIMIT' if str(exc) == 'market SQL resource bound exceeded'
                      else 'MARKET_METADATA_CELL_LIMIT' if str(exc) == 'market metadata cell bound exceeded'
                      else 'MARKET_DATE_STATE_LIMIT' if str(exc) == 'market distinct-date state bound exceeded'
                      else 'TABLE_ROW_WORK_LIMIT' if table != 'global_price_observations' and current['count'] > h.MAX_METADATA_ROWS
                      else 'METADATA_COUNT_MISMATCH')
            event(stage, reason, row_count=current['count'])
            raise
        except Exception:
            event(stage + '.' + current['phase'], 'METADATA_QUERY_OR_DECODE_FAILED')
            raise
        missing = len(summary.get('missing_adapter_columns', ()))
        reason = ('SCHEMA_INCOMPATIBLE' if summary['state'] == 'incompatible_evidence'
                  else 'SCHEMA_PARTIAL' if missing else 'METADATA_READ_OK')
        counts = {'adapter_column_count': len(summary['columns']), 'missing_column_count': missing}
        # Views are not evaluated: unknown row counts must not become zero.
        if summary['row_count'] is not None: counts['row_count'] = summary['row_count']
        event(stage, reason, **counts)
        return summary, rows

    def chains(periods, length, annual=False):
        try:
            return original_chains(periods, length, annual)
        except h.InventoryError:
            event('period_chain', 'PERIOD_CHAIN_WORK_LIMIT',
                  period_count=len(periods), chain_length=length)
            raise
        except Exception:
            event('period_chain', 'METADATA_PARSING_FAILED', period_count=len(periods))
            raise

    def processing(fn, stage):
        calls = 0
        def wrapped(*args, **kwargs):
            nonlocal calls
            name = ('research', 'production')[calls // 2 if stage == 'accounting' else calls]
            calls += 1
            try:
                return fn(*args, **kwargs)
            except h.InventoryError:
                raise
            except Exception:
                event(name + '.' + stage, 'METADATA_PARSING_FAILED')
                raise
        return wrapped

    def cached_fingerprint(path):
        name = next(name for name, p in paths.items() if p == path)
        # Inventory performs before then after; outer finally owns fresh checks.
        return before[name]

    try:
        if len(before) == 2:
            with ExitStack() as stack:
                for attr, replacement in (
                    ('fingerprint', cached_fingerprint), ('_read', read), ('_chains', chains),
                    ('_families', processing(h._families, 'accounting')),
                    ('_identity', processing(h._identity, 'identity')),
                    ('_market', processing(h._market, 'market')),
                ):
                    stack.enter_context(patch.object(h, attr, replacement))
                stack.enter_context(patch.object(h.duckdb, 'connect', connect))
                try:
                    h.inventory(research_db=paths['research'], production_db=paths['production'],
                                decision_at=decision_at)
                    event('inventory', 'INVENTORY_COMPLETED')
                except Exception as exc:
                    # The public code is an allowlisted class property, never text from an exception.
                    reason = ('INVENTORY_SQL_RESOURCE_LIMIT' if isinstance(exc, h.InventoryError) and str(exc) == 'inventory SQL resource bound exceeded'
                              else 'INVENTORY_BOUND_FAILED' if isinstance(exc, h.InventoryError)
                              else 'REPORT_CONTRACT_FAILED' if isinstance(exc, h.InvestmentResearchError)
                              else 'INVENTORY_INTERNAL_FAILED')
                    event('inventory', reason)
    finally:
        for name, path in paths.items():
            try:
                after[name] = fingerprint(path)
                reason = ('FINGERPRINT_BASELINE_UNAVAILABLE' if name not in before
                          else 'FINGERPRINT_UNCHANGED' if before[name] == after[name]
                          else 'FINGERPRINT_CHANGED')
                event(name + '.fingerprint.after', reason)
            except Exception:
                event(name + '.fingerprint.after', 'FINGERPRINT_READ_FAILED')
    # At most 44 table events, four hashes, three processing/failure events.
    assert len(events) <= 52
    return {'events': events}


def main():
    parser = argparse.ArgumentParser(description='Read-only safe Track B diagnostics')
    parser.add_argument('--research-db', required=True, type=Path)
    parser.add_argument('--production-db', required=True, type=Path)
    parser.add_argument('--decision-at', required=True)
    args = parser.parse_args()
    try:
        args.decision_at = datetime.fromisoformat(args.decision_at)
        result = diagnose(**vars(args))
    except Exception:
        result = {'events': [{'stage': 'diagnostic', 'reason_code': 'DIAGNOSTIC_FAILED', 'counts': {}}]}
    print(json.dumps(result, sort_keys=True, separators=(',', ':')))
    failed = any(e['reason_code'].endswith(('FAILED', 'LIMIT', 'MISMATCH', 'INVALID'))
                 or e['reason_code'] == 'FINGERPRINT_CHANGED' for e in result['events'])
    raise SystemExit(int(failed))


if __name__ == '__main__': main()
