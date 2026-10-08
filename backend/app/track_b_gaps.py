"""Metadata-only identity and accounting diagnostics; no resolver or acquisition.

The roster is the existing visible ordinary-company classification population,
not an approved sample. Exact stored security IDs are the only join key.
"""
from collections import Counter, defaultdict
from datetime import timedelta
import hashlib
import json
from pathlib import Path

import duckdb

from . import track_b_history as h
from .comparable_universe import EXCLUDED_TYPES
from .financial_strength import _within_contract
from .investment_research import TRACK_B_LABELS
from .track_b_panel import PROHIBITED_ARRAYS, SPEC_PATH

MAXIMUM_BYTES = 131072
MAX_ROWS = 500000
MAX_CELL_CHARS = 1024
MAX_ROSTER = 256
MAX_PERIOD_WORK = 50000
SAMPLE_LIMIT = 10
FIELDS = tuple(dict.fromkeys(h.FAMILY_FIELDS['value'] + h.FAMILY_FIELDS['financial_strength']))
# Review buckets only: never accepted or substituted by the draft adapter.
REVIEW_CONCEPT_FIELDS = {
    'LongTermDebtCurrent': 'current_debt', 'ShortTermDebtCurrent': 'current_debt',
    'CommercialPaper': 'current_debt',
    'CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents': 'cash_and_cash_equivalents',
    'RestrictedCashAndCashEquivalentsCurrent': 'cash_and_cash_equivalents',
}
IDENTITY_KEYS = ('run_id', 'lineage_id', 'plan_id', 'operation_type',
                 'operation_contract_version', 'concept_contract_hash')
SOURCES = {k: h.SOURCES[k] for k in ('security_listings', 'universe_snapshot_members',
    'sec_issuers', 'canonical_factor_evidence', 'sec_facts', 'security_classification_evidence')}
SOURCES['security_classification_evidence'] += ('cik', 'durable_identifier', 'is_current')
SOURCES.update({
    'sec_liquidity_runs': IDENTITY_KEYS,
    'sec_liquidity_checkpoints': IDENTITY_KEYS + ('security_id', 'cik', 'status',
        'transaction_succeeded', 'updated_at'),
    'sec_liquidity_raw_provenance': IDENTITY_KEYS + ('security_id', 'cik',
        'endpoint_class', 'retrieved_at', 'response_sha256', 'byte_count', 'parser_version'),
})


class GapDiagnosticError(h.InventoryError):
    reason_code = 'TRACK_B_GAP_DIAGNOSTIC_FAILED'


def _hash(value):
    return hashlib.sha256(str(value).encode('utf-8')).hexdigest()


def _bounded(items):
    return {'items': items[:SAMPLE_LIMIT], 'total_count': len(items),
            'returned_count': min(len(items), SAMPLE_LIMIT), 'sample_limit': SAMPLE_LIMIT,
            'truncated': len(items) > SAMPLE_LIMIT}


def _read(db, table):
    """Fixed base-table projections only; no payloads, amounts or dynamic adapters."""
    base = db.execute("""SELECT table_type FROM information_schema.tables
        WHERE table_catalog=current_database() AND table_schema='main' AND table_name=?""",
        [table]).fetchone()
    if not base:
        return {'state': 'absent_evidence', 'row_count': 0, 'columns': [],
                'missing_adapter_columns': list(SOURCES[table])}, []
    if base[0] != 'BASE TABLE':
        return {'state': 'incompatible_schema', 'row_count': None, 'columns': [],
                'missing_adapter_columns': list(SOURCES[table])}, []
    schema = {r[0] for r in db.execute("""SELECT column_name FROM information_schema.columns
        WHERE table_catalog=current_database() AND table_schema='main' AND table_name=?""",
        [table]).fetchall()}
    columns = [c for c in SOURCES[table] if c in schema]
    count = db.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0]
    if count > MAX_ROWS:
        raise GapDiagnosticError('metadata row bound exceeded')
    summary = {'state': 'metadata_only' if columns else 'incompatible_schema',
               'row_count': count, 'columns': columns,
               'missing_adapter_columns': sorted(set(SOURCES[table]) - set(columns))}
    if not columns:
        return summary, []
    oversized = ' OR '.join(f'length(CAST("{c}" AS VARCHAR)) > {MAX_CELL_CHARS}' for c in columns)
    if db.execute(f'SELECT count(*) FROM "{table}" WHERE {oversized}').fetchone()[0]:
        raise GapDiagnosticError('metadata cell bound exceeded')
    projection = ','.join(f'"{c}"' for c in columns)
    cursor = db.execute(f'SELECT {projection} FROM "{table}" LIMIT {MAX_ROWS + 1}')
    rows = []
    while batch := cursor.fetchmany(2048):
        rows.extend(dict(zip(columns, row)) for row in batch)
    if len(rows) != count:
        raise GapDiagnosticError('metadata count mismatch')
    return summary, rows


def _visible_classification(row, decision):
    public, retrieved, available = (h._stamp(row.get(k)) for k in
                                   ('public_at', 'retrieved_at', 'available_at'))
    return bool(row.get('is_current', True) is True and public and retrieved and available
                and available == max(public, retrieved) and available <= decision)


def _reconcile(data, decision):
    # Same timestamp/type roster as the existing financial-strength/liquidity
    # reader, without its canonical fallback or symbol-based tuple duplication.
    classes = defaultdict(set)
    for row in data['security_classification_evidence']:
        if row.get('security_id') and _visible_classification(row, decision):
            classes[str(row['security_id'])].add(row.get('security_type'))
    roster = {sid for sid, types in classes.items() if 'us_operating_company' in types}
    if len(roster) > MAX_ROSTER:
        raise GapDiagnosticError('roster bound exceeded')
    stored = {str(r['security_id']) for table in ('security_listings', 'universe_snapshot_members',
        'sec_issuers', 'canonical_factor_evidence', 'sec_facts') for r in data[table] if r.get('security_id')}
    ciks = defaultdict(set)
    invalid_ciks = set()
    for table in ('security_listings', 'sec_issuers', 'sec_facts', 'security_classification_evidence'):
        for row in data[table]:
            if not row.get('security_id') or not row.get('cik'):
                continue
            sid, cik = str(row['security_id']), str(row['cik'])
            if not cik.isascii() or not cik.isdigit() or not 1 <= len(cik) <= 10 or int(cik) == 0:
                invalid_ciks.add(sid)
            else:
                ciks[sid].add(cik.zfill(10))
    owners = defaultdict(set)
    for sid, values in ciks.items():
        for cik in values:
            owners[cik].add(sid)
    disposition = {}
    reasons = {}
    for sid in sorted(roster):
        flags = []
        if len(classes[sid]) > 1:
            flags.append('conflicting_current_classifications')
        if len(ciks[sid]) > 1:
            flags.append('multiple_stored_ciks_without_effective_resolution')
        if sid in invalid_ciks:
            flags.append('invalid_stored_cik')
        if any(len(owners[cik]) > 1 for cik in ciks[sid]):
            flags.append('shared_cik_share_class_identity_unresolved')
        disposition[sid] = 'unmatched' if sid not in stored else 'ambiguous' if flags else 'matched'
        reasons[sid] = flags
    outside = stored - roster
    excluded = {sid for sid in outside if len(classes[sid]) == 1
                and classes[sid] <= (EXCLUDED_TYPES - {'classification_unavailable'})}
    counts = Counter(disposition.values())
    result = {'stored_security_id_count': len(stored), 'comparable_roster_count': len(roster),
        'reference_counts': {'verified_inventory': 500, 'operator_comparable_roster': 71},
        'reference_count_deltas': {'stored': len(stored)-500, 'roster': len(roster)-71},
        'roster_counts': {k: counts[k] for k in ('matched', 'ambiguous', 'unmatched')},
        'stored_partition': {'matched_roster': counts['matched'], 'ambiguous_roster': counts['ambiguous'],
            'outside_perimeter_by_current_classification': len(excluded),
            'outside_roster_perimeter_unresolved': len(outside-excluded)},
        'outside_comparable_roster_count': len(outside),
        'outside_perimeter_count': len(excluded),
        'classification_source_present': bool(data['security_classification_evidence']),
        'identity_rule': 'Exact security_id intersection only; CIK is a consistency check, never a join. No ticker/name matching.',
        'roster_semantics': 'Existing visible ordinary-company classification roster; counts are security-level until issuer/share-class identity is resolved.',
        'historical_membership_certified': False, 'effective_identity_certified': False,
        'approved_eligibility': False,
        'current_identity_semantics': 'Matched means stored durable ID agrees without detected conflict; missing CIK and unproven effective intervals are reported, not certified.',
        'matched_missing_cik_count': sum(not ciks[sid] for sid in roster if disposition[sid] == 'matched'),
        'roster_details': [{'security_identity_sha256': _hash(sid), 'disposition': disposition[sid],
            'reason_codes': reasons[sid]} for sid in sorted(roster, key=_hash)]}
    return result, {sid for sid in roster if disposition[sid] == 'matched'}, ciks


def _completed(data, sources, decision):
    """Recognize matching operation/checkpoint and exact two-endpoint metadata.

    Payloads are deliberately not inspected or revalidated. Completion does not
    certify concept completeness, extraction, accounting chains or readiness.
    """
    names = ('sec_liquidity_runs', 'sec_liquidity_checkpoints', 'sec_liquidity_raw_provenance')
    required = (set(IDENTITY_KEYS), set(IDENTITY_KEYS) | {'security_id', 'cik', 'status', 'transaction_succeeded'},
        set(IDENTITY_KEYS) | {'security_id', 'cik', 'endpoint_class'})
    if any(not keys <= set(sources[name]['columns']) for name, keys in zip(names, required)):
        return set(), set(), {'state': 'completion_metadata_unavailable', 'completed_current_count': None,
                            'completed_by_boundary_count': None}
    def identity(row):
        return tuple(row.get(k) for k in IDENTITY_KEYS)
    runs = {identity(row) for row in data[names[0]] if all(identity(row))}
    endpoints = defaultdict(set)
    visible = defaultdict(set)
    for row in data[names[2]]:
        key = identity(row) + (row.get('security_id'), row.get('cik'))
        endpoint = row.get('endpoint_class')
        if endpoint not in ('submissions', 'companyfacts'):
            continue
        endpoints[key].add(endpoint)
        stamp = h._stamp(row.get('retrieved_at'))
        if stamp and stamp <= decision:
            visible[key].add(endpoint)
    completed, by_boundary = set(), set()
    for row in data[names[1]]:
        key = identity(row) + (row.get('security_id'), row.get('cik'))
        if (identity(row) in runs and row.get('operation_type') == 'sec_liquidity_evidence_ingestion'
            and row.get('status') == 'completed' and row.get('transaction_succeeded') is True
            and row.get('security_id') and row.get('cik')
            and endpoints[key] == {'submissions', 'companyfacts'}):
            completed.add(str(row['security_id']))
            stamp = h._stamp(row.get('updated_at'))
            if stamp and stamp <= decision and visible[key] == endpoints[key]:
                by_boundary.add(str(row['security_id']))
    return completed, by_boundary, {'state': 'declared_completion_metadata',
        'completed_current_count': len(completed), 'completed_by_boundary_count': len(by_boundary),
        'semantics': 'Matching completed transactional checkpoints plus retained submissions/companyfacts metadata; payload integrity and parser completeness not recertified.',
        'repeat_retrieval_recommended': False,
        'absence_semantics': 'Missing exact concepts/canonical fields after completed retrieval require stored-payload/parser/mapping diagnosis; absence alone does not indicate another identical retrieval.'}


def _provenance_flags(row, layer):
    flags = []
    for key in ('public_at', 'retrieved_at'):
        if not h._stamp(row.get(key)):
            flags.append('missing_aware_' + key)
    if layer == 'raw_sec':
        for key in ('cik', 'taxonomy', 'accession_number', 'source_endpoint'):
            if not row.get(key):
                flags.append('missing_' + key)
    else:
        for key in ('alias_contract_version', 'accession_or_source_identifier', 'source_fact_key'):
            if not row.get(key):
                flags.append('missing_' + key)
        public, retrieved, available = (h._stamp(row.get(k)) for k in
                                       ('public_at', 'retrieved_at', 'available_at'))
        materialized = h._stamp(row.get('materialized_at'))
        if row.get('materialization_run_id') and not materialized:
            flags.append('missing_materialization_timestamp')
        expected = max(public, retrieved) if public and retrieved else None
        if expected and row.get('materialization_run_id') and materialized:
            expected = max(expected, materialized)
        if not available or not expected or available != expected:
            flags.append('availability_rule_unverified')
    return flags


def _period_shape(row):
    start, end = h._date(row.get('period_start')), h._date(row.get('period_end') or row.get('instant_date'))
    if not end or (row.get('period_start') is not None and not start):
        return 'missing_or_invalid_period_metadata'
    if not start:
        return 'instant'
    days = (end-start).days+1
    if days <= 0:
        return 'invalid_period_order'
    if 60 <= days <= 120:
        return 'standalone_quarter_shape_candidate'
    if 150 <= days <= 300:
        return 'multi_quarter_cumulative_shape_candidate'
    if 330 <= days <= 400:
        return 'annual_duration_candidate'
    return 'other_duration_not_chained'


def _period_diagnostics(periods):
    quarters = sorted({(s, e) for s, e in periods if s and 60 <= (e-s).days+1 <= 120})
    if len(periods) > MAX_PERIOD_WORK:
        raise GapDiagnosticError('period state bound exceeded')
    adjacency = Counter()
    for previous, current in zip(quarters, quarters[1:]):
        adjacency['contiguous' if current[0] == previous[1]+timedelta(days=1)
                  else 'gap' if current[0] > previous[1] else 'overlap'] += 1
    starts = defaultdict(set)
    ends = defaultdict(set)
    for start, end in periods:
        if start:
            starts[start].add(end)
            ends[end].add(start)
    return {'standalone_quarter_period_count': len(quarters),
            'adjacent_quarter_pair_counts': {k: adjacency[k] for k in ('contiguous', 'gap', 'overlap')},
            'shared_start_multiple_end_count': sum(len(v)>1 for v in starts.values()),
            'shared_end_multiple_start_count': sum(len(v)>1 for v in ends.values())}


def _diagnose_layer(rows, layer, matched, decision, source):
    required = {'security_id', 'concept' if layer == 'raw_sec' else 'canonical_field'}
    supported = source['state'] != 'incompatible_schema' and (not source['row_count'] or required <= set(source['columns']))
    index = defaultdict(lambda: defaultdict(list))
    if not supported:
        return {'evidence_layer': layer, 'adapter_supported': False, 'population_denominator': len(matched),
            'fields': {field: {'stored_observation_count': None, 'missing_stored_field_count': None,
                'missing_exact_stored_concept_count': None,
                'existing_field_without_visible_draft_accepted_metadata_count': None,
                'post_boundary_only_count': None, 'adapter_states': {}, 'adapter_reasons': {},
                'stored_period_shapes': {}, 'independent_provenance_flags': {}, 'period_diagnostics': {}}
                for field in FIELDS},
            'families': {family: {'issue_counts_nonexclusive': {'source_schema_unavailable': len(matched)},
                'certified_formula_count': 0} for family in ('value', 'financial_strength')},
            'company_issue_samples': _bounded([{'security_identity_sha256': _hash(sid),
                'reason_codes': ['source_schema_unavailable']} for sid in sorted(matched, key=_hash)]),
            'interpretation': 'Unsupported source schema; no absence or period-chain inference.'}, index
    for row in rows:
        sid = str(row.get('security_id') or '')
        field = (h.CONCEPT_FIELDS.get(row.get('concept')) or REVIEW_CONCEPT_FIELDS.get(row.get('concept'))) if layer == 'raw_sec' else row.get('canonical_field')
        if sid in matched and field in FIELDS:
            index[sid][field].append(row)
    fields = {}
    valid = defaultdict(lambda: defaultdict(set))
    flags = defaultdict(set)
    for field in FIELDS:
        states, reasons, shapes, provenance = Counter(), Counter(), Counter(), Counter()
        absent, rejected, exact_absent, post_only = [], [], [], []
        for sid in sorted(matched):
            evidence = index[sid][field]
            if not evidence:
                absent.append(sid)
                flags[sid].add(field + ':missing_stored_field')
            if not any((row.get('concept') if layer == 'raw_sec' else row.get('original_concept_or_field'))
                       in h.FIELDS[field][2] for row in evidence):
                exact_absent.append(sid)
                flags[sid].add(field + ':missing_exact_stored_concept')
            per_states = Counter()
            for row in evidence:
                state, reason = h._accounting_state(row, field, layer, decision)
                states[state] += 1
                per_states[state] += 1
                reasons[reason] += 1
                shapes[_period_shape(row)] += 1
                if _period_shape(row) == 'multi_quarter_cumulative_shape_candidate':
                    flags[sid].add(field + ':cumulative_duration_shape_not_chained')
                for flag in _provenance_flags(row, layer):
                    provenance[flag] += 1
                    flags[sid].add(field + ':missing_or_inconsistent_provenance')
                if state == 'metadata_compatible_unverified':
                    valid[sid][field].add((h._date(row.get('period_start')),
                        h._date(row.get('period_end') or row.get('instant_date'))))
                else:
                    flags[sid].add(field + ':' + reason)
            if evidence and not per_states['metadata_compatible_unverified']:
                rejected.append(sid)
            if per_states and set(per_states) == {'post_boundary'}:
                post_only.append(sid)
        fields[field] = {'stored_observation_count': sum(states.values()),
            'missing_stored_field_count': len(absent) if supported else None,
            'missing_exact_stored_concept_count': len(exact_absent) if supported else None,
            'existing_field_without_visible_draft_accepted_metadata_count': len(rejected) if supported else None,
            'post_boundary_only_count': len(post_only) if supported else None,
            'adapter_states': {k: states[k] for k in h.ROW_STATES},
            'adapter_reasons': dict(sorted(reasons.items())),
            'stored_period_shapes': dict(sorted(shapes.items())),
            'independent_provenance_flags': dict(sorted(provenance.items())),
            'period_diagnostics': {}}
        totals = Counter()
        pairs = Counter()
        for sid in sorted(matched):
            diagnostic = _period_diagnostics(valid[sid][field])
            totals.update({k:v for k,v in diagnostic.items() if k != 'adjacent_quarter_pair_counts'})
            pairs.update(diagnostic['adjacent_quarter_pair_counts'])
            for reason in ('gap', 'overlap'):
                if diagnostic['adjacent_quarter_pair_counts'][reason]:
                    flags[sid].add(field + ':quarter_period_' + reason)
        fields[field]['period_diagnostics'] = dict(sorted(totals.items())) | {'adjacent_quarter_pair_counts': dict(sorted(pairs.items()))}
    family_counts = {family: Counter() for family in ('value', 'financial_strength')}
    for sid in sorted(matched):
        periods = valid[sid]
        ocf, capex = periods['operating_cash_flow'], periods['capital_expenditure']
        aligned = ocf & capex
        value_chains = h._chains(aligned, 4)
        for family in family_counts:
            missing = [f for f in h.FAMILY_FIELDS[family] if not index[sid][f]]
            if missing:
                family_counts[family]['missing_stored_input'] += 1
        family_counts['value']['structural_chain_present_unverified' if value_chains else 'no_structural_chain'] += 1
        if ocf-capex:
            family_counts['value']['companies_with_ocf_periods_without_identical_capex_period'] += 1
            flags[sid].add('value:ocf_capex_period_endpoints_not_identical')
        if capex-ocf:
            family_counts['value']['companies_with_capex_periods_without_identical_ocf_period'] += 1
        ocf_chains = h._chains(ocf, 4)
        chain_ends = {e for _, e in ocf_chains}
        instant_ends = [{e for _, e in periods[f]} for f in ('current_debt', 'non_current_debt', 'cash_and_cash_equivalents')]
        all_ends = set.intersection(*instant_ends)
        strength_chains = [(s, e) for s, e in ocf_chains if e in all_ends]
        family_counts['financial_strength']['structural_chain_present_unverified' if strength_chains else 'no_structural_chain'] += 1
        if chain_ends-all_ends:
            family_counts['financial_strength']['companies_with_ocf_chain_end_missing_aligned_instants'] += 1
            flags[sid].add('financial_strength:missing_aligned_instant_endpoints')
        for f, ends in zip(('current_debt', 'non_current_debt', 'cash_and_cash_equivalents'), instant_ends):
            family_counts['financial_strength'][f + '_missing_ocf_chain_endpoint_count'] += len(chain_ends-ends)
        if not ocf_chains:
            flags[sid].add('financial_strength:no_four_standalone_ocf_quarter_chain')
        if not value_chains:
            flags[sid].add('value:no_four_aligned_standalone_quarter_chain')
    return {'evidence_layer': layer, 'adapter_supported': supported,
        'population_denominator': len(matched), 'fields': fields,
        'families': {family: {'issue_counts_nonexclusive': dict(sorted(counts.items())),
                            'certified_formula_count': 0} for family, counts in family_counts.items()},
        'company_issue_samples': _bounded([{'security_identity_sha256': _hash(sid),
            'reason_codes': sorted(flags[sid])} for sid in sorted(matched, key=_hash)]),
        'interpretation': 'Duration labels are shape candidates, not certified fiscal quarters/YTD. Gaps/overlaps compare neighboring distinct accepted quarter shapes. Issue flags overlap; metadata chains are not certified formulas.'}, index


def _concept_explanations(data, matched, decision):
    debt = [r for r in data['canonical_factor_evidence'] if r.get('canonical_field') == 'current_debt']
    def debt_summary(rows):
        counts = Counter(); missing = 0; mismatch = 0
        for row in rows:
            concept = row.get('original_concept_or_field')
            if concept:
                counts[str(concept)] += 1
            else:
                missing += 1
            mismatch += h._accounting_state(row, 'current_debt', 'canonical', decision)[1] == 'concept_mismatch'
        return {'stored_row_count': len(rows), 'concept_mismatch_count': mismatch,
            'missing_concept_count': missing,
            'original_concepts': _bounded([{'concept': c, 'row_count': n} for c,n in sorted(counts.items())])}
    raw_cash = [r for r in data['sec_facts'] if r.get('concept') == 'CashAndCashEquivalentsAtCarryingValue']
    cash = [r for r in data['canonical_factor_evidence'] if r.get('canonical_field') == 'cash_and_cash_equivalents']
    alternate_cash = [r for r in data['canonical_factor_evidence'] if r.get('original_concept_or_field') == 'CashAndCashEquivalentsAtCarryingValue'
                      and r.get('canonical_field') != 'cash_and_cash_equivalents']
    raw_debt = [r for r in data['sec_facts'] if r.get('concept') in
                {'ShortTermBorrowings', 'LongTermDebtCurrent', 'ShortTermDebtCurrent', 'CommercialPaper'}]
    return {'current_debt': {'expected_draft_exact_concept': 'ShortTermBorrowings',
        'all_stored': debt_summary(debt),
        'matched_roster': debt_summary([r for r in debt if str(r.get('security_id')) in matched]),
        'verified_inventory_reference_mismatch_count': 1322,
        'reference_mismatch_count_delta': debt_summary(debt)['concept_mismatch_count']-1322,
        'raw_review_concepts_not_activated': _bounded([{'concept': c, 'row_count': n} for c,n in
            sorted(Counter(str(r['concept']) for r in raw_debt if r['concept'] != 'ShortTermBorrowings').items())]),
        'explanation': 'Draft history accepts only ShortTermBorrowings for current_debt. Other stored concepts can be permitted by separate existing alias contracts yet remain incompatible with this draft shape; no alias is activated or debt components substituted.'},
        'cash': {'raw_exact_row_count': len(raw_cash),
            'canonical_exact_field_row_count': len(cash),
            'same_exact_concept_under_other_canonical_fields_row_count': len(alternate_cash),
            'matched_roster_raw_exact_row_count': sum(str(r.get('security_id')) in matched for r in raw_cash),
            'matched_roster_canonical_exact_field_row_count': sum(str(r.get('security_id')) in matched for r in cash),
            'other_canonical_fields': _bounded([{'canonical_field': f, 'row_count': n} for f,n in
                sorted(Counter(str(r.get('canonical_field') or '') for r in alternate_cash).items())]),
            'explanation': 'Raw exact cash presence and canonical exact field coverage are separate. Evidence under unrestricted_cash or other fields is counted but not substituted; raw evidence is never promoted.'}}


def diagnose(*, research_db, production_db, decision_at):
    decision = h._utc(decision_at)
    paths = {'research': Path(research_db), 'production': Path(production_db)}
    h.validate_paths(paths['research'], paths['production'])
    before = {}
    try:
        baseline_failed = False
        for name, path in paths.items():
            try:
                before[name] = h.fingerprint(path)
            except Exception:
                baseline_failed = True
        if baseline_failed:
            raise GapDiagnosticError('database fingerprint baseline unavailable')
        datasets, summaries = {}, {}
        for name, path in paths.items():
            datasets[name], summaries[name] = {}, {}
            with duckdb.connect(str(path), read_only=True, config=h._sql_config()) as db:
                for table in SOURCES:
                    summaries[name][table], datasets[name][table] = _read(db, table)
        roster_source = summaries['research']['security_classification_evidence']
        if roster_source['state'] == 'incompatible_schema' or (roster_source['row_count'] and
            not {'security_id', 'security_type', 'public_at', 'retrieved_at', 'available_at'} <= set(roster_source['columns'])):
            raise GapDiagnosticError('roster adapter schema unsupported')
        reconciliation, matched, _ = _reconcile(datasets['research'], decision)
        complete, visible_complete, completion = _completed(datasets['research'], summaries['research'], decision)
        databases = {}
        for name, data in datasets.items():
            layers = {}; indexes = {}
            for layer, table in (('raw_sec', 'sec_facts'), ('canonical', 'canonical_factor_evidence')):
                layers[layer], indexes[layer] = _diagnose_layer(data[table], layer, matched, decision, summaries[name][table])
            coverage = {}
            for field in FIELDS:
                raw = {sid for sid in matched if any(r.get('concept') in h.FIELDS[field][2]
                                                    for r in indexes['raw_sec'][sid][field])}
                canonical = {sid for sid in matched if any(r.get('original_concept_or_field') in h.FIELDS[field][2]
                                                    for r in indexes['canonical'][sid][field])}
                coverage[field] = {'raw_and_canonical': len(raw & canonical), 'raw_only': len(raw-canonical),
                    'canonical_only': len(canonical-raw), 'neither': len(matched-(raw|canonical)),
                    'semantics': 'Exact stored concept in its expected field, regardless of usability. Not materialization diagnosis or readiness.'}
                if not all(layers[layer]['adapter_supported'] for layer in layers):
                    for key in ('raw_and_canonical', 'raw_only', 'canonical_only', 'neither'):
                        coverage[field][key] = None
            databases[name] = {'layers': layers, 'raw_canonical_coverage': coverage,
                               'concept_explanations': _concept_explanations(data, matched, decision)}
        # Completion and absent concept checks use research only, never the
        # production-local indexes from the separate coverage loop above.
        research_raw = {str(r.get('security_id')): set() for r in datasets['research']['sec_facts']}
        for row in datasets['research']['sec_facts']:
            field = h.CONCEPT_FIELDS.get(row.get('concept'))
            if field in FIELDS:
                research_raw[str(row.get('security_id'))].add(field)
        missing = {sid for sid in matched if set(FIELDS)-research_raw.get(sid, set())}
        completion.update({'matched_roster_completed_current_count': len(matched & complete),
            'matched_roster_completed_by_boundary_count': len(matched & visible_complete),
            'missing_exact_raw_input_after_completed_retrieval_count': len(missing & complete),
            'missing_exact_raw_input_without_completion_metadata_count': len(missing-complete),
            'completion_does_not_certify_accounting_chain': True})
        spec = json.loads(SPEC_PATH.read_text(encoding='utf-8'))
        result = {'command': 'track-b-identity-accounting-gap-diagnostic', 'version': 'track-b-gap-diagnostic-1.0.0',
            'decision_at': decision.isoformat(), 'labels': TRACK_B_LABELS,
            'read_only': True, 'metadata_only': True, 'realized_outcome_values_read': False,
            'reconciliation': reconciliation, 'databases': databases, 'sources': summaries,
            'controlled_sec_retrievals': completion, 'estimated_provider_requests': 0,
            'accounting_rules_changed': False, 'missing_inputs_are_zero': False,
            'specification': {'version': spec['version'], 'status': spec['status'],
                'sha256': _hash(json.dumps(spec, sort_keys=True, separators=(',', ':')))},
            'blockers': [{'code': code, 'state': 'unresolved'} for code in spec['preregistration_blockers']],
            'unresolved_requirement_count': len(spec['preregistration_blockers']),
            'preregistration_ready': False, 'approved_eligibility': False,
            'acquisition_authorized': False, 'contract_selected': None, 'sample_thresholds': None,
            'model_executed': False, 'panel_persisted': False, 'validation_credit': 0,
            **{key: [] for key in PROHIBITED_ARRAYS},
            'bounds': {'maximum_compact_utf8_bytes': MAXIMUM_BYTES, 'maximum_metadata_rows_per_table': MAX_ROWS,
                'maximum_metadata_cell_characters': MAX_CELL_CHARS, 'maximum_roster_details': MAX_ROSTER,
                'sample_limit': SAMPLE_LIMIT, 'maximum_period_state': MAX_PERIOD_WORK,
                'maximum_chain_work_per_call': h.MAX_CHAIN_WORK, 'sql_memory_limit': h.MARKET_SQL_MEMORY,
                'sql_threads': 1, 'disk_spill_allowed': False, 'unsupported_tables_read': False}}
    except duckdb.OutOfMemoryException:
        raise GapDiagnosticError('SQL resource bound exceeded') from None
    finally:
        # Attempt both after fingerprints even when one fails; never publish a
        # report if a hash is unavailable or either file changed.
        after = {}; failures = []
        for name, path in paths.items():
            try:
                after[name] = h.fingerprint(path)
            except Exception:
                failures.append(name)
        if failures or before != after:
            raise GapDiagnosticError('database fingerprint verification failed') from None
    result['database_fingerprints'] = {name: {'before': before[name], 'after': after[name], 'unchanged': True}
                                       for name in paths}
    return _within_contract(result, MAXIMUM_BYTES)
