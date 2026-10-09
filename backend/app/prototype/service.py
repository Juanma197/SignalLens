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
from ..track_b_gaps import _visible_classification
from .brief import analyst_brief
from .events import event_brief, read_events
from .financials import DURATIONS, INSTANTS, REVENUE, annual_brief, scenario_ranges, sector_notes, summary_only, valuation, valuation_history
from .ranking import value_ranking

CONFIG = json.loads(Path(__file__).with_name('config_v1.json').read_text(encoding='utf-8'))
NOTICE = 'UNVALIDATED RESEARCH PROTOTYPE — ZERO VALIDATION CREDIT'
# Sized for a US catalogue of a few thousand companies.
MAX_ROWS = 2_000_000
MAX_ROSTER = 4_000
MAX_COMPANY_FACTS = 100_000
MAX_CELL = 1024
MAX_OUTPUT = 40_000_000
MAX_FILE_BYTES = 32_000_000_000
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
    'global_price_observations': 'qualified_symbol trading_date exchange currency open high low close adjusted_close volume status source retrieved_at',
    'global_corporate_actions': 'qualified_symbol ex_date action_type value currency source retrieved_at',
    'eodhd_ingestion_checkpoints': 'stage qualified_symbol status updated_at',
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


def _columns(db, table):
    """(projected columns present, actual columns, failure state or None when readable)."""
    base = db.execute("SELECT table_type FROM information_schema.tables WHERE table_catalog=current_database() AND table_schema='main' AND table_name=?", [table]).fetchone()
    if not base: return [], set(), 'table_absent'
    if base[0] != 'BASE TABLE': return [], set(), 'unsupported_schema'
    actual = {r[0] for r in db.execute("SELECT column_name FROM information_schema.columns WHERE table_catalog=current_database() AND table_schema='main' AND table_name=?", [table]).fetchall()}
    columns = [c for c in COLUMNS[table].split() if c in actual]
    # Missing projected fields remain None and are reported by the domain checks.
    if not columns: return [], actual, 'unsupported_schema'
    return columns, actual, None


def _state(actual, table):
    return 'supported' if set(COLUMNS[table].split()) <= actual else 'missing_columns'


def _read(db, table, remaining, decision):
    columns, actual, failed = _columns(db, table)
    if failed: return [], failed
    where, args = '', []
    # Only prices are windowed; corporate actions are read in full so that
    # action_present coverage can be checked against its whole assessed interval.
    if table == 'global_price_observations':
        field = 'trading_date'
        if field not in actual: return [], 'unsupported_schema'
        # Only US rows are ever used (sessions and price checks are US-only).
        where = f' WHERE "{field}" BETWEEN ? AND ?' + (" AND exchange = 'US'" if 'exchange' in actual else '')
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
    return rows, _state(actual, table)


# Facts are read per company: a few thousand companies hold millions of rows.
FACT_CONCEPTS = sorted(set(FACTS) | set(REVENUE) | {c for cs in DURATIONS.values() for c in cs} | {c for cs in INSTANTS.values() for c in cs})


def _fact_identities(db):
    """Distinct (security_id, cik, public_at, retrieved_at) for identity matching."""
    columns, actual, failed = _columns(db, 'sec_facts')
    if failed: return [], failed
    if not {'security_id', 'cik', 'public_at', 'retrieved_at'} <= actual: return [], 'missing_columns'
    rows = db.execute('SELECT DISTINCT security_id, cik, public_at, retrieved_at FROM sec_facts LIMIT ?', [MAX_ROWS + 1]).fetchall()
    if len(rows) > MAX_ROWS: raise PrototypeError('PROTOTYPE_ROW_LIMIT')
    return [dict(zip(('security_id', 'cik', 'public_at', 'retrieved_at'), r)) for r in rows], _state(actual, 'sec_facts')


def _company_facts(db, security_id):
    """One company's facts for the concepts the prototype uses."""
    columns, actual, failed = _columns(db, 'sec_facts')
    if failed or not {'security_id', 'concept'} <= actual: return []
    marks = ', '.join('?' for _ in FACT_CONCEPTS)
    where, args = f'WHERE security_id = ? AND concept IN ({marks})', [security_id, *FACT_CONCEPTS]
    checks = ' OR '.join(f'length(CAST("{c}" AS VARCHAR))>{MAX_CELL}' for c in columns)
    if db.execute(f'SELECT count(*) FROM sec_facts {where} AND ({checks})', args).fetchone()[0]:
        raise PrototypeError('PROTOTYPE_CELL_LIMIT')
    names = ','.join('"' + c + '"' for c in columns)
    rows = [dict(zip(columns, r)) for r in db.execute(f'SELECT {names} FROM sec_facts {where} LIMIT {MAX_COMPANY_FACTS + 1}', args).fetchall()]
    if len(rows) > MAX_COMPANY_FACTS: raise PrototypeError('PROTOTYPE_ROW_LIMIT')
    return rows


def _matched(data, decision):
    """Durable IDs that match without conflict: the same rule as
    track_b_gaps._reconcile (exact security_id intersection; CIK only as a
    consistency check), without its Track B roster bound."""
    classes = defaultdict(set)
    for row in data['security_classification_evidence']:
        if row.get('security_id') and _visible_classification(row, decision):
            classes[str(row['security_id'])].add(row.get('security_type'))
    roster = {sid for sid, types in classes.items() if 'us_operating_company' in types}
    if len(roster) > MAX_ROSTER: raise PrototypeError('PROTOTYPE_ROSTER_LIMIT')
    stored = {str(r['security_id']) for table in ('security_listings', 'universe_snapshot_members', 'sec_issuers', 'sec_facts')
              for r in data[table] if r.get('security_id')}
    ciks, invalid = defaultdict(set), set()
    for table in ('security_listings', 'sec_issuers', 'sec_facts', 'security_classification_evidence'):
        for row in data[table]:
            if not row.get('security_id') or not row.get('cik'): continue
            sid, value = str(row['security_id']), str(row['cik'])
            if not value.isascii() or not value.isdigit() or not 1 <= len(value) <= 10 or int(value) == 0: invalid.add(sid)
            else: ciks[sid].add(value.zfill(10))
    owners = defaultdict(set)
    for sid, values in ciks.items():
        for value in values: owners[value].add(sid)
    return {sid for sid in roster if sid in stored and len(classes[sid]) == 1 and len(ciks[sid]) <= 1
            and sid not in invalid and not any(len(owners[value]) > 1 for value in ciks[sid])}


def _group(rows, key):
    out = defaultdict(list)
    for r in rows: out[key(r)].append(r)
    return out


def _identity(sec, data, decision, matched, window_start):
    """Identity at the cutoff (config identity_rule). The stored catalogue keeps
    CIK in sec_issuers/classification, not security_listings, so the CIK must be
    agreed there; a listing CIK, when present, must also agree. An approved
    full-window mapping is cited when it exists but is not required."""
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
    issuers = [r for r in data['sec_issuers'] if str(r.get('security_id')) == sid and stamp(r.get('mapped_at')) and stamp(r['mapped_at']) <= decision]
    values = {cik(r.get('cik')) for r in issuers}
    expected = next(iter(values)) if len(values) == 1 else None
    if not expected or any(r.get('qualified_symbol') != symbol or not r.get('mapping_source') for r in issuers):
        reasons.append('stored_cik_mapping_missing_or_conflicting'); expected = None
    class_ciks = {cik(r.get('cik')) for r in classes if visible(r, decision) and r.get('cik')}
    if expected and class_ciks != {expected}: reasons.append('classification_cik_mismatch')
    if sec.get('cik') is not None and cik(sec['cik']) != expected: reasons.append('listing_cik_mismatch')
    mappings = [r for r in data['issuer_mapping_candidates'] if str(r.get('security_id')) == sid and stamp(r.get('observed_at')) and stamp(r['observed_at']) <= decision]
    if any(r.get('conflict_state') not in ('none', None) for r in mappings):
        reasons.append('issuer_mapping_conflict')
    approved = []
    for r in mappings:
        start, end = stamp(r.get('effective_from')), stamp(r.get('effective_to'))
        if (start and start <= datetime.combine(window_start, time.min, timezone.utc)
            and (r.get('effective_to') is None or (end and decision < end))
            and r.get('review_status') == 'approved' and r.get('conflict_state') == 'none'
            and r.get('ticker_reuse_protected') is True and r.get('qualified_symbol') == symbol
            and r.get('evidence_source') and r.get('source_identifier') and cik(r.get('cik'))):
            approved.append(r)
    if len(approved) == 1 and cik(approved[0]['cik']) != expected: reasons.append('effective_mapping_cik_mismatch')
    return sorted(set(reasons)), expected, approved[0] if len(approved) == 1 else None


def _sessions(data, decision):
    """Session calendar derived from stored prices (no session table exists in
    the operator database). Weekdays are never assumed; a date counts only when
    most US symbols have a visible, completed price on it."""
    by_date = defaultdict(set)
    for r in data['global_price_observations']:
        d, retrieved = day(r.get('trading_date')), stamp(r.get('retrieved_at'))
        if (r.get('exchange') == 'US' and d and retrieved and retrieved <= decision and r.get('qualified_symbol')
                and datetime.combine(d, time(22), timezone.utc) <= decision):
            by_date[d].add(r['qualified_symbol'])
    symbols = set().union(*by_date.values()) if by_date else set()
    threshold = math.ceil(CONFIG['session_minimum_symbol_share'] * len(symbols))
    sessions = sorted(d for d, s in by_date.items() if threshold and len(s) >= threshold)
    return sessions, {'method': CONFIG['session_calendar'], 'us_symbols_priced': len(symbols),
        'minimum_symbols_per_session': threshold, 'candidate_dates': len(by_date), 'derived_sessions': len(sessions),
        'first_session': sessions[0] if sessions else None, 'last_session': sessions[-1] if sessions else None}


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
    actions = [r for r in data['global_corporate_actions'] if r.get('qualified_symbol') == symbol and day(r.get('ex_date')) and stamp(r.get('retrieved_at')) and stamp(r['retrieved_at']) <= decision]
    if not reasons and selected:
        if not detect_price_segments(pd.DataFrame(selected), pd.DataFrame(actions)).empty:
            reasons.append('unresolved_price_discontinuity')
    calculation = None
    if not reasons:
        feature = momentum_features(pd.Series([float(r['adjusted_close']) for r in selected]))
        calculation = {'formula': CONFIG['screen'].split(' >')[0], 'start_session': wanted[0], 'end_session': wanted[-1],
            'start_adjusted_close': float(selected[0]['adjusted_close']), 'end_adjusted_close': float(selected[-1]['adjusted_close']),
            'end_close': float(selected[-1]['close']),
            'session_intervals': 126, 'momentum_return': float(feature['local_momentum_126d']),
            'source': sorted({r['source'] for r in selected}), 'latest_input_retrieved_at': max(stamp(r['retrieved_at']) for r in selected)}
    if calculation and not finite(calculation['momentum_return']):
        reasons.append('nonfinite_momentum_return'); calculation = None
    return sorted(set(reasons)), calculation, wanted, actions


DIVIDEND_FETCH_SOURCE = 'eodhd_ingestion_checkpoints: completed prices (10-year) or refresh run; the dividends endpoint was queried'


def _dividend_fetch_coverage(sec, data, decision, wanted, actions):
    """Coverage from the dividend queries themselves, for companies with no stored
    coverage record (in practice: companies that never paid a dividend, for which
    nothing is stored). A completed 10-year 'prices' run queried the dividends
    endpoint from at least ten years before it finished, and each completed
    'refresh' re-queried it; both must bracket the price window. An empty answer is
    then evidence of no dividend, not a gap. Splits are not fetched by this
    provider path for any company; the price-discontinuity rule covers them."""
    symbol = sec['qualified_symbol']
    done = [c for c in data['eodhd_ingestion_checkpoints'] if c.get('qualified_symbol') == symbol and c.get('status') == 'completed'
            and c.get('stage') in ('prices', 'refresh') and stamp(c.get('updated_at')) and stamp(c['updated_at']) <= decision]
    full = [stamp(c['updated_at']) for c in done if c['stage'] == 'prices']
    if not full or min(full).date() - timedelta(days=CONFIG['dividend_fetch_history_days']) > wanted[0]:
        return ['corporate_action_coverage_missing_or_incomplete'], None
    latest = max(stamp(c['updated_at']) for c in done)
    if latest < datetime.combine(wanted[-1], time(22), timezone.utc):
        return ['corporate_action_coverage_missing_or_incomplete'], None
    events = [r for r in actions if wanted[0] <= day(r.get('ex_date')) <= wanted[-1]]
    if any(r.get('action_type') not in CONFIG['corporate_action_types_accepted'] or not finite(r.get('value')) or float(r['value']) <= 0 or not r.get('source') for r in events):
        return ['unresolved_corporate_action'], None
    return [], {'evidence_key': f'dividend-fetch:{symbol}:{latest.isoformat()}', 'coverage_state': 'action_present' if events else 'verified_no_action',
                'assessed_from': wanted[0], 'assessed_to': wanted[-1], 'source_identifier': DIVIDEND_FETCH_SOURCE,
                'available_at': latest, 'extension': None}


def _actions(sec, data, decision, wanted, actions):
    """Coverage over the whole price window: a stored coverage record, optionally
    extended past its end by a completed EODHD dividend refresh recorded after the
    window's last session closed (config coverage_extension)."""
    if len(wanted) != 127: return ['corporate_action_window_unproven'], None
    rows = [r for r in data['corporate_action_coverage_evidence'] if str(r.get('security_id')) == sec['security_id'] and r.get('qualified_symbol') == sec['qualified_symbol'] and visible(r, decision)]
    rows = [r for r in rows if day(r.get('assessed_from')) and day(r.get('assessed_to')) and day(r['assessed_from']) <= wanted[0]
            and day(r['assessed_to']) >= wanted[0] and r.get('source_identifier') and r.get('evidence_key')]
    if not rows: return _dividend_fetch_coverage(sec, data, decision, wanted, actions)
    extension = None
    if any(day(r['assessed_to']) >= wanted[-1] for r in rows):
        rows = [r for r in rows if day(r['assessed_to']) >= wanted[-1]]
        through = wanted[-1]
    else:
        through = max(day(r['assessed_to']) for r in rows)
        closed = datetime.combine(wanted[-1], time(22), timezone.utc)
        refreshes = [c for c in data['eodhd_ingestion_checkpoints'] if c.get('stage') == 'refresh' and c.get('status') == 'completed'
                     and c.get('qualified_symbol') == sec['qualified_symbol'] and stamp(c.get('updated_at'))
                     and closed <= stamp(c['updated_at']) <= decision]
        if not refreshes: return ['corporate_action_coverage_missing_or_incomplete'], None
        rows = [r for r in rows if day(r['assessed_to']) == through]
        extension = {'stored_record_through': through, 'dividend_refresh_completed_at': max(stamp(c['updated_at']) for c in refreshes),
                     'source': 'eodhd_ingestion_checkpoints (stage refresh, completed)'}
    states = {r.get('coverage_state') for r in rows}
    if not states <= {'verified_no_action', 'action_present'} or len(states) != 1:
        return ['unresolved_or_conflicting_corporate_action_coverage'], None
    events = [r for r in actions if wanted[0] <= day(r.get('ex_date')) <= wanted[-1]]
    # verified_no_action is a claim only up to the stored record's end; action_present
    # describes its whole assessed interval and conflicts only if that has no event.
    claimed = [r for r in events if day(r['ex_date']) <= through]
    assessed = [r for r in actions if any(day(c['assessed_from']) <= day(r.get('ex_date')) <= day(c['assessed_to']) for c in rows)]
    if ('verified_no_action' in states and claimed) or ('action_present' in states and not assessed):
        return ['corporate_action_coverage_event_conflict'], None
    if any(r.get('action_type') not in CONFIG['corporate_action_types_accepted'] or not finite(r.get('value')) or float(r['value']) <= 0 or not r.get('source') for r in events):
        return ['unresolved_corporate_action'], None
    row = max(rows, key=lambda r: (stamp(r['available_at']), r['evidence_key']))
    return [], dict({k: row[k] for k in ('evidence_key', 'coverage_state', 'assessed_from', 'assessed_to', 'source_identifier')},
                    available_at=stamp(row['available_at']), extension=extension)


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
        # Latest reported end. Each exact (concept, start, end) interval at that end
        # (e.g. a quarter and a year-to-date) is shown as its own observation with
        # its length; intervals are never combined, converted or chosen between.
        end = max(r['period_end'] for r in candidates)
        intervals = defaultdict(list)
        for r in candidates:
            if r['period_end'] == end: intervals[(r['concept'], r.get('period_start'))].append(r)
        shown = []
        for (concept, start), latest in sorted(intervals.items(), key=lambda kv: (kv[0][0], kv[0][1] or date.min), reverse=True):
            public = max(stamp(r['public_at']) for r in latest)
            latest = [r for r in latest if stamp(r['public_at']) == public]
            if len({float(r['value']) for r in latest}) != 1: continue
            r = max(latest, key=lambda r: (stamp(r['retrieved_at']), r['fact_key']))
            shown.append({'field': field, 'value': float(r['value']), 'unit': r['unit'], 'concept': concept,
                'reported_start': start, 'reported_end': end, 'period_kind': FACTS[concept][1],
                'reported_days': (day(end) - day(start)).days + 1 if start else None,
                'form': r['form'], 'public_at': stamp(r['public_at']), 'retrieved_at': stamp(r['retrieved_at']),
                'known_at': max(stamp(r['public_at']), stamp(r['retrieved_at'])),
                'citation': {'fact_key': r['fact_key'], 'accession': r['accession_number'], 'cik': cik(r['cik']), 'source_endpoint': r['source_endpoint']}})
        if not shown:
            missing.append({'field': field, 'reasons': ['conflicting_visible_values']}); continue
        output.extend(shown)
    return output, missing


def _industry(db, decision):
    """SIC/entity fields extracted in SQL from retained SEC submissions payloads,
    so payload text is never projected. Visible from their retrieval time."""
    tables = {r[0] for r in db.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='main'").fetchall()}
    if 'sec_liquidity_raw_provenance' not in tables: return {}, 'table_absent'
    rows = db.execute("""SELECT security_id, cik, retrieved_at, response_sha256,
            json_extract_string(payload_json, '$.sic') AS sic,
            left(json_extract_string(payload_json, '$.sicDescription'), 200) AS sic_description,
            left(json_extract_string(payload_json, '$.entityType'), 64) AS entity_type,
            left(json_extract_string(payload_json, '$.name'), 200) AS sec_name
        FROM sec_liquidity_raw_provenance WHERE endpoint_class = 'submissions'""").fetchall()
    found = defaultdict(list)
    for sid, row_cik, retrieved, digest, sic, description, entity_type, name in rows:
        if stamp(retrieved) and stamp(retrieved) <= decision:
            found[str(sid)].append({'cik': cik(row_cik), 'sic': sic, 'sic_description': description, 'entity_type': entity_type,
                                    'sec_name': name, 'retrieved_at': stamp(retrieved), 'response_sha256': digest})
    return found, 'supported'


def _industry_gate(sec, records):
    """Specialist sectors are withheld until their accounting is supported."""
    records = [r for r in records if r['cik'] == sec.get('cik')]
    sics = {r['sic'] for r in records}
    if not records or sics == {None} or sics == {''}: return ['industry_classification_unavailable'], None
    if len(sics) != 1 or not str(next(iter(sics))).isdigit(): return ['industry_classification_conflicting'], None
    record = max(records, key=lambda r: r['retrieved_at'])
    code = int(record['sic'])
    reasons = []
    if any(low <= code <= high for low, high in CONFIG['excluded_sic_ranges']): reasons.append('specialist_sector_excluded')
    names = ' / '.join(n for n in (sec.get('company_name'), record.get('sec_name')) if n)
    if re.search(CONFIG['partnership_name_pattern'], sec.get('company_name') or '') or re.search(CONFIG['partnership_name_pattern'], record.get('sec_name') or ''):
        reasons.append('partnership_units_excluded')
    return reasons, dict(record, sic=code, names_checked=names)


def _share_counts(db, decision):
    """Cover-page share counts from retained SEC companyfacts payloads, one row per
    reported entry (extracted in SQL; payload text is never projected). An entry is
    known from the later of its filing day (end of day, UTC) and payload retrieval."""
    tables = {r[0] for r in db.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='main'").fetchall()}
    if 'sec_liquidity_raw_provenance' not in tables: return {}
    # One payload at a time, parsed in Python: companyfacts documents reach several
    # MB each and exceed the bounded SQL memory limit when parsed in SQL. A payload
    # whose bytes do not match its stored SHA-256 and byte count is not used.
    keys = [r[0] for r in db.execute("SELECT evidence_key FROM sec_liquidity_raw_provenance WHERE endpoint_class = 'companyfacts' ORDER BY evidence_key").fetchall()]
    if len(keys) > MAX_ROSTER * 4: raise PrototypeError('PROTOTYPE_ROW_LIMIT')
    rows = []
    for key in keys:
        sid, row_cik, retrieved, digest, byte_count, payload = db.execute("""SELECT security_id, cik, retrieved_at,
            response_sha256, byte_count, payload_json FROM sec_liquidity_raw_provenance WHERE evidence_key = ?""", [key]).fetchone()
        raw = payload.encode('utf-8')
        if len(raw) != byte_count or hashlib.sha256(raw).hexdigest() != digest: continue
        try:
            entries = json.loads(payload)['facts']['dei']['EntityCommonStockSharesOutstanding']['units']['shares']
        except (ValueError, KeyError, TypeError):
            continue
        for e in entries if isinstance(entries, list) else []:
            if isinstance(e, dict):
                rows.append((sid, row_cik, retrieved, digest, e.get('end'), e.get('val'), e.get('accn'), e.get('form'), e.get('filed')))
        if len(rows) > MAX_ROWS: raise PrototypeError('PROTOTYPE_ROW_LIMIT')
    found = defaultdict(list)
    for sid, row_cik, retrieved, digest, end, value, accession, form, filed in rows:
        try:
            end, filed = date.fromisoformat(end), date.fromisoformat(filed)
            value = float(value)
        except (TypeError, ValueError):
            continue
        known = max(stamp(retrieved), datetime.combine(filed + timedelta(days=1), time.min, timezone.utc)) if stamp(retrieved) else None
        if known and known <= decision and finite(value) and value > 0 and form in ('10-K', '10-K/A', '10-Q', '10-Q/A') and end <= filed:
            found[str(sid)].append({'cik': cik(row_cik), 'end': end, 'value': value, 'accession': accession,
                                    'form': form, 'filed': filed, 'known_at': known, 'response_sha256': digest})
    return found


def _size(sec, entries, decision, calculation):
    """Market cap = latest visible cover-page share count x unadjusted close on
    the decision session. Counts reported in one filing are summed and flagged."""
    if not calculation: return ['market_cap_unavailable'], None
    entries = [e for e in entries if e['cik'] == sec.get('cik')]
    if not entries: return ['market_cap_unavailable'], None
    end = max(e['end'] for e in entries)
    if (decision.date() - end).days > CONFIG['maximum_share_count_age_days']: return ['market_cap_unavailable'], None
    latest = [e for e in entries if e['end'] == end]
    filed = max(e['filed'] for e in latest)
    latest = [e for e in latest if e['filed'] == filed]
    accessions = {e['accession'] for e in latest}
    if len(accessions) != 1: return ['market_cap_unavailable'], None
    classes = sorted({e['value'] for e in latest})
    shares = sum(classes)
    value = shares * calculation['end_close']
    low, high = CONFIG['market_cap_band_usd']
    size = {'market_cap_usd': value, 'shares_outstanding': shares, 'share_classes_summed': len(classes),
            'shares_as_of': end, 'shares_accession': next(iter(accessions)), 'shares_filed': filed,
            'shares_form': latest[0]['form'], 'source_response_sha256': latest[0]['response_sha256'],
            'close': calculation['end_close'], 'close_session': calculation['end_session'],
            'band_usd': [low, high], 'formula': CONFIG['market_cap_formula']}
    return ([] if low <= value <= high else ['market_cap_outside_band']), size


def _fiscal_year_prices(db, symbol, ends, decision):
    """Unadjusted close on the last stored US session on or before each fiscal
    year end (within 7 days), visible by the cutoff. Prices beyond the main
    450-day read are needed, so this is a small separate query per company."""
    found = {}
    for end in ends[:6]:
        row = db.execute("""SELECT trading_date, close FROM global_price_observations
            WHERE qualified_symbol = ? AND exchange = 'US' AND status = 'available'
              AND trading_date BETWEEN ? AND ? AND retrieved_at <= ?
            ORDER BY trading_date DESC, source LIMIT 1""",
            [symbol, end - timedelta(days=7), end, decision.replace(tzinfo=None)]).fetchone()
        if row and finite(row[1]) and float(row[1]) > 0:
            found[end] = {'session': day(row[0]), 'close': float(row[1])}
    return found


def _build(db, decision, target):
    data, schema, remaining = {}, {}, MAX_ROWS
    for table in COLUMNS:
        if table == 'sec_facts': continue  # read per company below
        data[table], schema[table] = _read(db, table, remaining, decision)
        remaining -= len(data[table])
    data['sec_facts'], schema['sec_facts'] = _fact_identities(db)
    required_catalogue = {'security_master_retrievals', 'security_listings'}
    if any(schema[t] != 'supported' for t in required_catalogue):
        return _report([], [], [], schema, ['active_catalogue_schema_unavailable'], target, decision)
    active = select_active_catalogue(db, as_of=decision.replace(tzinfo=None))
    if active is None: return _report([], [], [], schema, ['completed_active_catalogue_unavailable'], target, decision)
    selected = [r for r in data['security_listings'] if r.get('retrieval_id') == active.retrieval_id and r.get('primary_exchange') == 'US' and r.get('active') is True]
    # Exact-ID reconciliation (track_b_gaps rule) with cutoff-visible inputs only.
    reconcile_data = dict(data,
        security_listings=[r for r in data['security_listings'] if r.get('retrieval_id') == active.retrieval_id],
        sec_issuers=[r for r in data['sec_issuers'] if stamp(r.get('mapped_at')) and stamp(r['mapped_at']) <= decision],
        sec_facts=[r for r in data['sec_facts'] if stamp(r.get('public_at')) and stamp(r.get('retrieved_at')) and max(stamp(r['public_at']), stamp(r['retrieved_at'])) <= decision])
    matched = _matched(reconcile_data, decision)
    # Keep every ordinary-roster identity, including absent/inactive listings, in
    # review output so identity exclusions are not silently lost.
    roster_ids = {str(r['security_id']) for r in data['security_classification_evidence'] if r.get('security_id') and r.get('security_type') == 'us_operating_company' and visible(r, decision)}
    if len(roster_ids) > MAX_ROSTER: raise PrototypeError('PROTOTYPE_ROSTER_LIMIT')
    sessions, calendar = _sessions(data, decision)
    industries, schema['sec_liquidity_raw_provenance'] = _industry(db, decision)
    share_counts = _share_counts(db, decision)
    filing_events, schema['sec_event_metadata'] = read_events(db, decision, stamp)
    # Each company sees only its own rows: grouped once instead of rescanning every table per company.
    by_sid = {t: _group(data[t], lambda r: str(r.get('security_id'))) for t in
              ('security_classification_evidence', 'sec_issuers', 'issuer_mapping_candidates', 'corporate_action_coverage_evidence')}
    by_symbol = {t: _group(data[t], lambda r: r.get('qualified_symbol')) for t in
                 ('global_price_observations', 'global_corporate_actions', 'eodhd_ingestion_checkpoints')}
    listings_by_sid = _group(selected, lambda r: str(r.get('security_id')))
    symbol_counts = Counter(r.get('qualified_symbol') for r in selected)
    company_facts = {}
    companies = []
    for sid in sorted(roster_ids):
        listings = listings_by_sid.get(sid, [])
        if len(listings) != 1:
            companies.append({'security_id': sid, 'qualified_symbol': None, 'company_name': None, 'eligible': False,
                'reasons': ['active_listing_missing_or_ambiguous'], 'calculation': None, 'direct_evidence': [], 'missing_data': [], 'risks': ['Identity cannot be resolved.'], 'identity_evidence': None, 'action_coverage': None, 'industry': None, 'size': None}); continue
        sec = dict(listings[0], security_id=sid)
        facts = _company_facts(db, sid)
        own = {t: rows.get(sid, []) for t, rows in by_sid.items()} | {t: rows.get(sec['qualified_symbol'], []) for t, rows in by_symbol.items()} | {'sec_facts': facts}
        prices, calculation, wanted, actions = _price(sec, own, decision, sessions)
        identity, resolved_cik, mapping = _identity(sec, own, decision, matched, wanted[0] if wanted else decision.date())
        sec['cik'] = resolved_cik
        if sec.get('currency') != 'USD' or sec.get('instrument_type') not in ('common_stock', 'ordinary_share'): identity.append('listing_not_ordinary_us_usd_equity')
        if symbol_counts[sec['qualified_symbol']] != 1: identity.append('qualified_symbol_identity_ambiguous')
        coverage_reasons, coverage = _actions(sec, own, decision, wanted, actions)
        evidence, missing = _evidence(sec, own, decision)
        industry_reasons, industry = _industry_gate(sec, industries.get(sid, []))
        size_reasons, size = _size(sec, share_counts.get(sid, []), decision, calculation)
        reasons = identity + prices + coverage_reasons + industry_reasons + size_reasons
        if not evidence: reasons.append('no_usable_direct_financial_evidence')
        risks = ['Current roster is not survivorship-free.', 'Momentum can reverse; this screen has no profitability evidence.', 'Financial facts are direct reported context, not complete accounting validation.', 'Trading costs and executable liquidity are not assessed.']
        if not mapping: risks.append('Listing identity is evidenced at the cutoff only; ticker reuse earlier in the price window is not excluded.')
        if missing: risks.append('Financial context is incomplete; missing inputs are unknown, not zero.')
        if size and size['share_classes_summed'] > 1: risks.append('Market cap sums several share counts from one filing; class detail is not stored.')
        if coverage and coverage['coverage_state'] == 'action_present':
            in_window = any(wanted[0] <= day(r['ex_date']) <= wanted[-1] for r in actions)
            risks.append('Stored corporate actions fall inside the price window.' if in_window else 'Stored corporate actions exist in the assessed history, none inside the price window.')
        if evidence and min(e['reported_end'] for e in evidence) < decision.date() - timedelta(days=180): risks.append('Some reported financial observations are more than 180 days old.')
        companies.append({'security_id': sid, 'qualified_symbol': sec['qualified_symbol'], 'company_name': sec.get('company_name'), 'cik': resolved_cik,
            'eligible': not reasons, 'reasons': sorted(set(reasons)), 'calculation': calculation,
            'direct_evidence': evidence, 'missing_data': missing, 'risks': risks,
            'identity_evidence': {k: mapping.get(k) for k in ('candidate_key', 'evidence_source', 'source_identifier', 'effective_from', 'effective_to', 'observed_at')} if mapping else None,
            'action_coverage': coverage, 'industry': industry, 'size': size,
            # Context only: never used for eligibility, membership or ordering.
            'financials': annual_brief(sec, facts, decision, stamp=stamp, finite=finite)})
        if not reasons: company_facts[sid] = facts  # kept for the first-reported history below
    eligible = sorted([c for c in companies if c['eligible']], key=lambda c: (hashlib.sha256((CONFIG['version'] + ':' + c['security_id']).encode()).hexdigest(), c['security_id']))
    for c in companies:
        if c.get('financials') is not None: c['valuation'] = valuation(c.get('size'), c['financials'])
        if c.get('industry'): c['sector_notes'] = sector_notes(c['industry'])
        if c.get('cik'): c['events'] = event_brief(c, filing_events.get(c['security_id'], []), decision, full=c['eligible'])
        if c['eligible'] and c.get('valuation'):
            first = annual_brief({'security_id': c['security_id'], 'cik': c['cik']}, company_facts[c['security_id']], decision,
                                 stamp=stamp, finite=finite, revision='first')
            prices = _fiscal_year_prices(db, c['qualified_symbol'], [y['fiscal_year_end'] for y in first['years']], decision)
            c['valuation']['history'] = valuation_history(first, prices, c['size']['close'])
            c['valuation']['scenarios'] = scenario_ranges(c['financials'], c['valuation']['history'], c['size'])
        if not c['eligible'] and c.get('financials'): c['financials'] = summary_only(c['financials'])
    members = eligible[:target]
    blockers = ['eligible_population_below_minimum'] if len(members) < CONFIG['minimum_members'] else []
    if not roster_ids: blockers.append('visible_ordinary_company_roster_unavailable')
    if not sessions: blockers.append('completed_visible_us_sessions_unavailable')
    # Block all results below ten; proposed membership is still visible for review.
    results = [] if blockers else sorted([c for c in members if c['calculation']['momentum_return'] > 0], key=lambda c: (-c['calculation']['momentum_return'], c['security_id']))[:CONFIG['maximum_results']]
    for c in members:
        c['brief'] = analyst_brief(c, decision, result=c['security_id'] in {r['security_id'] for r in results})
    report = _report(companies, members, results, schema, blockers, target, decision, [c['security_id'] for c in eligible], calendar)
    # Separate from the momentum results above: every eligible company, not only members.
    report['value_ranking'] = value_ranking(companies)
    return report


def _report(companies, members, results, schema, blockers, target, decision, eligible_order=None, calendar=None):
    configuration = dict(CONFIG, target_members=target)
    configuration_hash = hashlib.sha256(json.dumps(configuration, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return {'namespace': CONFIG['namespace'], 'version': CONFIG['version'], 'configuration_hash': configuration_hash, 'configuration': configuration,
        'notice': NOTICE, 'validation_credit': 0, 'decision_at': decision, 'membership_state': 'proposed_unfrozen',
        'operator_review_required': True, 'target_members': target, 'minimum_members': CONFIG['minimum_members'],
        'eligible_count': sum(c['eligible'] for c in companies), 'eligible_roster': eligible_order or [],
        'proposed_membership': [c['security_id'] for c in members], 'blockers': blockers,
        'withholding_counts': dict(sorted(Counter(r for c in companies for r in c['reasons']).items())),
        'results': [c['security_id'] for c in results], 'companies': companies,
        'source_schema_states': schema, 'session_calendar': calendar, 'provider_requests': 0, 'writes': 0,
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
