"""Prototype endpoints behind the existing authentication/maintenance guard.

Assessment and tracking are read-only. The only writes go to the separate
prototype store, and only when SIGNALLENS_PROTOTYPE_WRITES_ENABLED is true.
"""
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from ..config import get_settings
from .service import PrototypeError, assess
from .store import THESIS_SECTIONS, PrototypeStore, StoreError
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
        status = 404 if exc.code == 'PROTOTYPE_UNKNOWN_SNAPSHOT' else 409
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
