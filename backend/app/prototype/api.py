"""Prototype endpoints behind the existing authentication/maintenance guard.

Assessment and tracking are read-only. The only writes go to the separate
prototype store, and only when SIGNALLENS_PROTOTYPE_WRITES_ENABLED is true.
"""
from datetime import date, datetime, timezone
import os
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from ..config import get_settings
from .allocation import allocate
from .cash import implied_gbp_rate, ledger
from .checks import catalogue, evaluate
from .scorecard import record_from_monthly, score
from .decisions import DECISIONS, RULES as DECISION_RULES, decide
from .portfolio import positions_from, read_gbp_rate, read_market, valuation
from .service import PrototypeError, assess
from .store import ACCOUNT_CURRENCY, CURRENCIES, MAX_HOLDINGS_LIMIT, THESIS_SECTIONS, PrototypeStore, StoreError
from .tracking import track

router = APIRouter(prefix='/api/v1/research/prototype', tags=['unvalidated-prototype'])


_CACHE = {}


def _stat(path):
    try:
        s = Path(path).stat(); return (str(path), s.st_size, s.st_mtime_ns)
    except OSError:
        return (str(path), None, None)


def report_at(decision_at, target_members):
    """Each assessment fully hashes both databases; a report is reused only while
    both files keep the same size and modification time, so opening a company
    from the shortlist does not re-read gigabytes."""
    settings = get_settings()
    key = (decision_at.isoformat(), target_members, _stat(settings.research_database_path), _stat(settings.database_path))
    if key in _CACHE: return _CACHE[key]
    try:
        report = assess(research_db=settings.research_database_path,
                        production_db=settings.database_path, decision_at=decision_at,
                        target_members=target_members)
        if len(_CACHE) >= 8: _CACHE.clear()
        _CACHE[key] = report
        return report
    except PrototypeError as exc:
        raise HTTPException(409, detail={'code': exc.code, 'message': 'Prototype evidence could not safely be read.'}) from None
    except Exception:
        raise HTTPException(409, detail={'code': 'PROTOTYPE_EVIDENCE_READ_FAILED', 'message': 'Prototype evidence could not safely be read.'}) from None


@router.get('/roster')
def roster(decision_at: datetime = Query(...), target_members: int = Query(15, ge=10, le=20)):
    return report_at(decision_at, target_members)


@router.get('/companies/{security_id}')
def company(security_id: str, decision_at: datetime = Query(...), target_members: int = Query(15, ge=10, le=20)):
    if not 1 <= len(security_id) <= 128:
        raise HTTPException(422, detail={'code': 'PROTOTYPE_INVALID_SECURITY_ID'})
    report = report_at(decision_at, target_members)
    found = next((c for c in report['companies'] if c['security_id'] == security_id), None)
    if found is None: raise HTTPException(404, detail={'code': 'PROTOTYPE_UNKNOWN_SECURITY_ID'})
    return {k: report[k] for k in ('namespace', 'version', 'configuration_hash', 'configuration', 'notice', 'validation_credit', 'decision_at', 'membership_state', 'operator_review_required', 'synthetic_fixture', 'blockers', 'databases_unchanged')} | {
        'company': found, 'proposed_member': security_id in report['proposed_membership'],
        'qualifying_result': security_id in report['results']}


# Separate prototype store -------------------------------------------------

class WatchRequest(BaseModel):
    security_id: str = Field(min_length=1, max_length=128)
    action: str = Field(pattern='^(add|remove)$')
    qualified_symbol: str | None = Field(None, max_length=64)
    company_name: str | None = Field(None, max_length=256)


class NoteRequest(BaseModel):
    security_id: str = Field(min_length=1, max_length=128)
    body: str = Field(min_length=1, max_length=4000)


class ThesisRequest(BaseModel):
    security_id: str = Field(min_length=1, max_length=128)
    status: str = Field(pattern='^(researching|active|rejected)$')
    sections: dict[str, str | None]


class TradeRequest(BaseModel):
    kind: str = Field(pattern='^(buy|sell)$')
    qualified_symbol: str = Field(min_length=1, max_length=32)
    shares: float = Field(gt=0)
    price: float = Field(gt=0)
    fees: float = Field(0, ge=0)
    currency: str = Field('USD', pattern='^(' + '|'.join(CURRENCIES) + ')$')
    traded_on: date
    company_name: str | None = Field(None, max_length=256)
    note: str | None = Field(None, max_length=4000)
    # Pounds that left or reached the cash pool, as the broker reported (fees and conversion included).
    account_amount: float | None = Field(None, gt=0, le=1e12)


class VoidRequest(BaseModel):
    transaction_id: str = Field(min_length=1, max_length=64)
    reason: str | None = Field(None, max_length=4000)


class CashRequest(BaseModel):
    kind: str = Field(pattern='^(deposit|withdrawal)$')
    amount: float = Field(gt=0, le=1e12)
    moved_on: date
    note: str | None = Field(None, max_length=4000)


class CashVoidRequest(BaseModel):
    movement_id: str = Field(min_length=1, max_length=64)
    reason: str | None = Field(None, max_length=4000)


class SettingsRequest(BaseModel):
    monthly_contribution: float = Field(ge=0, le=1e6)
    max_holdings: int = Field(ge=1, le=MAX_HOLDINGS_LIMIT)
    fractional_shares: bool = True


class CheckSetRequest(BaseModel):
    security_id: str = Field(min_length=1, max_length=128)
    checks: list[dict] = Field(max_length=20)


class RecordRequest(BaseModel):
    decision_at: datetime
    target_members: int = Field(15, ge=10, le=20)
    include_contribution: bool = False
    reinvest: bool = True


class SnapshotRequest(BaseModel):
    decision_at: datetime
    target_members: int = Field(15, ge=10, le=20)


def _store(write=False):
    settings = get_settings()
    if write and not settings.prototype_writes_enabled:
        raise HTTPException(409, detail={'code': 'PROTOTYPE_WRITES_DISABLED',
            'message': 'Prototype store writes are disabled; set SIGNALLENS_PROTOTYPE_WRITES_ENABLED=true.'})
    try:
        return PrototypeStore(settings.prototype_database_path,
            protected_paths=(settings.research_database_path, settings.database_path))
    except StoreError as exc:
        raise HTTPException(409, detail={'code': exc.code}) from None


def _call(function, *args, **kwargs):
    try:
        return function(*args, **kwargs)
    except StoreError as exc:
        status = 404 if exc.code in ('PROTOTYPE_UNKNOWN_SNAPSHOT', 'PROTOTYPE_UNKNOWN_TRANSACTION', 'PROTOTYPE_UNKNOWN_CASH_MOVEMENT') else 409
        raise HTTPException(status, detail={'code': exc.code}) from None


@router.get('/store/watchlist')
def watchlist():
    store = _store()
    latest = {}
    for thesis in _call(store.theses):  # newest first
        latest.setdefault(thesis['security_id'], thesis)
    items = [dict(i, thesis_status=latest.get(i['security_id'], {}).get('status'),
                  thesis_recorded_at=latest.get(i['security_id'], {}).get('recorded_at'))
             for i in _call(store.watchlist)]
    return {'items': items, 'notes': _call(store.notes)}


@router.post('/store/watchlist')
def watch(request: WatchRequest):
    store = _store(write=True)
    return {'items': _call(store.watch, request.security_id, request.action,
        qualified_symbol=request.qualified_symbol, company_name=request.company_name)}


@router.get('/store/notes/{security_id}')
def notes(security_id: str):
    store = _store()
    watched = any(i['security_id'] == security_id for i in _call(store.watchlist))
    return {'notes': _call(store.notes, security_id), 'watched': watched}


@router.post('/store/notes')
def add_note(request: NoteRequest):
    return {'notes': _call(_store(write=True).add_note, request.security_id, request.body)}


@router.get('/store/theses/{security_id}')
def theses(security_id: str):
    return {'sections': list(THESIS_SECTIONS), 'versions': _call(_store().theses, security_id)}


@router.post('/store/theses')
def add_thesis(request: ThesisRequest):
    return {'sections': list(THESIS_SECTIONS),
            'versions': _call(_store(write=True).add_thesis, request.security_id, request.status, request.sections)}


@router.get('/store/snapshots')
def snapshots():
    return {'snapshots': _call(_store().snapshots)}


@router.post('/store/snapshots')
def create_snapshot(request: SnapshotRequest):
    store = _store(write=True)
    if request.decision_at.tzinfo is None:
        raise HTTPException(422, detail={'code': 'PROTOTYPE_INVALID_TIMESTAMP'})
    report = report_at(request.decision_at, request.target_members)
    return _call(store.create_snapshot, report)


_TRACKING = {}


@router.get('/store/snapshots/{snapshot_id}')
def snapshot(snapshot_id: str):
    settings = get_settings()
    frozen = _call(_store().snapshot, snapshot_id)
    key = (snapshot_id, _stat(settings.research_database_path))
    if key not in _TRACKING:
        try:
            tracking = track(frozen, research_db=settings.research_database_path)
        except PrototypeError as exc:
            raise HTTPException(409, detail={'code': exc.code, 'message': 'Tracking evidence could not safely be read.'}) from None
        if len(_TRACKING) >= 8: _TRACKING.clear()
        _TRACKING[key] = tracking
    return {'snapshot': frozen, 'tracking': _TRACKING[key]}


_MARKET = {}


@router.get('/store/portfolio')
def portfolio():
    """Holdings from recorded trades, valued at the latest stored close."""
    settings = get_settings()
    store = _store()
    transactions = _call(store.transactions)
    trades = [t for t in transactions if t['voided_at'] is None]
    now = datetime.now(timezone.utc)
    symbols = tuple(sorted({t['qualified_symbol'] for t in trades}))
    key = (symbols, now.date(), _stat(settings.research_database_path))
    if key not in _MARKET:
        try:
            market = read_market(settings.research_database_path, symbols, now)
        except PrototypeError as exc:
            raise HTTPException(409, detail={'code': exc.code, 'message': 'Stored prices could not safely be read.'}) from None
        if len(_MARKET) >= 8: _MARKET.clear()
        _MARKET[key] = market
    try:
        result = valuation(trades, _MARKET[key], as_of=now)
    except PrototypeError as exc:
        raise HTTPException(409, detail={'code': exc.code}) from None
    return result | {'transactions': transactions, 'currencies': list(CURRENCIES), 'account_currency': ACCOUNT_CURRENCY,
                     'cash': ledger(_call(store.cash_movements), transactions), 'settings': _call(store.settings)}


@router.post('/store/portfolio/trades')
def record_trade(request: TradeRequest):
    store = _store(write=True)
    _call(store.record_trade, request.kind, request.qualified_symbol, request.shares, request.price,
          request.traded_on, fees=request.fees, currency=request.currency,
          company_name=request.company_name, note=request.note, account_amount=request.account_amount)
    return portfolio()


@router.post('/store/portfolio/voids')
def void_trade(request: VoidRequest):
    _call(_store(write=True).void_trade, request.transaction_id, reason=request.reason)
    return portfolio()


@router.post('/store/portfolio/cash')
def record_cash(request: CashRequest):
    """A deposit (e.g. this month's contribution, once it has arrived) or a withdrawal, in pounds."""
    _call(_store(write=True).record_cash, request.kind, request.amount, request.moved_on, note=request.note)
    return portfolio()


@router.post('/store/portfolio/cash/voids')
def void_cash(request: CashVoidRequest):
    _call(_store(write=True).void_cash, request.movement_id, reason=request.reason)
    return portfolio()


@router.post('/store/portfolio/settings')
def save_settings(request: SettingsRequest):
    """Change the monthly contribution or the maximum number of holdings; earlier values are kept."""
    _call(_store(write=True).save_settings, monthly_contribution=request.monthly_contribution, max_holdings=request.max_holdings,
          fractional_shares=request.fractional_shares)
    return portfolio()


@router.get('/store/checks/{security_id}')
def check_sets(security_id: str):
    return {'metrics': catalogue(), 'versions': _call(_store().check_sets, security_id)}


@router.post('/store/checks')
def add_check_set(request: CheckSetRequest):
    return {'metrics': catalogue(), 'versions': _call(_store(write=True).add_check_set, request.security_id, request.checks)}


@router.get('/thesis-checks')
def thesis_checks(decision_at: datetime = Query(...), target_members: int = Query(15, ge=10, le=20),
                  security_id: str | None = Query(None, min_length=1, max_length=128)):
    """Checks for one company, or for every company held, watched or with checks."""
    if decision_at.tzinfo is None:
        raise HTTPException(422, detail={'code': 'PROTOTYPE_INVALID_TIMESTAMP'})
    store = _store()
    current = _call(store.current_checks)
    report = report_at(decision_at, target_members)
    by_id = {c['security_id']: c for c in report['companies']}
    by_symbol = {c['qualified_symbol']: c['security_id'] for c in report['companies'] if c.get('qualified_symbol')}
    try:
        held, _ = positions_from(_call(store.trades))
    except PrototypeError as exc:
        raise HTTPException(409, detail={'code': exc.code}) from None
    held_ids = {by_symbol[p['qualified_symbol']] for p in held if p['qualified_symbol'] in by_symbol}
    watched = {i['security_id'] for i in _call(store.watchlist)}
    if security_id is not None:
        wanted = [security_id]
    else:
        wanted = sorted(held_ids | watched | {k for k, v in current.items() if v})
    day = datetime.fromisoformat(str(report['decision_at']).replace('Z', '+00:00')).date()
    companies = []
    for sid in wanted:
        reasons = [r for r, member in (('held', sid in held_ids), ('watched', sid in watched)) if member]
        if sid not in by_id:
            companies.append({'security_id': sid, 'overall': 'not_covered', 'interest': reasons, 'checks': [], 'automatic': [],
                              'has_own_checks': bool(current.get(sid))}); continue
        companies.append(evaluate(by_id[sid], current.get(sid, []), day) | {'interest': reasons})
    order = {'broken': 0, 'warning': 1, 'unknown': 2, 'not_covered': 3, 'intact': 4}
    companies.sort(key=lambda c: (order[c['overall']], c.get('qualified_symbol') or c['security_id']))
    return {'decision_at': report['decision_at'], 'notice': report['notice'], 'synthetic_fixture': report['synthetic_fixture'],
            'metrics': catalogue(), 'companies': companies,
            'uncovered_holdings': sorted(p['qualified_symbol'] for p in held if p['qualified_symbol'] not in by_symbol),
            'method': 'Your conditions and automatic warning signs are re-evaluated against stored evidence at this cutoff. '
                      'The price you paid is never used. Missing evidence is unknown, not a pass.'}


@router.get('/monthly')
def monthly(decision_at: datetime = Query(...), target_members: int = Query(15, ge=10, le=20),
            include_contribution: bool = Query(False), reinvest: bool = Query(True)):
    """The monthly view: Top 3 picks, a decision for every holding and a suggested
    allocation of the cash pool at the cutoff (plus the planned monthly contribution
    when `include_contribution`, and sale proceeds when `reinvest`), all at one cutoff."""
    if decision_at.tzinfo is None:
        raise HTTPException(422, detail={'code': 'PROTOTYPE_INVALID_TIMESTAMP'})
    settings = get_settings()
    store = _store()
    report = report_at(decision_at, target_members)
    decision = datetime.fromisoformat(str(report['decision_at']).replace('Z', '+00:00'))
    # Trades after the cutoff did not exist yet at that point.
    trades = [t for t in _call(store.trades) if str(t['traded_on']) <= decision.date().isoformat()]
    symbols = tuple(sorted({t['qualified_symbol'] for t in trades}))
    key = (symbols, decision.isoformat(), _stat(settings.research_database_path))
    if key not in _MARKET:
        try:
            market = read_market(settings.research_database_path, symbols, decision)
        except PrototypeError as exc:
            raise HTTPException(409, detail={'code': exc.code, 'message': 'Stored prices could not safely be read.'}) from None
        if len(_MARKET) >= 8: _MARKET.clear()
        _MARKET[key] = market
    try:
        book = valuation(trades, _MARKET[key], as_of=decision)
    except PrototypeError as exc:
        raise HTTPException(409, detail={'code': exc.code}) from None
    by_id = {c['security_id']: c for c in report['companies']}
    by_symbol = {c['qualified_symbol']: c['security_id'] for c in report['companies'] if c.get('qualified_symbol')}
    ranking = report.get('value_ranking') or {'picks': [], 'companies': [], 'population': 0}
    assessments = {a['security_id']: a for a in ranking['companies']}
    current = _call(store.current_checks)
    holdings = []
    for p in book['positions']:
        sid = by_symbol.get(p['qualified_symbol'])
        checks = evaluate(by_id[sid], current.get(sid, []), decision.date()) if sid else None
        holdings.append({k: p.get(k) for k in ('qualified_symbol', 'currency', 'shares', 'average_cost', 'cost_basis', 'price',
                                               'market_value', 'unrealised_return', 'weight')}
                        | {'security_id': sid, 'company_name': p.get('listed_name') or p.get('company_name'),
                           'checks': checks} | decide(p, assessments.get(sid), checks))
    holdings.sort(key=lambda h: (DECISIONS.index(h['decision']), h['qualified_symbol']))
    held = {h['security_id'] for h in holdings if h['security_id']}
    picks = [assessments[sid] | {'held': sid in held} for sid in ranking['picks']]
    plan = _allocation(store, trades, holdings, picks, decision, include_contribution=include_contribution, reinvest=reinvest)
    return {'decision_at': report['decision_at'], 'notice': report['notice'], 'synthetic_fixture': report['synthetic_fixture'],
            'target_members': report['target_members'], 'population': ranking['population'], 'picks': picks,
            'holdings': holdings, 'totals': book['totals'], 'rules': DECISION_RULES,
            'counts': {d: sum(h['decision'] == d for h in holdings) for d in DECISIONS},
            'allocation': plan,
            'method': 'Holdings are built from your trades up to the cutoff and valued at the last stored close on or before it. '
                      'Each decision follows fixed rules in order: no evidence, broken thesis, overvaluation, position size, then room to add.',
            'label': 'Decision support only. Nothing is executed. The rules have not been validated against later returns.'}


def _allocation(store, trades, holdings, picks, decision, *, include_contribution, reinvest):
    """Allocate the cash pool at the cutoff. Pounds become dollars at the stored rate,
    or failing that the rate implied by your last dollar trade; with neither, nothing is bought."""
    settings = get_settings()
    pool = ledger(_call(store.cash_movements), trades, until=decision.date())
    config = _call(store.settings)
    contribution = config['monthly_contribution'] if include_contribution else 0.0
    available = max(0.0, pool['balance']) + contribution
    key = ('fx', decision.isoformat(), _stat(settings.research_database_path))
    if key not in _MARKET:
        try: _MARKET[key] = read_gbp_rate(settings.research_database_path, 'USD', decision)
        except PrototypeError: _MARKET[key] = None
    fx = _MARKET[key] or implied_gbp_rate(trades, 'USD', until=decision.date())
    plan = allocate(holdings, picks, available / fx['rate'] if fx else 0.0, reinvest=reinvest, max_holdings=config['max_holdings'],
                    fractional=config['fractional_shares'])
    def gbp(usd): return usd * fx['rate'] if fx else None
    for order in plan['sales'] + plan['buys']: order['amount_gbp'] = gbp(order['amount'])
    return plan | {'account': {
        'currency': ACCOUNT_CURRENCY, 'cash_pool': pool['balance'], 'overdrawn': pool['overdrawn'],
        'uncounted_trades': len(pool['uncounted_trades']), 'deposited_this_month': pool['deposited_this_month'],
        'monthly_contribution': config['monthly_contribution'], 'contribution_included': contribution,
        'available': available, 'gbp_per_usd': fx, 'sale_proceeds': gbp(plan['sale_proceeds']),
        'invested': gbp(plan['invested']), 'left_as_cash': gbp(plan['left_as_cash']) if fx else available}}


@router.post('/store/decision-records')
def create_decision_record(request: RecordRequest):
    """Freeze this month's picks and holding decisions so they can be scored later."""
    store = _store(write=True)
    if request.decision_at.tzinfo is None:
        raise HTTPException(422, detail={'code': 'PROTOTYPE_INVALID_TIMESTAMP'})
    view = monthly(request.decision_at, request.target_members, request.include_contribution, request.reinvest)
    record = record_from_monthly(view, report_at(request.decision_at, request.target_members))
    if not record['benchmark_symbols']:
        # Nothing was assessed at this cutoff, so the month could never be scored.
        raise HTTPException(409, detail={'code': 'PROTOTYPE_RECORD_NO_ASSESSED_COMPANIES',
            'message': 'No company could be assessed at this cutoff; refresh prices or choose a later cutoff.'})
    return _call(store.create_decision_record, record)


@router.get('/store/decision-records')
def decision_records():
    return {'records': [{k: r[k] for k in ('record_id', 'month', 'decision_at', 'created_at', 'record_sha256', 'integrity_verified')}
                        | {'items': len(r['items'])} for r in _call(_store().decision_records)]}


_SCORECARD = {}


@router.get('/scorecard')
def scorecard():
    settings = get_settings()
    store = _store()
    records = _call(store.decision_records)
    key = (tuple(r['record_id'] for r in records), datetime.now(timezone.utc).date(), _stat(settings.research_database_path),
           _stat(settings.prototype_database_path))
    if key not in _SCORECARD:
        try:
            result = score(records, research_db=settings.research_database_path, funds=_call(store.benchmark_prices))
        except PrototypeError as exc:
            raise HTTPException(409, detail={'code': exc.code, 'message': 'Stored prices could not safely be read.'}) from None
        if len(_SCORECARD) >= 8: _SCORECARD.clear()
        _SCORECARD[key] = result
    return _SCORECARD[key] | {'label': 'Description of recorded calls, not validation. Small samples are noisy.'}


@router.get('/alerts/status')
def alerts_status():
    """Whether daily alerts are on, when they last ran and what they sent."""
    from .daily_job import DailyAlertJob
    settings = get_settings()
    job = DailyAlertJob(settings)
    return {'enabled': settings.alerts_enabled, 'utc_time': settings.alerts_utc_time,
            'telegram_configured': bool(os.environ.get('SIGNALLENS_TELEGRAM_BOT_TOKEN') and os.environ.get('SIGNALLENS_TELEGRAM_CHAT_ID')),
            'next_run': job.next_run(datetime.now(timezone.utc)) if settings.alerts_enabled else None,
            'state': job.state(), 'recent': [{k: e[k] for k in ('sent_at', 'kind', 'qualified_symbol', 'decision', 'thesis', 'delivered', 'message')}
                                             for e in _call(_store().alert_events, 10)]}

