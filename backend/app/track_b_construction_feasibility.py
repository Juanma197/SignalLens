"""PROPOSED: bounded read-only construction feasibility, never a producer/resolver.

No amount subtraction/summing, alias activation, provider or consumer integration.
Optional proof columns are a review-only adapter contract, not a schema migration.
Stored declarations plus references establish candidates, not accounting certification.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import duckdb

from . import track_b_gaps as gaps
from . import track_b_history as history
from .sec_ingestion import validate_paths
from .track_b_panel import PROHIBITED_ARRAYS, SPEC_PATH

VERSION = 'track-b-construction-feasibility-proposed-0.1.0'
MAX_ROWS = 500_000
MAX_TOTAL_ROWS = 500_000
MAX_CELL = 1024
MAX_GROUP_ROWS = 50_000
MAX_WORK = 50_000
MAX_BYTES = 131_072
OCF = 'NetCashProvidedByUsedInOperatingActivities'
CAPEX = 'PaymentsToAcquirePropertyPlantAndEquipment'
CASH = 'CashAndCashEquivalentsAtCarryingValue'
DEBT = ('ShortTermBorrowings', 'LongTermDebtCurrent', 'LongTermDebtNoncurrent')
REVIEW_DEBT = DEBT + ('CommercialPaper', 'LongTermDebt', 'ShortTermDebtCurrent')
CONCEPTS = (OCF, CAPEX, CASH) + REVIEW_DEBT
IDENTITY_TABLES = ('security_listings', 'universe_snapshot_members', 'sec_issuers',
                   'security_classification_evidence')
# fy/fp/frame/amendment flags are retained as diagnostics only, never proof.
OPTIONAL = ('fiscal_year', 'fiscal_period', 'frame', 'is_amendment', 'is_revision',
    'fiscal_year_start', 'fiscal_quarter', 'fiscal_quarter_end_1', 'fiscal_quarter_end_2',
    'fiscal_quarter_end_3', 'fiscal_quarter_end_4', 'fiscal_calendar_source',
    'duration_kind', 'context_scope', 'context_dimensions', 'accounting_basis',
    'context_source', 'revision_set_id', 'revision_status', 'revision_source',
    'source_decimals', 'precision_source', 'component_members',
    'borrowing_universe_members', 'borrowing_scope_source', 'fiscal_metadata_available_at',
    'context_metadata_available_at', 'revision_metadata_available_at',
    'precision_metadata_available_at', 'borrowing_metadata_available_at')
BASE = ('security_id', 'unit', 'currency', 'scale', 'period_start', 'period_end',
        'public_at', 'retrieved_at')
SOURCES = {t: gaps.SOURCES[t] for t in IDENTITY_TABLES}
SOURCES['sec_facts'] = BASE + ('fact_key', 'cik', 'taxonomy', 'concept',
    'accession_number', 'source_endpoint', 'available_at') + OPTIONAL
SOURCES['canonical_factor_evidence'] = BASE + ('evidence_key', 'source_fact_key',
    'original_concept_or_field', 'canonical_field', 'instant_date', 'available_at',
    'materialized_at', 'materialization_run_id', 'reliability_state',
    'alias_contract_version', 'accession_or_source_identifier', 'taxonomy') + OPTIONAL
BLOCKERS = tuple(json.loads(SPEC_PATH.read_text(encoding='utf-8'))['preregistration_blockers'])


class FeasibilityError(RuntimeError):
    pass


def stamp(value):
    return history._stamp(value)


def input_availability(row, layer):
    """Retain established SEC raw/legacy rules; controlled inputs include creation."""
    public, retrieved = (stamp(row.get(k)) for k in ('public_at', 'retrieved_at'))
    if not public or not retrieved:
        return None, 'missing_aware_input_timestamps'
    expected = max(public, retrieved)
    if layer == 'canonical':
        if row.get('_provenance_valid') is not True:
            return None, 'control_provenance_unproven'
        operation = row.get('_operation_type')
        controlled = operation == 'liquidity_canonical_materialization'
        if row.get('materialization_run_id') and not controlled:
            return None, 'control_provenance_unproven'
        if operation not in (None, '', 'liquidity_canonical_materialization'):
            return None, 'control_provenance_unproven'
        if not controlled and row.get('alias_contract_version') != 'milestone-37-audited-alias-contracts-1':
            return None, 'legacy_contract_unproven'
        if controlled:
            materialized = stamp(row.get('materialized_at'))
            if not materialized:
                return None, 'missing_aware_materialized_at'
            expected = max(expected, materialized)
        if stamp(row.get('available_at')) != expected:
            return None, 'availability_rule_mismatch'
    # sec_facts has no canonical available_at requirement. Retain its max rule.
    return expected, None


def assessment_timing(input_times, executed_at, materialized_at=None):
    """Synthetic/review helper; no writes and no historical availability claim."""
    inputs = [stamp(x) for x in input_times]
    execution = stamp(executed_at)
    if not inputs or any(x is None for x in inputs) or execution is None:
        raise FeasibilityError('TIMING_UNPROVEN')
    latest = max(inputs)
    if execution < latest:
        raise FeasibilityError('EXECUTION_BEFORE_INPUTS')
    persisted = None
    if materialized_at is not None:
        creation = stamp(materialized_at)
        if creation is None or creation < execution:
            raise FeasibilityError('PERSISTENCE_BEFORE_EXECUTION')
        persisted = max(latest, creation)
    return {'input_available_at': latest.isoformat(), 'executed_at': execution.isoformat(),
            'future_persisted_revision_available_at': persisted.isoformat() if persisted else None}


def read_table(db, table, remaining=MAX_TOTAL_ROWS):
    base = db.execute("""SELECT table_type FROM information_schema.tables
        WHERE table_catalog=current_database() AND table_schema='main' AND table_name=?""", [table]).fetchone()
    if not base:
        return {'state': 'absent_evidence', 'row_count': 0, 'columns': [],
                'missing_columns': list(SOURCES[table])}, []
    if base[0] != 'BASE TABLE':
        return {'state': 'unsupported_schema', 'row_count': None, 'columns': [],
                'missing_columns': list(SOURCES[table])}, []
    schema = {r[0]:r[1] for r in db.execute("""SELECT column_name,data_type FROM information_schema.columns
        WHERE table_catalog=current_database() AND table_schema='main' AND table_name=?""", [table]).fetchall()}
    columns = [c for c in SOURCES[table] if c in schema]
    count = db.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0]
    if count > MAX_ROWS:
        raise FeasibilityError('ROW_LIMIT')
    if count > remaining:
        raise FeasibilityError('TOTAL_ROW_LIMIT')
    required = {'security_id', 'concept' if table == 'sec_facts' else 'original_concept_or_field'}
    unsupported = not columns or (table not in IDENTITY_TABLES and not required <= set(columns))
    summary = {'state': 'unsupported_schema' if unsupported else 'supported', 'row_count': count,
               'columns': columns, 'missing_columns': sorted(set(SOURCES[table])-set(columns)),
               'value_projection': 'stored_value_signature_sign_and_order_only_no_amounts' if 'value' in schema else 'not_retained'}
    if unsupported:
        return summary, []
    projection = [f'"{c}"' for c in columns]
    # Values never leave SQL. Signature diagnoses stored disagreements, not materiality.
    if table not in IDENTITY_TABLES:
        numeric = 'value' in schema and (schema['value'] in ('DOUBLE','FLOAT','REAL','TINYINT','SMALLINT','INTEGER','BIGINT','HUGEINT','UTINYINT','USMALLINT','UINTEGER','UBIGINT') or schema['value'].startswith('DECIMAL('))
        if numeric:
            concept_column = 'concept' if table=='sec_facts' else 'original_concept_or_field'
            projection += [f'dense_rank() OVER (PARTITION BY security_id, "{concept_column}" ORDER BY value) AS _amount_order_rank']
            projection += ["CASE WHEN isfinite(try_cast(value AS DOUBLE)) THEN sha256(CAST(value AS VARCHAR)) END AS _value_signature",
                'CASE WHEN isfinite(try_cast(value AS DOUBLE)) THEN try_cast(value AS DOUBLE)>=0 END AS _nonnegative']
        if table == 'canonical_factor_evidence' and 'provenance' in schema:
            projection += ["json_extract_string(try_cast(provenance AS JSON),'$.operation_type') AS _operation_type",
                'try_cast(provenance AS JSON) IS NOT NULL AS _provenance_valid']
            if 'scale' not in schema:
                projection += ["json_extract_string(try_cast(provenance AS JSON),'$.scale') AS scale"]
    checks = [f'length(CAST("{c}" AS VARCHAR))>{MAX_CELL}' for c in columns]
    if table == 'canonical_factor_evidence' and 'provenance' in schema:
        checks += [f"length(json_extract_string(try_cast(provenance AS JSON),'$.operation_type'))>{MAX_CELL}",
                   f"length(json_extract_string(try_cast(provenance AS JSON),'$.scale'))>{MAX_CELL}"]
    if checks and db.execute(f'SELECT count(*) FROM "{table}" WHERE '+ ' OR '.join(checks)).fetchone()[0]:
        raise FeasibilityError('CELL_LIMIT')
    rows = []
    cursor = db.execute(f'SELECT {",".join(projection)} FROM "{table}" LIMIT {MAX_ROWS+1}')
    while batch := cursor.fetchmany(2048):
        rows.extend(dict(zip([c[0] for c in cursor.description], values)) for values in batch)
    if len(rows) != count:
        raise FeasibilityError('COUNT_MISMATCH')
    return summary, rows


def has(row, *keys):
    return all(row.get(k) not in (None, '') for k in keys)


def dimensions(row):
    try:
        value = json.loads(row.get('context_dimensions', ''))
        if not isinstance(value, dict): return None
        return json.dumps(value, sort_keys=True, separators=(',', ':'))
    except (ValueError, TypeError):
        return None


def calendar(row):
    dates = [history._date(row.get(k)) for k in ('fiscal_year_start',
        'fiscal_quarter_end_1', 'fiscal_quarter_end_2', 'fiscal_quarter_end_3', 'fiscal_quarter_end_4')]
    q = row.get('fiscal_quarter')
    if not has(row, 'fiscal_calendar_source') or str(q) not in ('1','2','3','4') or any(d is None for d in dates):
        return None
    q = int(q)
    if dates != sorted(set(dates)) or dates[0] >= dates[1]: return None
    if not 330 <= (dates[4]-dates[0]).days+1 <= 400: return None
    if any(not 60 <= (dates[k]-(dates[0] if k==1 else dates[k-1]+timedelta(days=1))).days+1 <=120 for k in range(1,5)):
        return None
    if row['_end'] != dates[q]: return None
    if row.get('duration_kind') == 'ytd' and row['_start'] != dates[0]: return None
    if row.get('duration_kind') == 'standalone' and row['_start'] != (dates[0] if q==1 else dates[q-1]+timedelta(days=1)): return None
    if row.get('duration_kind') not in ('ytd','standalone'): return None
    return tuple(dates), q


def context(row):
    dims = dimensions(row)
    if dims is None or not has(row, 'context_scope', 'accounting_basis', 'context_source'):
        return None
    return row['context_scope'], dims, row['accounting_basis']


def precision(row):
    if not has(row, 'source_decimals', 'precision_source'): return False
    value = str(row['source_decimals'])
    return value == 'INF' or (value.lstrip('-').isdigit() and -30 <= int(value) <= 30)


def revision(row):
    return has(row, 'revision_set_id', 'revision_source') and row.get('revision_status') in ('current_compatible','superseded','conflicting')


def members(value):
    try:
        result = json.loads(value)
        if not isinstance(result, list) or not result or any(not isinstance(x,str) or not x for x in result) or len(set(result)) != len(result):
            return None
        return frozenset(result)
    except (TypeError, ValueError):
        return None


def assess_layer(rows, layer, population, decision, source):
    """Exact counters within caps. No amount arithmetic, no certification."""
    if source['state'] == 'unsupported_schema':
        return {'state': 'unsupported_schema', 'counts': None, 'reason': 'unknown_not_zero'}
    visible = []; row_counts = Counter(); missing = Counter(); by_concept = Counter()
    for original in rows:
        row = dict(original)
        sid = str(row.get('security_id') or '')
        if sid not in population: continue
        concept = row.get('concept') if layer=='raw_sec' else row.get('original_concept_or_field')
        if concept not in CONCEPTS: continue
        row_counts['stored_relevant_rows'] += 1
        available, reason = input_availability(row, layer)
        if reason:
            row_counts['visibility_unproven_rows'] += 1
            missing[reason] += 1
            continue
        if available > decision:
            row_counts['post_decision_rows'] += 1
            continue
        row['_concept'], row['_sid'] = concept, sid
        row['_start'] = history._date(row.get('period_start'))
        row['_end'] = history._date(row.get('period_end') or row.get('instant_date'))
        if row['_end'] and row['_end'] > decision.date():
            row_counts['future_period_rows'] += 1
            continue
        row_counts['visible_relevant_rows'] += 1
        by_concept[concept] += 1
        def proof_visible(kind):
            time=stamp(row.get(kind+'_metadata_available_at'))
            return time is not None and time<=decision
        row['_context'] = context(row) if proof_visible('context') else None
        row['_calendar'] = calendar(row) if concept in (OCF,CAPEX) and proof_visible('fiscal') else None
        row['_revision'] = revision(row) and proof_visible('revision')
        row['_precision'] = precision(row) and proof_visible('precision')
        row['_borrowing_proof_visible'] = proof_visible('borrowing')
        if concept in (OCF,CAPEX) and row['_calendar'] is None: missing['missing_or_invalid_fiscal_metadata_rows'] += 1
        if row['_context'] is None: missing['missing_or_invalid_context_metadata_rows'] += 1
        if not row['_revision']: missing['missing_revision_metadata_rows'] += 1
        if not row['_precision']: missing['unknown_precision_rows'] += 1
        if row['_end'] is None or (concept in (OCF,CAPEX) and (row['_start'] is None or row['_start']>row['_end'])):
            missing['missing_or_invalid_period_rows'] += 1
        # Existing raw SEC USD-unit convention tolerates redundant null currency.
        currency = row.get('currency')
        row['_unit_ok'] = row.get('unit')=='USD' and currency in (None,'','USD')
        scale = row.get('scale')
        # Identity source representations only; no rescaling values is performed.
        row['_scale_ok'] = scale in (None,0,1,'0','1')
        row['_source_ok'] = has(row, 'fact_key','cik','taxonomy','accession_number','source_endpoint') if layer=='raw_sec' else has(row,'evidence_key','source_fact_key','alias_contract_version','accession_or_source_identifier') and row.get('reliability_state')=='usable'
        if not row['_unit_ok'] or not row['_scale_ok']: missing['incompatible_unit_or_scale_rows'] += 1
        if not row.get('_value_signature'): missing['unknown_or_invalid_value_rows'] += 1
        if not row['_source_ok']: missing['missing_source_provenance_rows'] += 1
        if row.get('taxonomy') != 'us-gaap': missing['missing_or_unsupported_taxonomy_rows'] += 1
        if row['_revision'] and row['revision_status']=='superseded':
            row_counts['visible_superseded_rows'] += 1
        visible.append(row)
    row_keys=('stored_relevant_rows','visibility_unproven_rows','post_decision_rows', 'future_period_rows','visible_relevant_rows','visible_superseded_rows')
    missing_keys=('missing_or_invalid_fiscal_metadata_rows','missing_or_invalid_context_metadata_rows',
        'missing_revision_metadata_rows','unknown_precision_rows','missing_or_invalid_period_rows',
        'incompatible_unit_or_scale_rows','unknown_or_invalid_value_rows','missing_source_provenance_rows')
    groups = defaultdict(list)
    for row in visible:
        key = row['_sid'],row['_concept'],row['_start'],row['_end']
        groups[key].append(row)
        if len(groups[key])>MAX_GROUP_ROWS: raise FeasibilityError('GROUP_LIMIT')
    diagnostics = Counter(); blocked = set(); work = 0
    def tick():
        nonlocal work
        work += 1
        if work > MAX_WORK: raise FeasibilityError('WORK_LIMIT')
    for key, group in groups.items():
        tick()
        current = [r for r in group if not (r['_revision'] and r['revision_status']=='superseded')]
        signatures = {r.get('_value_signature') for r in current if r.get('_value_signature')}
        if len(signatures)>1:
            diagnostics['stored_value_disagreement_groups'] += 1
            blocked.add(key)
        if any(r.get('revision_status')=='conflicting' and r['_revision'] for r in current):
            diagnostics['conflicting_revision_groups'] += 1
            blocked.add(key)
        if len(current)>1 and (len(signatures)>1 or not all(r['_revision'] for r in current) or len({r.get('revision_set_id') for r in current})>1):
            diagnostics['unresolved_multiple_revision_groups'] += 1
            blocked.add(key)
    def good(row):
        key=row['_sid'],row['_concept'],row['_start'],row['_end']
        return (key not in blocked and row['_context'] is not None and row['_context'][0]=='consolidated' and row['_revision'] and
            row['revision_status']=='current_compatible' and row['_precision'] and row['_unit_ok'] and
            row['_scale_ok'] and row['_source_ok'] and row.get('_value_signature') is not None and
            (row['_concept']==OCF or row.get('_nonnegative') is True) and
            row.get('taxonomy')=='us-gaap')
    # Count neighboring distinct shapes separately; these never certify fiscal periods.
    shapes=defaultdict(set)
    for row in visible:
        if row['_concept'] in (OCF,CAPEX) and row['_start'] and row['_end'] and 60 <= (row['_end']-row['_start']).days+1 <=120:
            shapes[(row['_sid'],row['_concept'])].add((row['_start'],row['_end']))
    for periods in shapes.values():
        ordered=sorted(periods)
        for previous, following in zip(ordered,ordered[1:]):
            tick()
            delta=(following[0]-previous[1]).days
            diagnostics['shape_gap_pairs' if delta>1 else 'shape_overlap_pairs' if delta<=0 else 'shape_contiguous_pairs'] += 1
    # Period tuples only. No standalone-quarter amounts are constructed.
    quarters=defaultdict(set); ytd=defaultdict(dict); flow=Counter(); direct=set()
    for row in visible:
        if row['_concept'] not in (OCF,CAPEX) or not good(row) or row['_calendar'] is None: continue
        dates,q=row['_calendar']
        key=(row['_sid'],row['_concept'],(row['_context'],row['revision_set_id']))
        if row['duration_kind']=='standalone':
            quarters[key].add((row['_start'],row['_end']))
            direct.add((key,row['_start'],row['_end']))
        else:
            bucket=ytd[(key,dates)]
            if q in bucket and bucket[q].get('_value_signature')!=row.get('_value_signature'):
                raise FeasibilityError('UNEXPECTED_REVISION_AMBIGUITY')
            bucket[q]=row
    for (key,dates), bucket in ytd.items():
        for q,row in sorted(bucket.items()):
            tick()
            start=dates[0] if q==1 else dates[q-1]+timedelta(days=1)
            if q==1:
                quarters[key].add((start,dates[q]));flow['compatible_first_quarter_ytd_candidates'] += 1
            elif q-1 in bucket and row.get('revision_set_id')==bucket[q-1].get('revision_set_id'):
                if row['_concept']==CAPEX:
                    previous=bucket[q-1]
                    if not row.get('_amount_order_rank') or not previous.get('_amount_order_rank'):
                        flow['capex_pair_order_unproven_candidates'] += 1
                        continue
                    if row['_amount_order_rank']<previous['_amount_order_rank']:
                        flow['capex_decreasing_cumulative_pair_count'] += 1
                        continue
                quarters[key].add((start,dates[q]));flow['compatible_cumulative_to_quarter_candidates'] += 1
            else:
                flow['missing_or_incompatible_cumulative_predecessor_candidates'] += 1
    flow['compatible_direct_quarter_period_candidates']=len(direct)
    chains=defaultdict(set)
    for key, periods in quarters.items():
        ordered=sorted(periods)
        for previous,following in zip(ordered,ordered[1:]):
            tick();delta=(following[0]-previous[1]).days
            diagnostics['fiscal_gap_pairs' if delta>1 else 'fiscal_overlap_pairs' if delta<=0 else 'fiscal_contiguous_pairs'] += 1
        by_start=defaultdict(list)
        for start,end in sorted(periods): by_start[start].append((start,end))
        paths={(period,) for period in periods}
        for _ in range(3):
            next_paths=set()
            for path in sorted(paths):
                for following in by_start.get(path[-1][1]+timedelta(days=1),[]):
                    tick();next_paths.add(path+(following,))
            paths=next_paths
        chains[key]=paths
    flow['compatible_ocf_ttm_candidates']=sum(len(v) for (sid,c,ctx),v in chains.items() if c==OCF)
    flow['compatible_capex_ttm_candidates']=sum(len(v) for (sid,c,ctx),v in chains.items() if c==CAPEX)
    flow['compatible_paired_ttm_candidates']=sum(len(v & chains.get((sid,CAPEX,ctx),set())) for (sid,c,ctx),v in chains.items() if c==OCF)
    # Borrowing groups count issuer-date groups, never component combinations/amounts.
    debt=Counter(); instants=defaultdict(list)
    for row in visible:
        if row['_concept'] in REVIEW_DEBT:
            debt['visible_borrowing_component_rows'] += 1
            if row.get('period_start') is None and row['_end'] is not None:
                instants[(row['_sid'],row['_end'])].append(row)
    for key, group in instants.items():
        tick();debt['borrowing_instant_groups'] += 1
        relevant=[r for r in group if r['_concept'] in DEBT and not (r['_revision'] and r['revision_status']=='superseded')]
        # Equivalent repeated component disclosures are not extra obligations.
        all_component_proofs_valid=all(good(r) and has(r,'borrowing_scope_source') and r['_borrowing_proof_visible'] for r in relevant)
        unique={}
        for row in relevant:
            signature=(row['_concept'],row.get('_value_signature'),row['_context'],row.get('revision_set_id'),row.get('component_members'),row.get('borrowing_universe_members'))
            unique.setdefault(signature,row)
        relevant=list(unique.values())
        by=defaultdict(list)
        for row in relevant: by[row['_concept']].append(row)
        if not all(by[c] for c in DEBT): debt['missing_required_component_groups'] += 1
        proofs=[(r,members(r.get('component_members')),members(r.get('borrowing_universe_members'))) for r in relevant]
        base_proof=bool(proofs) and all_component_proofs_valid
        base_proof=base_proof and len({(r['_context'],r.get('revision_set_id')) for r,m,u in proofs})==1
        overlap_proven=base_proof and all(m is not None for r,m,u in proofs)
        completeness_proven=overlap_proven and all(u is not None for r,m,u in proofs) and len({u for r,m,u in proofs})==1
        overlap=None;complete=None
        if not overlap_proven:
            debt['unknown_overlap_groups'] += 1
        else:
            seen=set();overlap=False
            for r,m,u in proofs:
                overlap=overlap or bool(seen & m);seen.update(m)
            debt['proven_overlapping_groups' if overlap else 'proven_disjoint_groups'] += 1
        if not completeness_proven:
            debt['unknown_completeness_groups'] += 1
        else:
            complete=set(seen)==set(proofs[0][2]) and all(by[c] for c in DEBT)
            debt['proven_complete_groups' if complete else 'proven_incomplete_groups'] += 1
        if complete is True and overlap is False:
            debt['compatible_borrowing_total_candidate_groups'] += 1
    cash=Counter()
    for row in visible:
        if row['_concept']!=CASH: continue
        cash['visible_exact_source_cash_rows'] += 1
        if row.get('canonical_field')=='unrestricted_cash': cash['exact_cash_rows_under_unrestricted_cash'] += 1
        if row.get('period_start') is None and row['_end'] is not None and good(row): cash['compatible_direct_cash_candidate_rows'] += 1
        else: cash['unproven_or_incompatible_direct_cash_rows'] += 1
    return {'state': source['state'], 'population_denominator': len(population),
        'row_counts': {k:row_counts[k] for k in row_keys},
        'visible_rows_by_exact_concept': {k:by_concept[k] for k in CONCEPTS},
        'missing_or_unproven_metadata': {k:missing[k] for k in sorted(set(missing_keys)|set(missing))},
        'revision_and_shape_diagnostics': {k:diagnostics[k] for k in ('stored_value_disagreement_groups','conflicting_revision_groups','unresolved_multiple_revision_groups','shape_gap_pairs','shape_overlap_pairs','shape_contiguous_pairs','fiscal_gap_pairs','fiscal_overlap_pairs','fiscal_contiguous_pairs')},
        'flows': {k:flow[k] for k in ('compatible_direct_quarter_period_candidates','compatible_first_quarter_ytd_candidates','compatible_cumulative_to_quarter_candidates','missing_or_incompatible_cumulative_predecessor_candidates','compatible_ocf_ttm_candidates','compatible_capex_ttm_candidates','compatible_paired_ttm_candidates','capex_pair_order_unproven_candidates','capex_decreasing_cumulative_pair_count')},
        'borrowing': {k:debt[k] for k in ('visible_borrowing_component_rows','borrowing_instant_groups','missing_required_component_groups','unknown_overlap_groups','unknown_completeness_groups','proven_overlapping_groups','proven_disjoint_groups','proven_complete_groups','proven_incomplete_groups','compatible_borrowing_total_candidate_groups')},
        'cash': {k:cash[k] for k in ('visible_exact_source_cash_rows','exact_cash_rows_under_unrestricted_cash','compatible_direct_cash_candidate_rows','unproven_or_incompatible_direct_cash_rows')},
        'work_units': work, 'certified_formula_count': 0}


def compact(report):
    for _ in range(8):
        encoded=json.dumps(report,sort_keys=True,separators=(',',':')).encode('utf-8')
        if report.get('compact_utf8_bytes')==len(encoded): break
        report['compact_utf8_bytes']=len(encoded)
    if len(encoded)>MAX_BYTES: raise FeasibilityError('REPORT_LIMIT')
    return report


def assess(*, research_db, production_db, decision_at):
    decision=stamp(decision_at)
    if decision is None or decision>datetime.now(timezone.utc): raise FeasibilityError('DECISION_INVALID')
    paths={'research':Path(research_db),'production':Path(production_db)}
    validate_paths(*paths.values())
    before={}; after={}; failure=False
    executed=datetime.now(timezone.utc)
    try:
        # Attempt independent fingerprints for both inputs even if one fails.
        for name,path in paths.items():
            try: before[name]=history.fingerprint(path)
            except Exception: failure=True
        if failure: raise FeasibilityError('BASELINE_HASH_FAILED')
        data={};sources={};total=0
        for name,path in paths.items():
            data[name]={};sources[name]={}
            with duckdb.connect(str(path),read_only=True,config=history._sql_config()) as db:
                for table in SOURCES:
                    sources[name][table],data[name][table]=read_table(db,table,remaining=MAX_TOTAL_ROWS-total)
                    total+=sources[name][table]['row_count'] or 0
                    if total>MAX_TOTAL_ROWS: raise FeasibilityError('TOTAL_ROW_LIMIT')
        roster=sources['research']['security_classification_evidence']
        needed={'security_id','security_type','public_at','retrieved_at','available_at'}
        if roster['state']=='unsupported_schema' or (roster['row_count'] and not needed<=set(roster['columns'])):
            raise FeasibilityError('ROSTER_SCHEMA_UNPROVEN')
        reconciliation, matched, _=gaps._reconcile(data['research'],decision)
        layers={name:{layer:assess_layer(data[name][table],layer,matched,decision,sources[name][table])
            for layer,table in (('raw_sec','sec_facts'),('canonical','canonical_factor_evidence'))} for name in paths}
        report={'command':'track-b-construction-feasibility','version':VERSION,'status':'proposed_not_authorized',
            'decision_at':decision.isoformat(),'assessment_executed_at':executed.isoformat(),
            'assessment_output_is_historical_evidence':False,'future_persisted_revision_available_at':None,
            'read_only':True,'numeric_source_columns_inspected':True,'amounts_returned':False,'value_signatures_inspected':True,'derived_amounts_computed':False,
            'metadata_shapes_certify_nothing':True,'reconciliation':reconciliation,'sources':sources,'databases':layers,
            'accounting_rules_changed':False,'aliases_activated':False,'consumers_changed':False,
            'derived_evidence_persisted':False,'panel_persisted':False,'model_executed':False,
            'realized_outcome_values_read':False,'provider_requests':0,'repeat_retrieval_recommended':False,
            'contract_selected':None,'sample_thresholds':None,'preregistration_ready':False,
            'approved_eligibility':False,'validation_credit':0,
            'blockers':[{'code':code,'state':'unresolved'} for code in BLOCKERS],
            'unresolved_requirement_count':8, **{key:[] for key in PROHIBITED_ARRAYS},
            'bounds':{'rows_per_table':MAX_ROWS,'total_rows':MAX_TOTAL_ROWS,'cell_characters':MAX_CELL,
                'rows_per_period_group':MAX_GROUP_ROWS,'work_per_layer':MAX_WORK,'report_bytes':MAX_BYTES,
                'sql_memory':'128MB','sql_threads':1,'disk_spill_allowed':False}}
    except duckdb.OutOfMemoryException:
        raise FeasibilityError('SQL_MEMORY_LIMIT') from None
    finally:
        for name,path in paths.items():
            try: after[name]=history.fingerprint(path)
            except Exception: failure=True
        if failure or len(before)!=2 or before!=after: raise FeasibilityError('HASH_VERIFICATION_FAILED') from None
    report['database_fingerprints']={name:{'before':before[name],'after':after[name],'unchanged':True} for name in paths}
    return compact(report)


def main():
    parser=argparse.ArgumentParser(description='Proposed isolated read-only construction feasibility')
    parser.add_argument('--research-db',required=True)
    parser.add_argument('--production-db',required=True)
    parser.add_argument('--decision-at',required=True)
    args=parser.parse_args()
    try:
        report=assess(**vars(args))
    except Exception:
        print(json.dumps({'error':{'code':'TRACK_B_CONSTRUCTION_FEASIBILITY_FAILED'}}),file=__import__('sys').stderr)
        raise SystemExit(1) from None
    print(json.dumps(report,sort_keys=True,separators=(',',':')))


if __name__=='__main__': main()
