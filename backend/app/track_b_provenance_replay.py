"""PROPOSED Step 1/2 only: metadata audit and one retained-payload replay.

Never an evidence producer. No network, financial value projection, or DB writes.
Recovery comparisons are diagnostic counts, not persisted recovered fields.
"""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import duckdb
from . import track_b_construction_feasibility as feasibility
from . import track_b_gaps as gaps
from . import track_b_history as history
from .sec_ingestion import validate_paths
from .sec_liquidity_contract import (OPERATION_TYPE, OPERATION_CONTRACT_VERSION,
                                     CONCEPT_CONTRACT_HASH, PARSER_VERSION)

VERSION = 'track-b-provenance-replay-proposed-0.1.2'
MAX_ROWS = 500_000
MAX_MANIFEST = 10_000
MAX_PAYLOAD = 5_000_000  # decimal MB, never MiB
MAX_TOTAL_PAYLOAD = 10_000_000
MAX_OBSERVATIONS = 24
MAX_ACCESSIONS = 3
MAX_REPORT = 131_072
MAX_CELL = 1024
MAX_CELL_SAMPLES = 8
OPAQUE_REFERENCES = {'plan_id': 'plan_id_sha256', 'ingestion_plan_id': 'ingestion_plan_id_sha256'}
RAW_TABLE = 'sec_liquidity_raw_provenance'
IDENTITY = ('operation_type', 'operation_contract_version', 'concept_contract_hash',
            'lineage_id', 'run_id', 'plan_id', 'security_id', 'cik')
MANIFEST = ('evidence_key',) + IDENTITY + ('endpoint_class', 'retrieved_at',
    'response_sha256', 'byte_count', 'content_type', 'parser_version')
FACT_FIELDS = ('fact_key', 'security_id', 'cik', 'taxonomy', 'concept', 'unit', 'currency', 'scale',
    'period_start', 'period_end', 'fiscal_year', 'fiscal_period', 'frame', 'form',
    'accession_number', 'filed_date', 'public_at', 'retrieved_at', 'is_amendment',
    'is_revision', 'source_endpoint', 'parser_contract_version', 'operation_type',
    'operation_contract_version', 'concept_contract_hash', 'ingestion_run_id', 'ingestion_plan_id')
FACT_REQUIRED = {'fact_key', 'security_id', 'cik', 'taxonomy', 'concept', 'unit',
                 'period_start', 'period_end', 'accession_number', 'public_at', 'retrieved_at'}
REPORTED = ('fiscal_year', 'fiscal_period', 'frame', 'form', 'filed_date', 'is_amendment', 'is_revision')
RECOGNIZED = ('security_id', 'cik', 'taxonomy', 'concept', 'unit', 'currency', 'scale', 'period_start', 'period_end',
              'accession_number', 'public_at', 'retrieved_at', 'fact_key')
RECOVERY_MAP = {'fiscal_year': 'fy', 'fiscal_period': 'fp', 'frame': 'frame'}
FILING_MAP = {'form': 'form', 'filed_date': 'filingDate', 'public_at': 'acceptanceDateTime'}
ORIGINAL_FILINGS = {
    'fiscal_calendar': 'statement periods/calendar note; FY/FP/frame are labels only',
    'context': 'contextRef entity/period/segment/scenario/dimensions and taxonomy defaults',
    'accounting_basis': 'statement and restatement/discontinued-operation notes',
    'precision': 'original XBRL decimals/precision and inline transformations; JSON digits are not decimals',
    'revision_relationship': 'specific amendment/restatement scope and fact-level replacement/recast evidence',
    'debt_overlap_and_completeness': 'exclusive balance portions and exhaustive debt-note perimeter',
    'unrestricted_cash': 'restriction/netting/pledge notes; exact direct cash does not establish this contract',
}
SPEC_DECISIONS = {
    'calendar_minimum': 'justify period evidence for a quarter pair separately from four-quarter TTM',
    'context_equivalence': 'define semantic compatibility; context IDs alone do not prove equivalence',
    'revision_graph': 'define fact-level replacement and pairwise compatible recast evidence',
    'precision_policy': 'distinguish rounding, JSON representation and DOUBLE storage',
    'debt_perimeter': 'define included obligations, exclusivity, exhaustive membership and evidenced zero',
    'availability': 'retain raw rules; replay now is not historical canonical evidence; never backdate new evidence',
}
BOUNDS = {'securities': 1, 'payload_pairs': 1, 'accessions': MAX_ACCESSIONS,
    'replayed_observations': MAX_OBSERVATIONS, 'payload_bytes_each': MAX_PAYLOAD,
    'payload_bytes_total': MAX_TOTAL_PAYLOAD, 'metadata_rows_total': MAX_ROWS,
    'manifest_rows': MAX_MANIFEST, 'metadata_cell_characters': MAX_CELL,
    'submission_rows': MAX_MANIFEST, 'submission_matches_per_accession': MAX_OBSERVATIONS,
    'report_bytes': MAX_REPORT, 'sql_memory': '128MB', 'sql_threads': 1, 'disk_spill': False}


class ReplayError(RuntimeError):
    def __init__(self, code, diagnostic=None):
        super().__init__(code)
        self.diagnostic = diagnostic


def require(ok, code):
    if not ok:
        raise ReplayError(code)


def encode(report):
    return json.dumps(report, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode('utf-8')


def fingerprint(path):
    digest = hashlib.sha256(); size = 0
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block); size += len(block)
    return {'sha256': digest.hexdigest(), 'bytes': size}


def schema(db, table):
    found = db.execute("SELECT table_type FROM information_schema.tables WHERE table_catalog=current_database() AND table_schema='main' AND table_name=?", [table]).fetchone()
    if not found:
        return None
    require(found[0] == 'BASE TABLE', 'UNSUPPORTED_TABLE')
    return {r[0] for r in db.execute("SELECT column_name FROM information_schema.columns WHERE table_catalog=current_database() AND table_schema='main' AND table_name=?", [table]).fetchall()}


def projection(column):
    if column in OPAQUE_REFERENCES:
        # Exact PR #95 expression: full stored UTF-8 SHA-256, tagged reference,
        # NULL and empty preserved. No prefix, decoding, or raw token projection.
        expression = gaps._projection_expression('sec_liquidity_runs','plan_id')
        expression = expression.replace('plan_id', f'"{column}"')
        alias = OPAQUE_REFERENCES[column]
        return expression + f' AS "{alias}"', alias
    return f'"{column}"', column


def metadata_filter(table, row_key):
    if row_key is None: return '', []
    if table == 'sec_liquidity_runs':
        require(isinstance(row_key,str), 'UNSUPPORTED_ROW_FILTER')
        return ' WHERE run_id=?', [row_key]
    if table == 'sec_liquidity_checkpoints':
        require(isinstance(row_key,tuple) and len(row_key)==2, 'UNSUPPORTED_ROW_FILTER')
        return ' WHERE security_id=? AND cik=?', list(row_key)
    raise ReplayError('UNSUPPORTED_ROW_FILTER')


def cell_diagnostics(db, table, selected, stage, *, original_projection=False, row_key=None):
    """Only fixed field names, aggregate counts and bounded exact lengths escape SQL."""
    findings = []
    for column in selected:
        if column in OPAQUE_REFERENCES and not original_projection: continue
        predicate = f'length(CAST("{column}" AS VARCHAR))>{MAX_CELL}'
        where, parameters = metadata_filter(table,row_key)
        if where: predicate = '('+predicate+') AND '+where[7:]
        count, low, high = db.execute(f'SELECT count(*),min(length(CAST("{column}" AS VARCHAR))),max(length(CAST("{column}" AS VARCHAR))) FROM "{table}" WHERE {predicate}',parameters).fetchone()
        if not count: continue
        samples = db.execute(f'SELECT length(CAST("{column}" AS VARCHAR)),octet_length(encode(CAST("{column}" AS VARCHAR))) FROM "{table}" WHERE {predicate} ORDER BY 1 DESC,2 DESC LIMIT {MAX_CELL_SAMPLES}',parameters).fetchall()
        findings.append({'read_stage': stage, 'table': table, 'source_column': column,
            'projected_column': column if original_projection else OPAQUE_REFERENCES.get(column,column),
            'proposed_reference_column': OPAQUE_REFERENCES.get(column), 'rejected_cell_count': count,
            'min_characters': low, 'max_characters': high,
            'cell_lengths': [{'characters':chars,'utf8_bytes':size} for chars,size in samples],
            'sample_truncated': count>MAX_CELL_SAMPLES,
            'field_class': 'opaque_capability_reference' if column in OPAQUE_REFERENCES else 'descriptive_metadata',
            'rejected_values_returned': False})
    return {'read_stage': stage, 'table': table, 'metadata_cell_limit': MAX_CELL,
        'original_projection': original_projection, 'offending_columns': findings,
        'rejected_values_returned': False, 'payloads_returned': False}


def metadata_rows(db, table, fields, required, remaining, *, stage='metadata_read', row_key=None):
    require('payload_json' not in fields, 'PAYLOAD_IN_METADATA_PROJECTION')
    columns = schema(db, table)
    if columns is None:
        return {'state': 'absent_evidence', 'rows': 0, 'columns': []}, []
    where, parameters = metadata_filter(table,row_key)
    count = db.execute(f'SELECT count(*) FROM "{table}"{where}',parameters).fetchone()[0]
    require(count <= remaining, 'METADATA_ROW_LIMIT')
    selected = [c for c in fields if c in columns]
    if not selected or not required <= columns:
        return {'state': 'unsupported_schema', 'rows': count, 'columns': selected,
                'missing_required': sorted(required - columns)}, []
    descriptive = [c for c in selected if c not in OPAQUE_REFERENCES]
    checks = ' OR '.join(f'length(CAST("{c}" AS VARCHAR))>{MAX_CELL}' for c in descriptive)
    check_where = ('('+checks+') AND '+where[7:]) if where else checks
    if checks and db.execute(f'SELECT count(*) FROM "{table}" WHERE {check_where}',parameters).fetchone()[0]:
        diagnostic = cell_diagnostics(db,table,selected,stage,row_key=row_key)
        raise ReplayError('METADATA_CELL_LIMIT', diagnostic)
    projected = [projection(c) for c in selected]
    rows = [dict(zip([alias for _,alias in projected],r)) for r in db.execute(f'SELECT {",".join(sql for sql,_ in projected)} FROM "{table}"{where}',parameters).fetchall()]
    return {'state': 'supported', 'rows': count, 'columns': selected,
            'opaque_identity_references': {c:OPAQUE_REFERENCES[c] for c in selected if c in OPAQUE_REFERENCES}}, rows


def present(value):
    return value is not None and str(value) != ''


def norm(value):
    if value is None: return None
    if isinstance(value, datetime): return value.isoformat()
    return str(value)


def cik(value):
    text = str(value)
    require(text.isascii() and text.isdigit() and 0 < len(text) <= 10 and int(text)>0, 'CIK_INVALID')
    return text.zfill(10)


def semantic(row):
    return tuple(norm(row.get(k)) for k in ('taxonomy', 'concept', 'unit', 'period_start', 'period_end', 'accession_number'))


def visible(rows, population, decision):
    result = []; counts = Counter({k:0 for k in ('stored_relevant_rows','visibility_unproven_rows','post_decision_rows','period_end_unproven_rows','future_period_rows','visible_relevant_rows')})
    for row in rows:
        if row.get('security_id') not in population or row.get('concept') not in feasibility.CONCEPTS: continue
        counts['stored_relevant_rows'] += 1
        available, reason = feasibility.input_availability(row, 'raw_sec')
        if reason: counts['visibility_unproven_rows'] += 1; continue
        if available > decision: counts['post_decision_rows'] += 1; continue
        end = history._date(row.get('period_end'))
        if end is None: counts['period_end_unproven_rows'] += 1; continue
        if end > decision.date(): counts['future_period_rows'] += 1; continue
        counts['visible_relevant_rows'] += 1; result.append(row)
    return dict(counts), result


def mapping_audit(rows, columns, decision):
    # Reuse unchanged validator functions, with the same proof visibility rule.
    accepted = Counter({k: 0 for k in ('fiscal', 'context', 'revision', 'precision')})
    for source in rows:
        row = dict(source, _start=history._date(source.get('period_start')), _end=history._date(source.get('period_end')))
        for kind, validator in (('fiscal', feasibility.calendar), ('context', feasibility.context),
                                ('revision', feasibility.revision), ('precision', feasibility.precision)):
            timestamp = feasibility.stamp(row.get(kind + '_metadata_available_at'))
            if timestamp is not None and timestamp <= decision and validator(row): accepted[kind] += 1
    def field_counts(fields):
        return {k: {'column_retained': k in columns, 'present_rows': sum(present(r.get(k)) for r in rows),
                    'missing_rows': sum(not present(r.get(k)) for r in rows)} for k in fields}
    return {'metadata_already_recognized': {'source_fields': field_counts(RECOGNIZED),
            'accepted_proof_rows': dict(accepted), 'accepted_does_not_mean_approved': True},
        'stored_metadata_needing_interpretation': field_counts(REPORTED),
        'stored_proof_field_inventory': field_counts(k for k in feasibility.OPTIONAL if k not in REPORTED),
        'unaccepted_proof_rows': {k:len(rows)-accepted[k] for k in accepted},
        'visible_rows_by_exact_concept': {k:sum(row.get('concept')==k for row in rows) for k in feasibility.CONCEPTS},
        'prerequisites_requiring_original_filings': ORIGINAL_FILINGS,
        'prerequisites_requiring_specification_decisions': SPEC_DECISIONS,
        'flags_are_not_revision_evidence': True, 'categories_are_nonexclusive': True}


def operation_ok(row):
    return (row.get('operation_type'), row.get('operation_contract_version'), row.get('concept_contract_hash')) == (OPERATION_TYPE, OPERATION_CONTRACT_VERSION, CONCEPT_CONTRACT_HASH)


def pair_identity(row):
    return tuple(norm(row.get(k)) for k in (OPAQUE_REFERENCES.get(c,c) for c in IDENTITY))


def fact_matches_pair(row, pair):
    return (operation_ok(row) and all(norm(row.get(k)) == norm(pair.get(k)) for k in ('security_id', 'cik'))
        and norm(row.get('ingestion_run_id')) == norm(pair.get('run_id'))
        and norm(row.get('ingestion_plan_id_sha256')) == norm(pair.get('plan_id_sha256'))
        and row.get('parser_contract_version') == PARSER_VERSION)


def pattern_rows(rows):
    """Qualification uses source metadata only; never financial values/count rank."""
    groups = defaultdict(list); repeated = defaultdict(set)
    for r in rows:
        if not present(r.get('accession_number')) or r.get('taxonomy') != 'us-gaap': continue
        if r.get('concept') in (feasibility.OCF, feasibility.CAPEX) and r.get('period_start') is not None:
            groups[(r['concept'], norm(r['unit']), norm(r['period_start']))].append(r)
        repeated[semantic(r)[:-1]].add(r['accession_number'])
    cumulative = any(len({norm(r['period_end']) for r in group}) >= 2 for group in groups.values())
    redisclosure = any(len(acc) >= 2 for acc in repeated.values())
    missing_label = any(any(not present(r.get(k)) for k in ('fiscal_year', 'fiscal_period', 'frame')) for r in rows)
    return {'same_start_distinct_end_flow_shape': cumulative,
            'repeated_period_across_accessions': redisclosure, 'missing_reported_label': missing_label}


def select_pilot(manifest, rows, decision):
    buckets = defaultdict(lambda: defaultdict(list))
    skipped = Counter()
    for item in manifest:
        time = feasibility.stamp(item.get('retrieved_at'))
        if time is None or time > decision: skipped['not_visible_or_unproven'] += 1; continue
        if not operation_ok(item) or item.get('parser_version') != PARSER_VERSION:
            skipped['unsupported_operation_or_parser'] += 1; continue
        if not all(present(item.get(k)) for k in (OPAQUE_REFERENCES.get(c,c) for c in IDENTITY)): skipped['incomplete_identity'] += 1; continue
        size = item.get('byte_count')
        if type(size) is not int or not 0 < size <= MAX_PAYLOAD:
            skipped['declared_payload_size_outside_bound'] += 1; continue
        if item.get('endpoint_class') in ('companyfacts', 'submissions'):
            buckets[pair_identity(item)][item['endpoint_class']].append(item)
    # Never build a cross product. Exact pair identity must have one row per endpoint.
    rows_by_operation = defaultdict(list)
    for row in rows:
        if operation_ok(row) and row.get('parser_contract_version') == PARSER_VERSION:
            key = tuple(norm(row.get(k)) for k in ('operation_type','operation_contract_version','concept_contract_hash')) + (norm(row.get('ingestion_run_id')),norm(row.get('ingestion_plan_id_sha256')),norm(row.get('security_id')),norm(row.get('cik')))
            rows_by_operation[key].append(row)
    eligible = []
    seen_operation_keys = set()
    for key in buckets:
        index_key = key[:3] + (key[4],key[5],key[6],key[7])
        require(index_key not in seen_operation_keys, 'AMBIGUOUS_RUN_LINEAGE_IDENTITY')
        seen_operation_keys.add(index_key)
    for key, endpoints in buckets.items():
        if set(endpoints) != {'companyfacts', 'submissions'}: skipped['missing_pair_endpoint'] += 1; continue
        if any(len(v) != 1 for v in endpoints.values()): skipped['ambiguous_retained_pair'] += 1; continue
        pair = [endpoints[k][0] for k in ('companyfacts', 'submissions')]
        if sum(p['byte_count'] for p in pair) > MAX_TOTAL_PAYLOAD: skipped['total_payload_size'] += 1; continue
        index_key = key[:3] + (key[4],key[5],key[6],key[7])
        matching = rows_by_operation[index_key]
        patterns = pattern_rows(matching)
        if matching and any(patterns.values()): eligible.append((key, pair, matching, patterns))
        else: skipped['no_qualifying_metadata_pattern'] += 1
    if not eligible: return None, {'state': 'no_qualifying_pair', 'skipped': dict(skipped), 'qualifying_pair_count': 0}
    key, pair, matching, patterns = min(eligible, key=lambda p: p[0] + tuple(r['evidence_key'] for r in p[1]))
    # First three accessions, then first 24 rows, ordered only by source metadata.
    accessions = sorted({r['accession_number'] for r in matching})[:MAX_ACCESSIONS]
    groups = defaultdict(list)
    for row in matching:
        if row['accession_number'] in accessions: groups[semantic(row)].append(row)
    selected = []
    for key in sorted(groups, key=lambda k: tuple(x or '' for x in k)):
        group = sorted(groups[key], key=lambda r: r['fact_key'])
        if len(selected) + len(group) > MAX_OBSERVATIONS:
            require(bool(selected), 'STORED_MATCH_MULTIPLICITY_LIMIT')
            break
        selected.extend(group)
    return (pair, selected), {'state': 'selected', 'security_id': pair[0]['security_id'], 'cik': pair[0]['cik'],
        'payload_evidence_keys': [r['evidence_key'] for r in pair], 'qualifying_pair_count': len(eligible),
        'patterns': patterns, 'accessions': sorted({r['accession_number'] for r in selected}),
        'selected_stored_rows': len(selected), 'excluded_stored_rows': len(matching) - len(selected),
        'selection_order': 'lexicographic_full_operation_security_cik_identity_with_full_plan_sha256_then_accession_semantic_metadata_fact_key',
        'skipped': dict(skipped)}


def no_duplicate_json_keys(pairs):
    out = {}
    for key, value in pairs:
        require(key not in out, 'DUPLICATE_JSON_MEMBER')
        out[key] = value
    return out


def verify_payload(item, text):
    require(isinstance(text, str), 'PAYLOAD_NOT_TEXT')
    raw = text.encode('utf-8', errors='strict')
    require(len(raw) <= MAX_PAYLOAD, 'PAYLOAD_BYTE_LIMIT')
    require(len(raw) == item['byte_count'], 'PAYLOAD_BYTE_COUNT_MISMATCH')
    digest = hashlib.sha256(raw).hexdigest()
    require(digest == item['response_sha256'], 'PAYLOAD_HASH_MISMATCH')
    require('json' in str(item['content_type']).lower(), 'PAYLOAD_CONTENT_TYPE')
    expected = hashlib.sha256(f"{item['lineage_id']}|{item['cik']}|{item['endpoint_class']}|{digest}".encode()).hexdigest()
    require(item['evidence_key'] == expected, 'PAYLOAD_EVIDENCE_KEY_MISMATCH')
    # Values are parsed as inert JSON tokens; no numeric financial conversion.
    payload = json.loads(text, parse_int=str, parse_float=str,
        parse_constant=lambda _: (_ for _ in ()).throw(ReplayError('NONFINITE_JSON')),
        object_pairs_hook=no_duplicate_json_keys)
    require(isinstance(payload, dict) and cik(payload.get('cik')) == cik(item['cik']), 'PAYLOAD_CIK_MISMATCH')
    return payload, {'evidence_key': item['evidence_key'], 'original_utf8_sha256': digest,
                     'verified_byte_count': len(raw), 'hash_and_size_verified': True}


def verify_lineage(db, pair, remaining=MAX_ROWS, decision_at=None):
    first = pair[0]
    require(pair_identity(first) == pair_identity(pair[1]) and operation_ok(first), 'PAIR_LINEAGE_MISMATCH')
    require(first['retrieved_at'] == pair[1]['retrieved_at'], 'PAIR_RETRIEVAL_MISMATCH')
    issuers = schema(db, 'sec_issuers')
    require(issuers is not None and {'security_id', 'cik'} <= issuers, 'ISSUER_LINEAGE_UNPROVEN')
    mappings = db.execute('SELECT cik FROM sec_issuers WHERE security_id=?', [first['security_id']]).fetchall()
    require(len(mappings) == 1 and cik(mappings[0][0]) == cik(first['cik']), 'ISSUER_LINEAGE_UNPROVEN')
    fields = ('run_id', 'operation_type', 'operation_contract_version', 'concept_contract_hash', 'lineage_id', 'plan_id', 'decision_at')
    columns = schema(db, 'sec_liquidity_runs')
    require(columns is not None and set(fields) <= columns, 'RUN_LINEAGE_UNPROVEN')
    run_source, run_rows = metadata_rows(db, 'sec_liquidity_runs', fields, set(fields), remaining, stage='research.run_lineage', row_key=first['run_id'])
    require(run_source['state']=='supported', 'RUN_LINEAGE_UNPROVEN')
    runs = [row for row in run_rows if norm(row.get('run_id'))==norm(first['run_id'])]
    require(len(runs) == 1, 'RUN_LINEAGE_UNPROVEN')
    run = runs[0]
    require(all(norm(run[OPAQUE_REFERENCES.get(k,k)]) == norm(first[OPAQUE_REFERENCES.get(k,k)]) for k in fields[:-1]), 'RUN_LINEAGE_MISMATCH')
    decision = feasibility.stamp(run['decision_at'])
    require(decision is not None, 'RUN_DECISION_UNPROVEN')
    lineage = hashlib.sha256(f'{OPERATION_TYPE}|{OPERATION_CONTRACT_VERSION}|{CONCEPT_CONTRACT_HASH}|{decision.isoformat()}'.encode()).hexdigest()
    require(lineage == first['lineage_id'], 'RUN_LINEAGE_HASH_MISMATCH')
    # Same full tagged plan reference in facts, runs, checkpoints and provenance.
    checkpoint_fields = IDENTITY + ('status','transaction_succeeded','updated_at')
    checkpoint_source, checkpoints = metadata_rows(db, 'sec_liquidity_checkpoints',
        checkpoint_fields, set(checkpoint_fields), remaining-run_source['rows'],
        stage='research.checkpoint_lineage', row_key=(first['security_id'],first['cik']))
    require(checkpoint_source['state']=='supported' and checkpoints, 'CHECKPOINT_LINEAGE_UNPROVEN')
    matches = [row for row in checkpoints if pair_identity(row)==pair_identity(first)]
    require(len(matches)==1, 'CHECKPOINT_LINEAGE_MISMATCH')
    checkpoint = matches[0]
    require(checkpoint.get('status')=='completed' and checkpoint.get('transaction_succeeded') is True,
            'CHECKPOINT_COMPLETION_UNPROVEN')
    boundary = feasibility.stamp(decision_at) if decision_at is not None else decision
    checkpoint_time = feasibility.stamp(checkpoint.get('updated_at'))
    require(boundary is not None and checkpoint_time is not None and checkpoint_time<=boundary,
            'CHECKPOINT_METADATA_NOT_VISIBLE')
    return {'pair_identity_verified': True, 'issuer_mapping_verified': True,
            'run_identity_and_decision_lineage_verified': True,
            'source_fact_operation_links_verified': True,
            'checkpoint_full_identity_verified': True, 'checkpoint_completion_visible': True,
            'plan_reference_representation': 'sha256_full_stored_utf8_preserving_null_and_empty_PR95',
            'selected_lineage_metadata_rows': run_source['rows']+checkpoint_source['rows']}


def retained_payload(db, item):
    # Check actual encoded size in SQL before allowing full text out of DuckDB.
    sizes = db.execute(f'SELECT octet_length(encode(payload_json)) FROM {RAW_TABLE} WHERE evidence_key=?', [item['evidence_key']]).fetchall()
    require(len(sizes) == 1 and sizes[0][0] is not None and 0 < sizes[0][0] <= MAX_PAYLOAD, 'ACTUAL_PAYLOAD_SIZE_OR_MULTIPLICITY')
    require(sizes[0][0] == item['byte_count'], 'PAYLOAD_BYTE_COUNT_MISMATCH')
    return db.execute(f'SELECT payload_json FROM {RAW_TABLE} WHERE evidence_key=?', [item['evidence_key']]).fetchone()[0]


def accepted_timestamp(value):
    """Exact established parser interpretation, diagnostics only; no validator changes."""
    text = str(value)
    try:
        if len(text) == 14 and text.isdigit(): return datetime.strptime(text, '%Y%m%d%H%M%S').replace(tzinfo=timezone.utc), 'compact_utc_established_parser'
        parsed = datetime.fromisoformat(text.replace('Z', '+00:00'))
        if parsed.tzinfo is None: return parsed.replace(tzinfo=timezone.utc), 'naive_utc_established_parser_assumption'
        return parsed.astimezone(timezone.utc), 'explicit_offset'
    except ValueError: return None, 'invalid'


def replay(company, submissions, selected):
    recent = submissions.get('filings', {}).get('recent', {})
    require(isinstance(recent, dict), 'SUBMISSIONS_RECENT_INVALID')
    required = ('accessionNumber', 'form', 'filingDate', 'acceptanceDateTime')
    require(all(isinstance(recent.get(k), list) for k in required), 'SUBMISSIONS_ARRAYS_MISSING')
    length = len(recent[required[0]])
    require(length <= MAX_MANIFEST, 'SUBMISSIONS_RECENT_ROW_LIMIT')
    require(all(len(recent[k]) == length for k in required), 'SUBMISSIONS_ARRAY_LENGTH_MISMATCH')
    if 'primaryDocument' in recent:
        require(isinstance(recent['primaryDocument'], list) and len(recent['primaryDocument']) == length, 'SUBMISSIONS_ARRAY_LENGTH_MISMATCH')
    filing_matches = defaultdict(list)
    for i in range(length):
        accession = recent['accessionNumber'][i]
        if accession in {r['accession_number'] for r in selected}:
            filing_matches[accession].append((i, {k: recent[k][i] for k in required}))
            require(len(filing_matches[accession]) <= MAX_OBSERVATIONS, 'SUBMISSION_MATCH_MULTIPLICITY_LIMIT')
    targets = defaultdict(list)
    for row in selected: targets[semantic(row)].append(row)
    observations = []
    gaap = company.get('facts', {}).get('us-gaap', {})
    require(isinstance(gaap, dict), 'COMPANY_FACTS_SHAPE')
    for concept in sorted({r['concept'] for r in selected}):
        node = gaap.get(concept, {})
        require(isinstance(node, dict) and isinstance(node.get('units', {}), dict), 'COMPANY_FACTS_SHAPE')
        for unit, items in sorted(node.get('units', {}).items()):
            require(isinstance(items, list), 'COMPANY_FACTS_SHAPE')
            for index, obs in enumerate(items):
                require(isinstance(obs, dict), 'COMPANY_FACTS_SHAPE')
                key = ('us-gaap', concept, unit, norm(obs.get('start')), norm(obs.get('end')), norm(obs.get('accn')))
                if key in targets:
                    observations.append((key, index, obs))
                    # Never truncate duplicate observations to make the pilot fit.
                    require(len(observations) <= MAX_OBSERVATIONS, 'REPLAY_OBSERVATION_LIMIT')
    stats = Counter(); comparisons = defaultdict(Counter); details = []; interpretations = Counter()
    def compare(field, stored, recovered):
        if not present(recovered): state = 'missing_payload'
        elif not present(stored): state = 'missing_stored_recoverable'
        elif norm(stored) == norm(recovered): state = 'equal'
        else: state = 'different_metadata'
        comparisons[field][state] += 1
    for key, index, observation in observations:
        rows = targets[key]; filings = filing_matches[key[-1]]
        stats['payload_observations_replayed'] += 1
        stats['source_metadata_match_edges'] += len(rows)
        stats['submission_match_edges'] += len(rows) * len(filings)
        if len(rows) > 1: stats['observations_with_multiple_stored_matches'] += 1
        if len(filings) > 1: stats['observations_with_multiple_submission_matches'] += 1
        for row in rows:
            for field, source in RECOVERY_MAP.items(): compare(field, row.get(field), observation.get(source))
            for _, filing in filings:
                for field, source in FILING_MAP.items():
                    value = filing[source]
                    if field == 'public_at':
                        value, interpretation = accepted_timestamp(value); interpretations[interpretation] += 1
                        stored = feasibility.stamp(row.get(field))
                    else: stored = row.get(field)
                    compare(field, stored, value)
                # CF form/filed may describe a disclosure; compare separately from submissions.
            compare('companyfacts_form_vs_stored_form', row.get('form'), observation.get('form'))
            compare('companyfacts_filed_vs_stored_filed_date', row.get('filed_date'), observation.get('filed'))
        # Diagnostic references to existing sources only; no recovered field values/amounts.
        details.append({'companyfacts_path': ['facts', 'us-gaap', key[1], 'units', key[2], index],
            'stored_fact_keys': [r['fact_key'] for r in rows],
            'submissions_recent_indices': [i for i, _ in filings],
            'stored_match_multiplicity': len(rows), 'submission_match_multiplicity': len(filings)})
    counts = {k: stats[k] for k in ('payload_observations_replayed', 'source_metadata_match_edges',
        'submission_match_edges', 'observations_with_multiple_stored_matches', 'observations_with_multiple_submission_matches')}
    matched = {key for key, _, _ in observations}
    counts['stored_rows_without_payload_metadata_match'] = sum(len(rows) for key, rows in targets.items() if key not in matched)
    counts['replayed_observations_without_recent_submission_match'] = sum(not filing_matches[key[-1]] for key, _, _ in observations)
    return {'state': 'replayed' if observations else 'no_selected_observation_matches',
        'metadata_recovered_from_verified_payloads': {'counts': counts,
            'comparisons_by_field': {k: dict(v) for k, v in sorted(comparisons.items())},
            'duplicate_matches': details, 'acceptance_timestamp_interpretations': dict(interpretations),
            'companyfacts_field_map': RECOVERY_MAP, 'submissions_recent_field_map': FILING_MAP},
        'matching_ignores_financial_values': True, 'metadata_match_is_not_numeric_fact_identity': True,
        'conflicting_amendments_inferred': False, 'supersession_inferred': False,
        'historical_canonical_evidence_created': False, 'recovered_fields_persisted': False,
        'filings_files_references_followed': False}


def base_report(decision_at):
    return {'command': 'track-b-provenance-replay', 'version': VERSION,
        'status': 'proposed_not_authorized', 'execution_state': 'failed',
        'decision_at': str(decision_at), 'executed_at': datetime.now(timezone.utc).isoformat(),
        'assessment_is_historical_evidence': False, 'read_only': True, 'bounds': BOUNDS,
        'provider_requests': 0, 'financial_values_compared': False, 'derived_calculations': False,
        'database_writes': False, 'aliases_activated': False, 'consumers_changed': False,
        'recovered_fields_persisted': False, 'constructions_approved': False,
        'contract_selected': None, 'validation_credit': 0, 'model_outputs': [],
        'blockers': [{'code': code, 'state': 'unresolved'} for code in feasibility.BLOCKERS],
        'unresolved_requirement_count': 8}


def audit_and_replay(db, production, decision, report):
    data = {}; total = 0
    for table in feasibility.IDENTITY_TABLES:
        source, rows = metadata_rows(db, table, feasibility.SOURCES[table], set(), MAX_ROWS-total, stage='research.identity.'+table)
        total += source['rows']; data[table] = rows
        require(source['state'] != 'unsupported_schema', 'IDENTITY_SCHEMA_UNPROVEN')
        if table == 'security_classification_evidence' and source['rows']:
            require({'security_id','security_type','public_at','retrieved_at','available_at'} <= set(source['columns']), 'ROSTER_SCHEMA_UNPROVEN')
    identity_source, data['canonical_factor_evidence'] = metadata_rows(db, 'canonical_factor_evidence', ('security_id','cik'), {'security_id'}, MAX_ROWS-total, stage='research.canonical_identity')
    total += identity_source['rows']
    require(identity_source['state'] != 'unsupported_schema', 'IDENTITY_SCHEMA_UNPROVEN')
    population = None
    facts = {}; report['mapping_audits'] = {}; report['source_schemas'] = {}
    for name, conn in (('research', db), ('production', production)):
        fields = tuple(dict.fromkeys(FACT_FIELDS + feasibility.OPTIONAL))
        source, rows = metadata_rows(conn, 'sec_facts', fields, FACT_REQUIRED, MAX_ROWS-total, stage=name+'.sec_facts')
        total += source['rows']; report['source_schemas'][name] = source
        if source['state'] == 'unsupported_schema':
            report['mapping_audits'][name] = {'state': 'unsupported_schema', 'counts': None, 'reason': 'unknown_not_zero'}
            facts[name] = []; continue
        if name == 'research':
            data['sec_facts'] = rows
            reconciliation, population, _ = gaps._reconcile(data, decision)
            report['reconciliation'] = reconciliation
        if population is None:
            report['mapping_audits'][name] = {'state': 'perimeter_unproven', 'counts': None}
            facts[name] = []; continue
        counts, found = visible(rows, population, decision); facts[name] = found
        report['mapping_audits'][name] = {'state': source['state'], 'scope': 'exact_ID_matched_research_perimeter_raw_SEC_only',
            'counts': counts, **mapping_audit(found, source['columns'], decision)}
    source, manifest = metadata_rows(db, RAW_TABLE, MANIFEST, set(MANIFEST) | {'payload_json'}, min(MAX_MANIFEST, MAX_ROWS-total), stage='research.retained_manifest')
    total += source['rows']
    report['retained_manifest'] = source
    if source['state'] == 'unsupported_schema' or report['source_schemas']['research']['state'] == 'unsupported_schema':
        report['pilot'] = {'state': 'unproven_source_schema', 'reason': 'unknown_not_no_pair'}; return
    selection, report['pilot'] = select_pilot(manifest, facts['research'], decision)
    if selection is None: return
    pair, selected = selection
    report['pilot']['lineage_checks'] = verify_lineage(db, pair, remaining=MAX_ROWS-total, decision_at=decision)
    payloads = []; checks = []
    report['pilot']['payload_checks'] = checks
    for item in pair:
        payload, check = verify_payload(item, retained_payload(db, item)); payloads.append(payload); checks.append(check)
    require(sum(c['verified_byte_count'] for c in checks) <= MAX_TOTAL_PAYLOAD, 'TOTAL_PAYLOAD_LIMIT')
    report['pilot']['payload_checks'] = checks
    report['pilot']['replay'] = replay(*payloads, selected)


def run(*, research_db, production_db, decision_at):
    report = base_report(decision_at); paths = {'research': Path(research_db), 'production': Path(production_db)}
    before = {}; after = {}; errors = []
    try:
        # Independently attempt both hashes even when validation/work fails.
        for name, path in paths.items():
            try: before[name] = fingerprint(path)
            except Exception: errors.append('PRE_HASH_' + name.upper())
        validate_paths(*paths.values())
        require(not errors, 'BASELINE_HASH_FAILED')
        decision = feasibility.stamp(decision_at)
        require(decision is not None and decision <= datetime.now(timezone.utc), 'DECISION_INVALID')
        report['decision_at'] = decision.isoformat()
        with duckdb.connect(str(paths['research']), read_only=True, config=history._sql_config()) as db, duckdb.connect(str(paths['production']), read_only=True, config=history._sql_config()) as production:
            audit_and_replay(db, production, decision, report)
        report['execution_state'] = 'completed'
    except ReplayError as exc:
        errors.append(str(exc))
        if exc.diagnostic is not None: report['safe_read_diagnostic'] = exc.diagnostic
    except Exception: errors.append('REPLAY_FAILED')  # Do not leak payloads/SQL/paths.
    finally:
        for name, path in paths.items():
            try: after[name] = fingerprint(path)
            except Exception: errors.append('POST_HASH_' + name.upper())
        for name in paths:
            unchanged = name in before and name in after and before[name] == after[name]
            if not unchanged: errors.append('HASH_NOT_VERIFIED_' + name.upper())
        report['database_hashes'] = {name: {'before': before.get(name), 'after': after.get(name),
            'unchanged': name in before and name in after and before[name] == after[name]} for name in paths}
    report['errors'] = errors
    if errors: report['execution_state'] = 'failed'
    if len(encode(report)) > MAX_REPORT:
        report = {**base_report(decision_at), 'errors': ['REPORT_SIZE_LIMIT'] + errors,
                  'database_hashes': report['database_hashes']}
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--research-db', required=True)
    parser.add_argument('--production-db', required=True)
    parser.add_argument('--decision-at', default='2026-10-05T00:30:00+00:00')
    args = parser.parse_args()
    report = run(research_db=args.research_db, production_db=args.production_db, decision_at=args.decision_at)
    print(encode(report).decode('utf-8'))
    return 0 if report['execution_state'] == 'completed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
