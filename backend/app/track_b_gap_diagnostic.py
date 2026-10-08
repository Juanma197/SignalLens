"""Opt-in internal stage diagnostics. Never emits exception text or report data."""
import argparse
from datetime import datetime
import json

from . import track_b_gaps as g
from .investment_research import public_error_code

MAX_EVENTS = 64
MAX_BYTES = 16384
BASE_STAGES = frozenset(('input', 'internal', 'fingerprints.baseline', 'fingerprints.verify',
    'roster.schema', 'roster.identity', 'retrievals.completion', 'retrievals.gaps',
    'specification', 'report.build', 'report.contract'))
STAGES = BASE_STAGES | frozenset(
    [f'{db}.{phase}' for db in ('research','production') for phase in
        ('connect','fingerprint.before','fingerprint.after','coverage','concepts')]
    + [f'{db}.{table}.{phase}' for db in ('research','production') for table in g.SOURCES
        for phase in ('schema','columns','row_count','cell_count','projection')]
    + [f'{db}.{table}.cell_count.{column}' for db in ('research','production')
        for table,columns in g.SOURCES.items() for column in columns]
    + [f'{db}.{layer}.accounting' for db in ('research','production') for layer in ('raw_sec','canonical')]
    + [f'{db}.{layer}.{field}.periods' for db in ('research','production')
        for layer in ('raw_sec','canonical') for field in g.FIELDS]
    + [f'{db}.{layer}.{family}.chain' for db in ('research','production')
        for layer in ('raw_sec','canonical') for family in ('value','financial_strength')])
REASONS = frozenset(('METADATA_TABLE_ABSENT','METADATA_SCHEMA_INCOMPATIBLE','METADATA_READ_OK',
    'METADATA_ROW_LIMIT','METADATA_CELL_LIMIT','METADATA_CELL_COLUMN_LIMIT',
    'METADATA_CELL_DETAILS_UNAVAILABLE','METADATA_COUNT_MISMATCH','ROSTER_LIMIT',
    'ROSTER_SCHEMA_UNSUPPORTED','ROSTER_RECONCILED','PERIOD_STATE_LIMIT','CHAIN_WORK_LIMIT',
    'FINGERPRINT_BEFORE_OK','FINGERPRINT_BEFORE_IO_FAILED','FINGERPRINT_BASELINE_UNAVAILABLE',
    'FINGERPRINT_AFTER_OK','FINGERPRINT_AFTER_IO_FAILED','FINGERPRINT_AFTER_UNAVAILABLE',
    'FINGERPRINT_CHANGED','SQL_MEMORY_LIMIT','REPORT_BYTE_LIMIT','DIAGNOSTIC_COMPLETED',
    'DIAGNOSTIC_FAILED','DIAGNOSTIC_INTERNAL_FAILED','EVENT_BOUND_EXCEEDED'))
COUNT_KEYS = frozenset(('projected_column_count','row_count','row_limit','oversized_row_count',
    'cell_character_limit','maximum_cell_characters','expected_row_count','decoded_row_count','classification_row_count',
    'roster_count','roster_limit','period_count','period_limit','metadata_row_count','matched_count',
    'chain_length','chain_work_limit','baseline_count','required_baseline_count',
    'missing_required_column_count','after_count','after_failure_count','changed_database_count',
    'byte_limit','compact_utf8_bytes'))
PUBLIC_CODES = frozenset(('TRACK_B_GAP_DIAGNOSTIC_FAILED','TRACK_B_HISTORY_INVENTORY_FAILED',
    'INVESTMENT_RESEARCH_NOT_READY','INVESTMENT_RESEARCH_INTERNAL_ERROR'))


class Trace:
    def __init__(self):
        self.stage = 'input'
        self.counts = {}
        self.events = []
        self.overflow = False

    def mark(self, stage, counts):
        self.stage = stage if stage in STAGES else 'internal'
        self.counts = self.safe_counts(counts)

    @staticmethod
    def safe_counts(counts):
        return {key:value for key,value in counts.items() if key in COUNT_KEYS
                and type(value) is int and 0 <= value <= 2**63-1}

    def record(self, reason, counts):
        if len(self.events) >= MAX_EVENTS-1:
            self.overflow = True
            return
        self.events.append({'stage':self.stage,
            'reason_code':reason if reason in REASONS else 'DIAGNOSTIC_INTERNAL_FAILED',
            'counts':self.counts | self.safe_counts(counts)})


def diagnose(**kwargs):
    trace = Trace()
    token = g._TRACE.set(trace)
    completed = False
    code = None
    try:
        # Discard the full report. No report values, hashes or identities leave
        # this diagnostic surface, even when the underlying command succeeds.
        g.diagnose(**kwargs)
        completed = True
    except Exception as exc:
        candidate = public_error_code(exc)
        code = candidate if candidate in PUBLIC_CODES else 'INVESTMENT_RESEARCH_INTERNAL_ERROR'
        if trace.stage in ('research.fingerprint.after', 'production.fingerprint.after'):
            # Successful cleanup is not the location of the earlier exception.
            trace.mark('internal', {})
        trace.record('DIAGNOSTIC_FAILED', {})
    finally:
        g._TRACE.reset(token)
    if trace.overflow:
        trace.events.append({'stage':'internal','reason_code':'EVENT_BOUND_EXCEEDED','counts':{}})
        completed = False
    result = {'command':'track-b-gap-internal-diagnostic','read_only':True,'metadata_only':True,
        'diagnostic_completed':completed,'operator_verification_successful':False,
        'public_error_code':code,'events':trace.events,
        'bounds':{'maximum_events':MAX_EVENTS,'maximum_utf8_bytes':MAX_BYTES}}
    encoded = json.dumps(result,sort_keys=True,separators=(',',':')).encode('utf-8')
    if len(encoded)>MAX_BYTES:
        # Diagnostic output budget never relaxes a computation/report budget.
        result['diagnostic_completed'] = False
        result['events'] = [{'stage':'internal','reason_code':'EVENT_BOUND_EXCEEDED','counts':{}}]
    return result


def main():
    parser = argparse.ArgumentParser(description='Bounded internal Track B gap diagnostics')
    parser.add_argument('--research-db',required=True)
    parser.add_argument('--production-db',required=True)
    parser.add_argument('--decision-at',required=True,type=datetime.fromisoformat)
    args = parser.parse_args()
    result = diagnose(**vars(args))
    print(json.dumps(result,sort_keys=True,separators=(',',':')))
    raise SystemExit(0 if result['diagnostic_completed'] else 1)


if __name__ == '__main__':
    main()
