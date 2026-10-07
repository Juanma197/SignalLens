"""Bounded metadata inventory. Never reads numerical observations or outcomes.

Structural chains are evidence-planning counterfactuals, not certified factors.
Only explicitly listed base tables and metadata columns may be queried.
"""
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path

import duckdb

from .investment_research import InvestmentResearchError, TRACK_B_LABELS, _utc
from .sec_ingestion import validate_paths
from .sec_capability import fingerprint
from .financial_strength import _within_contract
from .track_b_panel import PROHIBITED_ARRAYS, SPEC_PATH

MAXIMUM_BYTES = 128 * 1024
SAMPLE_LIMIT = 10
MAX_METADATA_ROWS = 500_000
MAX_CHAIN_WORK = 50_000


class InventoryError(InvestmentResearchError):
    reason_code = 'TRACK_B_HISTORY_INVENTORY_FAILED'


# No wildcard projections, numeric value columns, provider payloads, feature
# tables, model tables or outcome tables. Unknown schemas never gain adapters.
SOURCES = {
    'security_master_retrievals': ('retrieval_id', 'retrieved_at', 'source_as_of', 'status'),
    'security_listings': ('retrieval_id', 'security_id', 'qualified_symbol', 'cik', 'active', 'first_seen_at', 'last_seen_at', 'instrument_type'),
    'universe_snapshots': ('snapshot_id', 'snapshot_month', 'snapshot_at'),
    'universe_snapshot_members': ('snapshot_id', 'security_id', 'eligible', 'source_retrieved_at'),
    'sec_issuers': ('security_id', 'cik', 'mapped_at', 'mapping_source'),
    'issuer_mapping_candidates': ('security_id', 'cik', 'effective_from', 'effective_to', 'observed_at', 'review_status', 'conflict_state', 'ticker_reuse_protected'),
    'security_classification_evidence': ('security_id', 'security_type', 'effective_from', 'effective_to', 'public_at', 'retrieved_at', 'available_at'),
    'sec_filings': ('cik', 'accession_number', 'filed_date', 'public_at', 'retrieved_at', 'form'),
    'sec_facts': ('security_id', 'cik', 'concept', 'taxonomy', 'unit', 'currency', 'period_start', 'period_end', 'public_at', 'retrieved_at', 'accession_number', 'source_endpoint'),
    'canonical_factor_evidence': ('security_id', 'canonical_field', 'original_concept_or_field', 'unit', 'currency', 'period_start', 'period_end', 'instant_date', 'public_at', 'retrieved_at', 'available_at', 'materialized_at', 'reliability_state', 'alias_contract_version', 'accession_or_source_identifier', 'source_fact_key', 'materialization_run_id'),
    'liquidity_canonical_materialization_revisions': ('security_id', 'canonical_field', 'period_end', 'public_at', 'retrieved_at', 'available_at', 'materialized_at'),
    'research_filing_reports': ('security_id', 'period_start', 'period_end', 'published_at', 'retrieved_at', 'available_at', 'accounting_standard'),
    'research_raw_fundamental_facts': ('security_id', 'source_concept', 'unit', 'currency', 'period_start', 'period_end', 'published_at', 'retrieved_at', 'available_at', 'state'),
    'research_normalized_fundamentals': ('security_id', 'metric', 'unit', 'currency', 'period_start', 'period_end', 'retrieved_at', 'available_at', 'mapping_version', 'state'),
    'global_price_observations': ('qualified_symbol', 'exchange', 'trading_date', 'currency', 'status', 'retrieved_at', 'source'),
    'global_exchange_sessions': ('exchange', 'session_date', 'is_open', 'source', 'retrieved_at'),
    'global_corporate_actions': ('qualified_symbol', 'ex_date', 'action_type', 'currency', 'source', 'retrieved_at'),
    'corporate_action_coverage_evidence': ('security_id', 'coverage_state', 'assessed_from', 'assessed_to', 'public_at', 'retrieved_at', 'available_at'),
    'fundamental_facts': ('ticker', 'metric', 'taxonomy', 'concept', 'unit', 'period_start', 'period_end', 'filed_at', 'available_at', 'retrieved_at'),
    'fundamental_fetches': ('ticker', 'requested_at', 'completed_at', 'status'),
    'security_universe': ('ticker', 'active'),
    'price_bars': ('ticker', 'trading_date', 'ingested_at', 'source'),
}
DATE_COLUMNS = frozenset(('retrieved_at', 'source_as_of', 'first_seen_at', 'last_seen_at', 'snapshot_month', 'snapshot_at', 'source_retrieved_at', 'mapped_at', 'effective_from', 'effective_to', 'observed_at', 'public_at', 'available_at', 'filed_date', 'period_start', 'period_end', 'instant_date', 'materialized_at', 'published_at', 'trading_date', 'session_date', 'ex_date', 'assessed_from', 'assessed_to', 'filed_at', 'requested_at', 'completed_at', 'ingested_at'))
FIELDS = {
    'operating_cash_flow': ('duration', 'USD', ('NetCashProvidedByUsedInOperatingActivities',)),
    'capital_expenditure': ('duration', 'USD', ('PaymentsToAcquirePropertyPlantAndEquipment',)),
    'net_income': ('duration', 'USD', ('NetIncomeLoss',)),
    'assets': ('instant', 'USD', ('Assets',)),
    'revenue': ('duration', 'USD', ('RevenueFromContractWithCustomerExcludingAssessedTax', 'Revenues')),
    'diluted_shares': ('duration', 'shares', ('WeightedAverageNumberOfDilutedSharesOutstanding',)),
    'current_debt': ('instant', 'USD', ('ShortTermBorrowings',)),
    'non_current_debt': ('instant', 'USD', ('LongTermDebtNoncurrent',)),
    'cash_and_cash_equivalents': ('instant', 'USD', ('CashAndCashEquivalentsAtCarryingValue',)),
}
CONCEPT_FIELDS = {c: f for f, (_, _, concepts) in FIELDS.items() for c in concepts}
ROW_STATES = ('incompatible_evidence', 'unverified_provenance', 'post_boundary', 'metadata_compatible_unverified')
FAMILY_FIELDS = {
    'value': ('operating_cash_flow', 'capital_expenditure'),
    'business_quality': ('net_income', 'assets'),
    'financial_strength': ('operating_cash_flow', 'current_debt', 'non_current_debt', 'cash_and_cash_equivalents'),
    'growth': ('revenue',), 'shareholder_treatment': ('diluted_shares',),
}


def _stamp(value):
    if not isinstance(value, datetime):
        try: value = datetime.fromisoformat(str(value))
        except (ValueError, TypeError): return None
    if value.tzinfo is None or value.utcoffset() is None: return None
    return value.astimezone(timezone.utc)


def _date(value):
    if isinstance(value, datetime): return value.date()
    if isinstance(value, date): return value
    try: return date.fromisoformat(str(value))
    except (ValueError, TypeError): return None


def _market_stamp(value):
    # global_market_data persists utc_naive() timestamps. This explicit adapter
    # convention must never be applied to canonical accounting evidence.
    if isinstance(value, datetime) and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return _stamp(value)


def _bounded(items):
    return {'items': items[:SAMPLE_LIMIT], 'total_count': len(items),
            'returned_count': min(len(items), SAMPLE_LIMIT), 'sample_limit': SAMPLE_LIMIT,
            'truncated': len(items) > SAMPLE_LIMIT}


def _read(db, table):
    schema = db.execute('''SELECT column_name FROM information_schema.columns
        WHERE table_catalog=current_database() AND table_schema='main' AND table_name=?
        ORDER BY ordinal_position''', [table]).fetchall()
    base = db.execute('''SELECT table_type FROM information_schema.tables
        WHERE table_catalog=current_database() AND table_schema='main' AND table_name=?''', [table]).fetchone()
    if base is None: return {'state': 'absent_evidence', 'reason': 'supported_table_not_persisted', 'row_count': 0, 'columns': [], 'date_ranges': {}}, []
    if base[0] != 'BASE TABLE':
        return {'state': 'incompatible_evidence', 'reason': 'views_not_evaluated', 'row_count': None, 'columns': [], 'date_ranges': {}}, []
    columns = [c for c in SOURCES[table] if c in {r[0] for r in schema}]
    count = db.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0]
    if count > MAX_METADATA_ROWS: raise InventoryError('metadata work bound exceeded')
    ranges = {}
    for c in columns:
        if c not in DATE_COLUMNS: continue
        lo, hi, invalid, missing = db.execute(f'''SELECT min(try_cast("{c}" AS TIMESTAMP)),
            max(try_cast("{c}" AS TIMESTAMP)), count(*) FILTER (WHERE "{c}" IS NOT NULL
            AND try_cast("{c}" AS TIMESTAMP) IS NULL), count(*) FILTER (WHERE "{c}" IS NULL)
            FROM "{table}"''').fetchone()
        ranges[c] = {'first': str(lo) if lo else None, 'last': str(hi) if hi else None,
                     'invalid_count': invalid, 'missing_count': missing}
    summary = {'state': 'absent_evidence' if not count else 'unverified_provenance',
               'reason': 'persisted_table_empty' if not count else 'metadata_presence_not_provenance_certification',
               'row_count': count, 'columns': columns, 'date_ranges': ranges,
               'missing_adapter_columns': sorted(set(SOURCES[table]) - set(columns))}
    if not columns:
        if count: summary.update(state='incompatible_evidence', reason='no_supported_metadata_columns')
        return summary, []
    projection = ','.join(f'"{c}"' for c in columns)
    # Exact complete metadata within the explicit work cap; output never exposes
    # stored endpoint/source strings or raw identifiers. Ordering is in Python.
    cursor = db.execute(f'SELECT {projection} FROM "{table}" LIMIT {MAX_METADATA_ROWS + 1}')
    rows = []
    while batch := cursor.fetchmany(2048): rows.extend(dict(zip(columns, r)) for r in batch)
    if len(rows) != count: raise InventoryError('metadata count mismatch')
    return summary, rows


def _accounting_state(row, field, layer, decision):
    nature, unit, concepts = FIELDS[field]
    end = _date(row.get('period_end') or row.get('instant_date'))
    start = _date(row.get('period_start'))
    concept = row.get('concept') if layer == 'raw_sec' else row.get('original_concept_or_field')
    if not concept: return 'unverified_provenance', 'missing_exact_concept'
    if concept not in concepts: return 'incompatible_evidence', 'concept_mismatch'
    if not row.get('unit') or (unit == 'USD' and not row.get('currency')):
        return 'unverified_provenance', 'missing_unit_or_currency'
    if row.get('unit') not in ({unit, 'monetary'} if unit == 'USD' else {unit}):
        return 'incompatible_evidence', 'unit_mismatch'
    if unit == 'USD' and row.get('currency') != 'USD': return 'incompatible_evidence', 'currency_mismatch'
    if not end or (nature == 'duration' and not start):
        return 'unverified_provenance', 'missing_comparable_period_metadata'
    if (start and start > end) or (nature == 'instant' and row.get('period_start') is not None):
        return 'incompatible_evidence', 'period_mismatch'
    if layer == 'canonical' and row.get('reliability_state') is None:
        return 'unverified_provenance', 'missing_canonical_usability_declaration'
    if layer == 'canonical' and row.get('reliability_state') != 'usable':
        return 'incompatible_evidence', 'canonical_not_usable'
    public, retrieved = (_stamp(row.get(k)) for k in ('public_at', 'retrieved_at'))
    if not public or not retrieved: return 'unverified_provenance', 'missing_aware_source_timestamps'
    expected = max(public, retrieved)
    if layer == 'canonical':
        if row.get('materialization_run_id'):
            materialized = _stamp(row.get('materialized_at'))
            if not materialized: return 'unverified_provenance', 'missing_materialization_timestamp'
            expected = max(expected, materialized)
        available = _stamp(row.get('available_at'))
        if available != expected: return 'unverified_provenance', 'availability_rule_unverified'
        if not row.get('alias_contract_version') or not row.get('accession_or_source_identifier'):
            return 'unverified_provenance', 'missing_contract_or_source_reference'
    elif row.get('taxonomy') != 'us-gaap' or not row.get('accession_number') or not row.get('source_endpoint') or not row.get('cik'):
        return 'unverified_provenance', 'missing_raw_source_reference'
    if expected > decision: return 'post_boundary', 'not_available_at_decision'
    if end > decision.date(): return 'incompatible_evidence', 'future_accounting_period'
    if not row.get('security_id'): return 'unverified_provenance', 'missing_security_identity'
    return 'metadata_compatible_unverified', 'lineage_identity_and_values_not_certified'


def _chains(periods, length, annual=False):
    """Exact contiguous standalone durations; never infer quarters from YTD."""
    low, high = (330, 400) if annual else (60, 120)
    valid = sorted({(s, e) for s, e in periods if s and low <= (e-s).days+1 <= high})
    by_end = {e: [] for _, e in valid}
    for s, e in valid: by_end[e].append(s)
    paths = set(valid); work = len(paths)
    if work > MAX_CHAIN_WORK: raise InventoryError('chain work bound exceeded')
    for _ in range(1, length):
        following = set()
        for start, end in sorted(paths):
            for previous in by_end.get(start-timedelta(days=1), []):
                work += 1
                if work > MAX_CHAIN_WORK: raise InventoryError('chain work bound exceeded')
                following.add((previous, end))
        paths = following
        if not paths: break
    return sorted(paths)


def _families(rows, layer, population, decision, source):
    keys = {'security_id', 'concept' if layer == 'raw_sec' else 'canonical_field'}
    unsupported = source['state'] == 'incompatible_evidence' or (
        bool(source['row_count']) and not keys <= set(source['columns']))
    periods = defaultdict(lambda: defaultdict(set))
    statuses = {f: Counter() for f in FIELDS}
    per_security_states = defaultdict(lambda: defaultdict(Counter))
    availability = {f: [] for f in FIELDS}
    duration_types = {f: Counter() for f in FIELDS}
    reasons = Counter()
    seen = defaultdict(set)
    for row in rows:
        field = CONCEPT_FIELDS.get(row.get('concept')) if layer == 'raw_sec' else row.get('canonical_field')
        if field not in FIELDS: continue
        state, reason = _accounting_state(row, field, layer, decision)
        statuses[field][state] += 1; reasons[reason] += 1
        sid = str(row.get('security_id') or '')
        seen[field].add(sid)
        per_security_states[sid][field][state] += 1
        if state == 'metadata_compatible_unverified':
            start, end = _date(row.get('period_start')), _date(row.get('period_end') or row.get('instant_date'))
            periods[sid][field].add((start, end))
            availability[field].append((end, _stamp(row['public_at']), _stamp(row['retrieved_at'])))
            days = (end-start).days+1 if start else 0
            duration_types[field]['instant' if not start else 'standalone_quarter_candidate' if 60 <= days <= 120
                else 'annual_duration_candidate' if 330 <= days <= 400 else 'other_duration_not_chained'] += 1
    result = {}
    for family in ('value', 'business_quality', 'financial_strength', 'growth', 'shareholder_treatment'):
        samples = []; count = 0; chain_count = 0
        gaps = Counter()
        for sid in sorted(population):
            p = periods[sid]
            if family == 'value':
                chains = _chains(p['operating_cash_flow'] & p['capital_expenditure'], 4)
            elif family == 'business_quality':
                instants = {e for _, e in p['assets']}
                chains = [(s, e) for s, e in _chains(p['net_income'], 4)
                          if s-timedelta(days=1) in instants and e in instants]
            elif family == 'financial_strength':
                ends = set.intersection(*({e for _, e in p[f]} for f in
                    ('current_debt', 'non_current_debt', 'cash_and_cash_equivalents')))
                chains = [(s, e) for s, e in _chains(p['operating_cash_flow'], 4) if e in ends]
            elif family == 'growth': chains = _chains(p['revenue'], 8)
            else: chains = _chains(p['diluted_shares'], 2, annual=True)
            if chains:
                gaps['structural_chain_present_unverified'] += 1
                count += 1; chain_count += len(chains)
                if len(samples) < SAMPLE_LIMIT:
                    samples.append({'security_identity_sha256': hashlib.sha256(sid.encode()).hexdigest(),
                        'chain_count': len(chains), 'earliest_start': str(min(s for s, _ in chains)),
                        'latest_end': str(max(e for _, e in chains))})
            else:
                required = FAMILY_FIELDS[family]
                unavailable = [f for f in required if not p[f]]
                if unsupported: gap = 'incompatible_source_schema'
                elif any(sid not in seen[f] for f in required): gap = 'absent_input_evidence'
                elif any(set(per_security_states[sid][f]) == {'incompatible_evidence'} for f in unavailable):
                    gap = 'incompatible_only_input_evidence'
                elif any(per_security_states[sid][f]['unverified_provenance'] for f in unavailable):
                    gap = 'unverified_input_provenance'
                elif unavailable: gap = 'post_boundary_or_incompatible_inputs'
                else: gap = 'incomplete_or_unaligned_period_chain'
                gaps[gap] += 1
        result[family] = {'population_denominator': len(population),
            'securities_with_structural_chain': count, 'structural_chain_count': chain_count,
            'securities_without_structural_chain': len(population)-count,
            'primary_gap_counts': {g: gaps[g] for g in ('structural_chain_present_unverified',
                'absent_input_evidence', 'incompatible_only_input_evidence', 'unverified_input_provenance',
                'post_boundary_or_incompatible_inputs', 'incomplete_or_unaligned_period_chain', 'incompatible_source_schema')},
            'gap_precedence': 'absent, incompatible-only, unverified, boundary, incomplete/unaligned; one primary gap per security',
            'certified_formula_count': 0,
            'samples': {'items': samples, 'total_count': count, 'returned_count': len(samples),
                        'sample_limit': SAMPLE_LIMIT, 'truncated': count > len(samples)}}
    return {'evidence_layer': layer,
        'adapter_state': 'incompatible_evidence' if unsupported else source['state'],
        'unmapped_metadata_observation_count': sum(
            (CONCEPT_FIELDS.get(r.get('concept')) if layer == 'raw_sec' else r.get('canonical_field')) not in FIELDS for r in rows),
        'fields': {f: {
        'observation_states': {s: statuses[f][s] for s in ROW_STATES},
        'absent_security_count': None if unsupported else len(population - seen[f]),
        'visible_metadata_duration_counts': dict(sorted(duration_types[f].items())),
        'visible_metadata_period_end_range': {
            'first': str(min(e for e, _, _ in availability[f])) if availability[f] else None,
            'last': str(max(e for e, _, _ in availability[f])) if availability[f] else None},
        'public_age_days_range': {
            'minimum': min((decision-p).days for _, p, _ in availability[f]) if availability[f] else None,
            'maximum': max((decision-p).days for _, p, _ in availability[f]) if availability[f] else None},
        'observation_count': sum(statuses[f].values())} for f in FIELDS},
        'observation_reasons': dict(sorted(reasons.items())), 'families': result,
        'interpretation': 'Metadata-only structural chains; no value, denominator, lineage, identity, staleness or accounting certification. Raw SEC chains are not canonical evidence. No financial-strength contract selected.'}


def _identity(data, decision):
    listing = data['security_listings']
    retrievals = data['security_master_retrievals']
    mappings = data['issuer_mapping_candidates']
    states = Counter(); pairs = defaultdict(set)
    for r in mappings:
        start, end, observed = (_stamp(r.get(k)) for k in ('effective_from', 'effective_to', 'observed_at'))
        if not r.get('security_id') or not r.get('cik') or not start or not observed:
            states['unverified_provenance'] += 1
        elif (r.get('effective_to') is not None and not end) or (end and end <= start) or r.get('conflict_state') not in (None, 'none'):
            states['incompatible_evidence'] += 1
        elif observed > decision or start > decision or (end and decision >= end):
            states['not_effective_at_boundary'] += 1
        elif r.get('review_status') != 'approved' or r.get('conflict_state') is None or r.get('ticker_reuse_protected') is not True:
            states['unverified_provenance'] += 1
        else:
            states['declared_approved_effective_unverified'] += 1
            pairs[str(r['security_id'])].add(str(r['cik']))
    actions = data['global_corporate_actions']
    delist = sum(r.get('action_type') in ('delisting', 'delisted') for r in actions)
    classification = Counter()
    for row in data['security_classification_evidence']:
        start, end, public, retrieved, available = (_stamp(row.get(k)) for k in
            ('effective_from', 'effective_to', 'public_at', 'retrieved_at', 'available_at'))
        if not start or not public or not retrieved or not available or available != max(public,retrieved):
            classification['unverified_provenance'] += 1
        elif row.get('effective_to') is not None and (not end or end <= start):
            classification['incompatible_evidence'] += 1
        elif available > decision or start > decision or (end and decision >= end):
            classification['not_effective_at_boundary'] += 1
        else: classification['effective_metadata_unverified'] += 1
    return {
        'listing_row_count': len(listing),
        'distinct_listing_security_count': len({r['security_id'] for r in listing if r.get('security_id')}),
        'inactive_listing_row_count': sum(r.get('active') is False for r in listing),
        'completed_catalogue_retrieval_count': sum(r.get('status') == 'completed' for r in retrievals),
        'membership_snapshot_row_count': len(data['universe_snapshot_members']),
        'interpretation': 'Persisted retrieval/snapshot membership only; inactive or disappeared listings do not establish delistings. first_seen/last_seen and mapped_at are not effective membership/CIK intervals.',
        'effective_mapping_rows': dict(sorted(states.items())),
        'security_ids_with_multiple_declared_effective_ciks': sum(len(v)>1 for v in pairs.values()),
        'legacy_sec_mapping_row_count': len(data['sec_issuers']),
        'classification_interval_states': dict(sorted(classification.items())),
        'delisting_action_metadata_row_count': delist,
        'historical_membership_certified': False, 'effective_identity_certified': False,
        'gap': 'Operator must approve identity/effective interval semantics; separately propose historical membership, delisting and mapping acquisition where missing. Mapping candidates and metadata declarations do not certify provenance.'}


def _market(data, decision, action_source):
    actions = data['global_corporate_actions']
    types = {'dividends': ('cash_distribution', 'dividend', 'cash_dividend'),
             'splits': ('split', 'stock_split'), 'mergers_spinoffs': ('merger', 'spinoff'),
             'delistings': ('delisting', 'delisted'), 'delisting_proceeds': ('delisting_proceeds',)}
    result = {}
    for category, names in types.items():
        relevant = [r for r in actions if r.get('action_type') in names]
        unsupported = action_source['state'] == 'incompatible_evidence' or (
            action_source['row_count'] and 'action_type' not in action_source['columns'])
        result[category] = {'metadata_row_count': None if unsupported else len(relevant),
            'state': 'incompatible_evidence' if unsupported else 'unverified_provenance' if relevant else 'absent_evidence',
            'retrieved_by_boundary_count': None if unsupported else sum(bool(_market_stamp(r.get('retrieved_at')) and
                _market_stamp(r['retrieved_at']) <= decision) for r in relevant),
            'completeness_certified': False}
    result['other_action_metadata_row_count'] = sum(r.get('action_type') not in
        {x for names in types.values() for x in names} for r in actions)
    sessions = defaultdict(set); price_states = Counter()
    for r in data['global_price_observations']:
        day, retrieved = _date(r.get('trading_date')), _market_stamp(r.get('retrieved_at'))
        if not day or r.get('status') not in (None, 'available'): price_states['incompatible_evidence'] += 1
        elif not retrieved or not r.get('qualified_symbol'): price_states['unverified_provenance'] += 1
        elif day > decision.date() or retrieved > decision: price_states['post_boundary'] += 1
        else:
            price_states['metadata_compatible_unverified'] += 1
            sessions[str(r.get('qualified_symbol'))].add(day)
    result['price_and_risk'] = {'symbols_with_253_date_metadata_rows': sum(len(v)>=253 for v in sessions.values()),
        'metadata_observation_states': {s: price_states[s] for s in ROW_STATES},
        'certified_formula_count': 0, 'interpretation': 'Distinct dates only; exact exchange-session continuity, identity, price positivity, actions and total returns are unverified. Numerical price/outcome values were not read.'}
    result['market_timestamp_convention'] = 'global_market_data utc_naive storage is interpreted as UTC for retrieval metadata only; public availability and source provenance remain unverified.'
    result['benchmark'] = {'state': 'unverified_provenance', 'approved_benchmark': None,
        'reason': 'No approved source/instrument/version contract or supported persisted benchmark metadata adapter. Ordinary price rows cannot prove benchmark total-return availability; unknown tables are not evaluated.'}
    result['corporate_action_coverage_metadata_row_count'] = len(data['corporate_action_coverage_evidence'])
    return result


def inventory(*, research_db, production_db, decision_at):
    research, production = Path(research_db), Path(production_db)
    decision = _utc(decision_at)
    validate_paths(research, production)
    before = {name: fingerprint(path) for name, path in (('research', research), ('production', production))}
    result = None
    try:
        sources = []; datasets = {}; schema_metadata = {}
        for name, path in (('research', research), ('production', production)):
            datasets[name] = {}
            with duckdb.connect(str(path), read_only=True) as db:
                base_tables = {r[0] for r in db.execute('''SELECT table_name FROM information_schema.tables
                    WHERE table_catalog=current_database() AND table_schema='main' AND table_type='BASE TABLE' ''').fetchall()}
                schema_metadata[name] = {'base_table_count': len(base_tables),
                    'supported_base_table_count': len(base_tables & set(SOURCES)),
                    'uninspected_base_table_count': len(base_tables - set(SOURCES)),
                    'scope': 'Only fixed adapters inspected; unknown and model/outcome tables are not evaluated.'}
                for table in SOURCES:
                    summary, rows = _read(db, table)
                    sources.append({'database': name, 'table': table, **summary})
                    datasets[name][table] = rows
        # Never combine raw/canonical or research/production counts: they may be
        # duplicate replicas and have distinct availability/provenance histories.
        databases = {}
        for name, data in datasets.items():
            source_by_table = {s['table']: s for s in sources if s['database']==name}
            population = {str(r['security_id']) for table in ('security_listings', 'universe_snapshot_members',
                'sec_issuers', 'canonical_factor_evidence', 'sec_facts') for r in data[table] if r.get('security_id')}
            databases[name] = {'identity': _identity(data, decision),
                'canonical_history': _families(data['canonical_factor_evidence'], 'canonical', population, decision, source_by_table['canonical_factor_evidence']),
                'raw_sec_history': _families(data['sec_facts'], 'raw_sec', population, decision, source_by_table['sec_facts']),
                'market_metadata': _market(data, decision, source_by_table['global_corporate_actions']),
                'denominator': 'Union of persisted security IDs across listings, snapshot members, SEC mappings, canonical evidence and SEC facts; not an approved historical universe.'}
        spec = json.loads(SPEC_PATH.read_text(encoding='utf-8'))
        result = {'command': 'track-b-historical-evidence-inventory', 'version': 'track-b-history-inventory-1.0.0',
            'specification': {'version': spec['version'], 'status': spec['status'],
                'sha256': hashlib.sha256(json.dumps(spec, sort_keys=True, separators=(',', ':')).encode()).hexdigest()},
            'decision_at': decision.isoformat(), 'labels': TRACK_B_LABELS, 'read_only': True,
            'metadata_only': True, 'realized_outcome_values_read': False,
            'sources': sources, 'schema_metadata': schema_metadata, 'databases': databases,
            'state_definitions': {
                'absent_evidence': 'No persisted table/rows, or no rows for the specified field/security; absence is confined to supported adapters.',
                'incompatible_evidence': 'Rows exist but units/concepts/periods/schema or declared usability contradict the draft structural rule.',
                'unverified_provenance': 'Missing/inconsistent provenance timestamps/references, unsupported adapter, or lineage/identity not independently certified.',
                'post_boundary': 'Structurally compatible metadata with availability later than decision; not absent.',
                'metadata_compatible_unverified': 'Metadata passes draft structure/timestamp checks; source lineage, identity and numerical validity remain unverified.'},
            'operator_decisions': ['accounting perimeter and contract (A-D unselected)', 'historical universe and identity rules',
                'sample policy/minima (unset)', 'period alignment/staleness/materiality rules', 'outcomes and benchmark',
                'execution costs', 'temporal splits/inference', 'registration and holdout controls'],
            'separately_proposed_acquisition': ['historical membership/delistings/effective identity where missing',
                'comparable accounting history and reliable availability/source lineage where missing',
                'corporate-action/dividend/delisting-proceeds and benchmark metadata where missing',
                'execution-cost evidence and holdout-access records where missing'],
            'acquisition_authorized': False, 'contract_selected': None, 'sample_thresholds': None,
            'blockers': [{'code': c, 'state': 'unresolved'} for c in spec['preregistration_blockers']],
            'unresolved_requirement_count': len(spec['preregistration_blockers']), 'preregistration_ready': False,
            'panel_persisted': False, 'model_executed': False, 'validation_credit': 0,
            **{key: [] for key in PROHIBITED_ARRAYS},
            'bounds': {'maximum_compact_utf8_bytes': MAXIMUM_BYTES, 'security_sample_limit': SAMPLE_LIMIT,
                'maximum_metadata_rows_per_table': MAX_METADATA_ROWS, 'source_count': len(SOURCES)*2,
                'maximum_chain_work_per_call': MAX_CHAIN_WORK,
                'unsupported_tables_read': False}}
    finally:
        after = {name: fingerprint(path) for name, path in (('research', research), ('production', production))}
        if before != after: raise InventoryError('database changed during inventory')
    result['database_fingerprints'] = {k: {'before': before[k], 'after': after[k], 'unchanged': True} for k in before}
    return _within_contract(result, MAXIMUM_BYTES)
