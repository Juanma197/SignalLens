"""Bounded reads of existing stored evidence; no provider or persistence path.

Prototype candidates never pass through a Track B report or Track A vintage.
Approved effective mapping evidence is required; catalogue observation dates are
not substituted for effective listing intervals. All membership remains proposed.
"""
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
import re

import duckdb
import pandas as pd

from ..active_catalogue import select_active_catalogue
from ..comparable_universe import classify_security
from ..global_research import momentum_features
from ..model_readiness import fingerprint
from ..price_segments import detect_price_segments
from ..research_observations import ObservationPolicy
from ..sec_ingestion import validate_paths
from ..track_b_gaps import _reconcile

CONFIG = json.loads(Path(__file__).with_name('config_v1.json').read_text(encoding='utf-8'))
NOTICE = 'UNVALIDATED RESEARCH PROTOTYPE — ZERO VALIDATION CREDIT'
MAX_ROWS = 500_000
MAX_ROSTER = 256
MAX_CELL = 1024
MAX_OUTPUT = 2_000_000
MAX_FILE_BYTES = 1_000_000_000
FACTS = {
    'CashAndCashEquivalentsAtCarryingValue': ('cash_and_cash_equivalents', 'instant'),
    'Assets': ('assets', 'instant'),
    'StockholdersEquity': ('shareholders_equity', 'instant'),
    'Revenues': ('revenue', 'duration'),
    'RevenueFromContractWithCustomerExcludingAssessedTax': ('revenue', 'duration'),
    'NetIncomeLoss': ('net_income', 'duration'),
    'OperatingIncomeLoss': ('operating_income', 'duration'),
}
# Fixed projections exclude payloads, plan capabilities, SQL, and unrelated data.
COLUMNS = {
    'security_master_retrievals': 'retrieval_id retrieved_at status',
    'security_listings': 'retrieval_id security_id qualified_symbol company_name primary_exchange currency instrument_type active cik',
    'universe_snapshot_members': 'security_id',
    'sec_issuers': 'security_id qualified_symbol cik mapping_source mapped_at',
    'security_classification_evidence': 'evidence_key security_id qualified_symbol security_type durable_identifier evidence_source_family source_record_identifier cik effective_from effective_to public_at retrieved_at available_at confidence_category review_required is_current conflict_details',
    'issuer_mapping_candidates': 'candidate_key security_id qualified_symbol cik evidence_source source_identifier effective_from effective_to review_status conflict_state ticker_reuse_protected observed_at',
    'sec_facts': 'fact_key security_id qualified_symbol cik taxonomy concept value unit currency period_start period_end form accession_number public_at retrieved_at source_endpoint',
    'corporate_action_coverage_evidence': 'evidence_key security_id qualified_symbol coverage_state assessed_from assessed_to source_identifier public_at retrieved_at available_at',
    'global_exchange_sessions': 'exchange session_date is_open source retrieved_at',
    'global_price_observations': 'qualified_symbol trading_date exchange currency open high low close adjusted_close volume status source retrieved_at',
    'global_corporate_actions': 'qualified_symbol ex_date action_type value currency source retrieved_at',
}


class PrototypeError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def stamp(value):
    """Legacy market TIMESTAMP columns are explicitly UTC by their contract."""
    if isinstance(value, str):
        try: value = datetime.fromisoformat(value.replace('Z', '+00:00'))
        except ValueError: return None
    if not isinstance(value, datetime): return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def day(value):
    if isinstance(value, datetime): return value.date()
    if isinstance(value, date): return value
    return None


def visible(row, decision):
    public, retrieved, available = (stamp(row.get(k)) for k in ('public_at', 'retrieved_at', 'available_at'))
    return bool(public and retrieved and available and available == max(public, retrieved) and available <= decision)


def cik(value):
    value = str(value or '')
    return value.zfill(10) if value.isascii() and value.isdigit() and 1 <= len(value) <= 10 and int(value) > 0 else None


def finite(value):
    try: return math.isfinite(float(value))
    except (TypeError, ValueError, OverflowError): return False


def _read(db, table, remaining, decision):
    base = db.execute("SELECT table_type FROM information_schema.tables WHERE table_catalog=current_database() AND table_schema='main' AND table_name=?", [table]).fetchone()
    if not base: return [], 'table_absent'
    if base[0] != 'BASE TABLE': return [], 'unsupported_schema'
    actual = {r[0] for r in db.execute("SELECT column_name FROM information_schema.columns WHERE table_catalog=current_database() AND table_schema='main' AND table_name=?", [table]).fetchall()}
    columns = [c for c in COLUMNS[table].split() if c in actual]
    # Missing projected fields remain None and are reported by the domain checks.
    if not columns: return [], 'unsupported_schema'
    where, args = '', []
    if table in ('global_price_observations', 'global_exchange_sessions', 'global_corporate_actions'):
        field = {'global_price_observations': 'trading_date', 'global_exchange_sessions': 'session_date', 'global_corporate_actions': 'ex_date'}[table]
        if field not in actual: return [], 'unsupported_schema'
        where = f' WHERE "{field}" BETWEEN ? AND ?'
        args = [decision.date() - timedelta(days=450), decision.date()]
    count = db.execute(f'SELECT count(*) FROM "{table}"{where}', args).fetchone()[0]
    if count > remaining: raise PrototypeError('PROTOTYPE_ROW_LIMIT')
    checks = ' OR '.join(f'length(CAST("{c}" AS VARCHAR))>{MAX_CELL}' for c in columns)
    conjunction = ' AND ' if where else ' WHERE '
    if db.execute(f'SELECT count(*) FROM "{table}"{where}{conjunction}({checks})', args).fetchone()[0]:
        raise PrototypeError('PROTOTYPE_CELL_LIMIT')
    cursor = db.execute(f'SELECT {",".join(chr(34)+c+chr(34) for c in columns)} FROM "{table}"{where} LIMIT {MAX_ROWS+1}', args)
    rows = [dict(zip(columns, r)) for r in cursor.fetchall()]
    if len(rows) != count: raise PrototypeError('PROTOTYPE_COUNT_MISMATCH')
    for row in rows:
        if 'conflict_details' in row and isinstance(row['conflict_details'], str):
            try: row['conflict_details'] = json.loads(row['conflict_details'])
            except ValueError: row['conflict_details'] = {'invalid': True}
    return rows, 'supported' if set(COLUMNS[table].split()) <= actual else 'missing_columns'


def _identity(sec, data, decision, matched, window_start):
    sid, symbol = sec['security_id'], sec['qualified_symbol']
    reasons = []
    if sid not in matched: reasons.append('durable_id_not_matched_or_conflicting')
    classes = [r for r in data['security_classification_evidence'] if str(r.get('security_id')) == sid]
    classification = classify_security(classes, decision, security_id=sid)
    if not classification.included: reasons.append(classification.reason_code)
    if any(r.get('review_required') is True for r in classes if visible(r, decision)):
        reasons.append('classification_review_required')
    if not any(visible(r, decision) and r.get('source_record_identifier') for r in classes):
        reasons.append('classification_provenance_unproven')
    mappings = [r for r in data['issuer_mapping_candidates'] if str(r.get('security_id')) == sid and stamp(r.get('observed_at')) and stamp(r['observed_at']) <= decision]
    approved = []
    for r in mappings:
        start, end = stamp(r.get('effective_from')), stamp(r.get('effective_to'))
        if (start and start <= datetime.combine(window_start, time.min, timezone.utc)
            and (r.get('effective_to') is None or (end and decision < end))
            and r.get('review_status') == 'approved' and r.get('conflict_state') == 'none'
            and r.get('ticker_reuse_protected') is True and r.get('qualified_symbol') == symbol
            and r.get('evidence_source') and r.get('source_identifier') and cik(r.get('cik'))):
            approved.append(r)
    if len(approved) != 1: reasons.append('effective_listing_interval_unproven_or_ambiguous')
    if any(r.get('conflict_state') not in ('none', None) for r in mappings):
        reasons.append('issuer_mapping_conflict')
    issuers = [r for r in data['sec_issuers'] if str(r.get('security_id')) == sid and stamp(r.get('mapped_at')) and stamp(r['mapped_at']) <= decision]
    values = {cik(r.get('cik')) for r in issuers}
    expected = cik(sec.get('cik'))
    if not expected or values != {expected} or any(r.get('qualified_symbol') != symbol or not r.get('mapping_source') for r in issuers):
        reasons.append('stored_cik_mapping_missing_or_conflicting')
    if approved and cik(approved[0]['cik']) != expected: reasons.append('effective_mapping_cik_mismatch')
    if any(visible(r, decision) and r.get('cik') and cik(r['cik']) != expected for r in classes):
        reasons.append('classification_cik_mismatch')
    return sorted(set(reasons)), approved[0] if len(approved) == 1 else None


def _price(sec, data, decision, sessions):
    symbol = sec['qualified_symbol']
    rows = [r for r in data['global_price_observations'] if r.get('qualified_symbol') == symbol and stamp(r.get('retrieved_at')) and stamp(r['retrieved_at']) <= decision]
    by_date = defaultdict(list)
    for r in rows: by_date[day(r.get('trading_date'))].append(r)
    reasons = []
    if len(sessions) < CONFIG['momentum_sessions'] + 1: reasons.append('insufficient_visible_exchange_sessions')
    wanted = sessions[-(CONFIG['momentum_sessions'] + 1):]
    if not wanted or any(d not in by_date for d in wanted): reasons.append('missing_exact_session_prices')
    if any(len(by_date[d]) != 1 for d in wanted if d in by_date): reasons.append('duplicate_price_observation')
    selected = [by_date[d][0] for d in wanted if len(by_date[d]) == 1]
    policy = ObservationPolicy(maximum_price_age_days=CONFIG['maximum_price_age_days'])
    if wanted and (decision.date() - wanted[-1]).days > policy.maximum_price_age_days: reasons.append('stale_decision_session')
    for r in selected:
        values = [r.get(k) for k in ('open', 'high', 'low', 'close', 'adjusted_close', 'volume')]
        if (not all(finite(v) for v in values) or any(float(v) <= 0 for v in values[:5])
            or float(values[-1]) < 0 or float(r['high']) < max(float(r[k]) for k in ('open', 'low', 'close'))
            or float(r['low']) > min(float(r[k]) for k in ('open', 'high', 'close'))
            or r.get('status') != 'available' or r.get('currency') != 'USD'
            or r.get('exchange') != 'US' or not r.get('source')
            or stamp(r['retrieved_at']).date() < day(r['trading_date'])):
            reasons.append('invalid_price_history')
    actions = [r for r in data['global_corporate_actions'] if r.get('qualified_symbol') == symbol and stamp(r.get('retrieved_at')) and stamp(r['retrieved_at']) <= decision]
    if not reasons and selected:
        if not detect_price_segments(pd.DataFrame(selected), pd.DataFrame(actions)).empty:
            reasons.append('unresolved_price_discontinuity')
    calculation = None
    if not reasons:
        feature = momentum_features(pd.Series([float(r['adjusted_close']) for r in selected]))
        calculation = {'formula': CONFIG['screen'].split(' >')[0], 'start_session': wanted[0], 'end_session': wanted[-1],
            'start_adjusted_close': float(selected[0]['adjusted_close']), 'end_adjusted_close': float(selected[-1]['adjusted_close']),
            'session_intervals': 126, 'momentum_return': float(feature['local_momentum_126d']),
            'source': sorted({r['source'] for r in selected}), 'latest_input_retrieved_at': max(stamp(r['retrieved_at']) for r in selected)}
    if calculation and not finite(calculation['momentum_return']):
        reasons.append('nonfinite_momentum_return'); calculation = None
    return sorted(set(reasons)), calculation, wanted, actions


def _actions(sec, data, decision, wanted, actions):
    if len(wanted) != 127: return ['corporate_action_window_unproven'], None
    rows = [r for r in data['corporate_action_coverage_evidence'] if str(r.get('security_id')) == sec['security_id'] and r.get('qualified_symbol') == sec['qualified_symbol'] and visible(r, decision)]
    rows = [r for r in rows if day(r.get('assessed_from')) and day(r.get('assessed_to')) and day(r['assessed_from']) <= wanted[0] and day(r['assessed_to']) >= wanted[-1] and r.get('source_identifier') and r.get('evidence_key')]
    if not rows: return ['corporate_action_coverage_missing_or_incomplete'], None
    states = {r.get('coverage_state') for r in rows}
    if not states <= {'verified_no_action', 'action_present'} or len(states) != 1:
        return ['unresolved_or_conflicting_corporate_action_coverage'], None
    events = [r for r in actions if wanted[0] <= day(r.get('ex_date')) <= wanted[-1]]
    if ('verified_no_action' in states and events) or ('action_present' in states and not events):
        return ['corporate_action_coverage_event_conflict'], None
    if any(r.get('action_type') not in ('dividend', 'split') or not finite(r.get('value')) or float(r['value']) <= 0 or not r.get('source') for r in events):
        return ['unresolved_corporate_action'], None
    row = max(rows, key=lambda r: (stamp(r['available_at']), r['evidence_key']))
    return [], {k: row[k] for k in ('evidence_key', 'coverage_state', 'assessed_from', 'assessed_to', 'source_identifier', 'available_at')}


def _evidence(sec, data, decision):
    """Direct facts only, no canonical alias activation or interval arithmetic."""
    rows = [r for r in data['sec_facts'] if str(r.get('security_id')) == sec['security_id'] and r.get('concept') in FACTS]
    grouped = defaultdict(list)
    rejected = defaultdict(set)
    for r in rows:
        field, nature = FACTS[r['concept']]
        reason = None
        public, retrieved = stamp(r.get('public_at')), stamp(r.get('retrieved_at'))
        end, start = day(r.get('period_end')), day(r.get('period_start'))
        if not public or not retrieved: reason = 'missing_availability_timestamps'
        elif max(public, retrieved) > decision: reason = 'evidence_after_cutoff'
        elif cik(r.get('cik')) != cik(sec.get('cik')) or r.get('qualified_symbol') != sec['qualified_symbol']: reason = 'evidence_identity_mismatch'
        elif r.get('taxonomy') != 'us-gaap' or r.get('unit') != 'USD' or r.get('currency') not in (None, 'USD'): reason = 'incompatible_taxonomy_unit_or_currency'
        elif not finite(r.get('value')): reason = 'nonfinite_value'
        elif r['concept'] in ('CashAndCashEquivalentsAtCarryingValue', 'Assets') and float(r['value']) < 0: reason = 'incompatible_sign'
        elif not end or end > decision.date() or end > public.date() or (nature == 'instant' and start is not None) or (nature == 'duration' and (not start or start > end)): reason = 'incompatible_reported_period'
        elif (decision.date() - end).days > CONFIG['maximum_direct_fact_age_days']: reason = 'stale_direct_evidence'
        elif (not r.get('fact_key') or not re.fullmatch(r'\d{10}-\d{2}-\d{6}', str(r.get('accession_number') or ''))
              or r.get('source_endpoint') != f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik(sec.get('cik'))}.json"
              or r.get('form') not in ('10-K', '10-K/A', '10-Q', '10-Q/A')): reason = 'source_provenance_unproven'
        if reason: rejected[field].add(reason)
        else: grouped[field].append(r)
    output, missing = [], []
    for field in sorted({v[0] for v in FACTS.values()}):
        candidates = grouped[field]
        if not candidates:
            missing.append({'field': field, 'reasons': sorted(rejected[field] or {'direct_evidence_unavailable'})})
            continue
        # Latest reported end, then latest publicly visible revision for its exact
        # interval. Different interval shapes at that end are never conflated.
        end = max(r['period_end'] for r in candidates)
        latest = [r for r in candidates if r['period_end'] == end]
        intervals = {(r.get('period_start'), r['period_end'], r['concept']) for r in latest}
        if len(intervals) != 1:
            missing.append({'field': field, 'reasons': ['ambiguous_reported_interval_or_concept']}); continue
        public = max(stamp(r['public_at']) for r in latest)
        latest = [r for r in latest if stamp(r['public_at']) == public]
        if len({float(r['value']) for r in latest}) != 1:
            missing.append({'field': field, 'reasons': ['conflicting_visible_values']}); continue
        r = max(latest, key=lambda r: (stamp(r['retrieved_at']), r['fact_key']))
        output.append({'field': field, 'value': float(r['value']), 'unit': r['unit'], 'concept': r['concept'],
            'reported_start': r.get('period_start'), 'reported_end': r['period_end'], 'period_kind': FACTS[r['concept']][1],
            'form': r['form'], 'public_at': stamp(r['public_at']), 'retrieved_at': stamp(r['retrieved_at']),
            'known_at': max(stamp(r['public_at']), stamp(r['retrieved_at'])),
            'citation': {'fact_key': r['fact_key'], 'accession': r['accession_number'], 'cik': cik(r['cik']), 'source_endpoint': r['source_endpoint']}})
    return output, missing


def _build(db, decision, target):
    data, schema, remaining = {}, {}, MAX_ROWS
    for table in COLUMNS:
        data[table], schema[table] = _read(db, table, remaining, decision)
        remaining -= len(data[table])
    required_catalogue = {'security_master_retrievals', 'security_listings'}
    if any(schema[t] != 'supported' for t in required_catalogue):
        return _report([], [], [], schema, ['active_catalogue_schema_unavailable'], target, decision)
    active = select_active_catalogue(db, as_of=decision.replace(tzinfo=None))
    if active is None: return _report([], [], [], schema, ['completed_active_catalogue_unavailable'], target, decision)
    selected = [r for r in data['security_listings'] if r.get('retrieval_id') == active.retrieval_id and r.get('primary_exchange') == 'US' and r.get('active') is True]
    # Reuse main's exact-ID reconciliation with cutoff-visible inputs only.
    reconcile_data = dict(data, canonical_factor_evidence=[],
        security_listings=[r for r in data['security_listings'] if r.get('retrieval_id') == active.retrieval_id],
        sec_issuers=[r for r in data['sec_issuers'] if stamp(r.get('mapped_at')) and stamp(r['mapped_at']) <= decision],
        sec_facts=[r for r in data['sec_facts'] if stamp(r.get('public_at')) and stamp(r.get('retrieved_at')) and max(stamp(r['public_at']), stamp(r['retrieved_at'])) <= decision])
    _, matched, _ = _reconcile(reconcile_data, decision)
    # Keep every ordinary-roster identity, including absent/inactive listings, in
    # review output so identity exclusions are not silently lost.
    roster_ids = {str(r['security_id']) for r in data['security_classification_evidence'] if r.get('security_id') and r.get('security_type') == 'us_operating_company' and visible(r, decision)}
    if len(roster_ids) > MAX_ROSTER: raise PrototypeError('PROTOTYPE_ROSTER_LIMIT')
    session_rows = [r for r in data['global_exchange_sessions'] if r.get('exchange') == 'US' and r.get('is_open') is True and stamp(r.get('retrieved_at')) and stamp(r['retrieved_at']) <= decision and r.get('source') and day(r.get('session_date')) and datetime.combine(day(r['session_date']), time(22), timezone.utc) <= decision]
    session_dates = [day(r.get('session_date')) for r in session_rows]
    session_error = len(session_dates) != len(set(session_dates)) or None in session_dates
    sessions = sorted(set(d for d in session_dates if d))
    companies = []
    for sid in sorted(roster_ids):
        listings = [r for r in selected if str(r.get('security_id')) == sid]
        if len(listings) != 1:
            companies.append({'security_id': sid, 'qualified_symbol': None, 'company_name': None, 'eligible': False,
                'reasons': ['active_listing_missing_or_ambiguous'], 'calculation': None, 'direct_evidence': [], 'missing_data': [], 'risks': ['Identity cannot be resolved.'], 'identity_evidence': None, 'action_coverage': None}); continue
        sec = dict(listings[0], security_id=sid)
        prices, calculation, wanted, actions = _price(sec, data, decision, sessions)
        identity, mapping = _identity(sec, data, decision, matched, wanted[0] if wanted else decision.date())
        if sec.get('currency') != 'USD' or sec.get('instrument_type') not in ('common_stock', 'ordinary_share'): identity.append('listing_not_ordinary_us_usd_equity')
        if sum(r.get('qualified_symbol') == sec['qualified_symbol'] for r in selected) != 1: identity.append('qualified_symbol_identity_ambiguous')
        if any(str(r.get('security_id')) != sid and cik(r.get('cik')) == cik(sec.get('cik')) for r in selected): identity.append('shared_cik_share_class_identity_unresolved')
        coverage_reasons, coverage = _actions(sec, data, decision, wanted, actions)
        evidence, missing = _evidence(sec, data, decision)
        reasons = identity + prices + coverage_reasons
        if session_error: reasons.append('exchange_session_identity_ambiguous')
        if not evidence: reasons.append('no_usable_direct_financial_evidence')
        risks = ['Current roster is not survivorship-free.', 'Momentum can reverse; this screen has no profitability evidence.', 'Financial facts are direct reported context, not complete accounting validation.', 'Trading costs and executable liquidity are not assessed.']
        if missing: risks.append('Financial context is incomplete; missing inputs are unknown, not zero.')
        if coverage and coverage['coverage_state'] == 'action_present': risks.append('Stored corporate actions are present in the price window.')
        if evidence and min(e['reported_end'] for e in evidence) < decision.date() - timedelta(days=180): risks.append('Some reported financial observations are more than 180 days old.')
        companies.append({'security_id': sid, 'qualified_symbol': sec['qualified_symbol'], 'company_name': sec.get('company_name'), 'cik': cik(sec.get('cik')),
            'eligible': not reasons, 'reasons': sorted(set(reasons)), 'calculation': calculation,
            'direct_evidence': evidence, 'missing_data': missing, 'risks': risks,
            'identity_evidence': {k: mapping.get(k) for k in ('candidate_key', 'evidence_source', 'source_identifier', 'effective_from', 'effective_to', 'observed_at')} if mapping else None,
            'action_coverage': coverage})
    eligible = sorted([c for c in companies if c['eligible']], key=lambda c: (hashlib.sha256((CONFIG['version'] + ':' + c['security_id']).encode()).hexdigest(), c['security_id']))
    members = eligible[:target]
    blockers = ['eligible_population_below_minimum'] if len(members) < CONFIG['minimum_members'] else []
    if not roster_ids: blockers.append('visible_ordinary_company_roster_unavailable')
    if not sessions: blockers.append('completed_visible_us_sessions_unavailable')
    # Block all results below ten; proposed membership is still visible for review.
    results = [] if blockers else sorted([c for c in members if c['calculation']['momentum_return'] > 0], key=lambda c: (-c['calculation']['momentum_return'], c['security_id']))[:CONFIG['maximum_results']]
    return _report(companies, members, results, schema, blockers, target, decision, [c['security_id'] for c in eligible])


def _report(companies, members, results, schema, blockers, target, decision, eligible_order=None):
    configuration = dict(CONFIG, target_members=target)
    configuration_hash = hashlib.sha256(json.dumps(configuration, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return {'namespace': CONFIG['namespace'], 'version': CONFIG['version'], 'configuration_hash': configuration_hash, 'configuration': configuration,
        'notice': NOTICE, 'validation_credit': 0, 'decision_at': decision, 'membership_state': 'proposed_unfrozen',
        'operator_review_required': True, 'target_members': target, 'minimum_members': CONFIG['minimum_members'],
        'eligible_count': sum(c['eligible'] for c in companies), 'eligible_roster': eligible_order or [],
        'proposed_membership': [c['security_id'] for c in members], 'blockers': blockers,
        'withholding_counts': dict(sorted(Counter(r for c in companies for r in c['reasons']).items())),
        'results': [c['security_id'] for c in results], 'companies': companies,
        'source_schema_states': schema, 'provider_requests': 0, 'writes': 0,
        'synthetic_fixture': any('offline-synthetic' in (c.get('calculation') or {}).get('source', []) for c in companies),
        'remaining_work': ['operator_review_actual_roster', 'explicit_universe_freeze', 'separate_prototype_database', 'watchlist_persistence', 'monthly_snapshot_persistence', 'subsequent_performance_tracking']}


def assess(*, research_db, production_db, decision_at, target_members=None, now=None):
    target = CONFIG['target_members'] if target_members is None else target_members
    if type(target) is not int or not CONFIG['minimum_members'] <= target <= CONFIG['maximum_members']:
        raise PrototypeError('PROTOTYPE_INVALID_UNIVERSE_SIZE')
    if decision_at.tzinfo is None or decision_at.utcoffset() is None: raise PrototypeError('PROTOTYPE_INVALID_TIMESTAMP')
    decision = decision_at.astimezone(timezone.utc)
    if decision > (now or datetime.now(timezone.utc)): raise PrototypeError('PROTOTYPE_FUTURE_CUTOFF')
    paths = [Path(research_db), Path(production_db)]
    try:
        validate_paths(*paths)
        if any(p.stat().st_size > MAX_FILE_BYTES for p in paths): raise PrototypeError('PROTOTYPE_DATABASE_SIZE_LIMIT')
        before = [fingerprint(p) for p in paths]
    except PrototypeError: raise
    except Exception: raise PrototypeError('PROTOTYPE_DATABASE_UNAVAILABLE') from None
    failure = None
    report = None
    try:
        with duckdb.connect(str(paths[0]), read_only=True, config={'memory_limit': '256MB', 'threads': 1, 'enable_external_access': False}) as db:
            report = _build(db, decision, target)
    except PrototypeError as exc: failure = exc
    except Exception: failure = PrototypeError('PROTOTYPE_EVIDENCE_READ_FAILED')
    try:
        if before != [fingerprint(p) for p in paths]: raise PrototypeError('PROTOTYPE_DATABASE_CHANGED')
    except PrototypeError: raise
    except Exception: raise PrototypeError('PROTOTYPE_FINGERPRINT_FAILED') from None
    if failure: raise failure
    report['databases_unchanged'] = True
    # Explicit serializers prevent NaN and avoid returning SQL/path/payload text.
    encoded = json.dumps(report, default=lambda v: v.isoformat(), sort_keys=True, allow_nan=False)
    if len(encoded.encode('utf-8')) > MAX_OUTPUT: raise PrototypeError('PROTOTYPE_OUTPUT_LIMIT')
    return json.loads(encoded)
