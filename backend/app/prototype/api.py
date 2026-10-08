"""GET-only prototype endpoints behind the existing authentication/maintenance guard."""
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query

from ..config import get_settings
from .service import PrototypeError, assess

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
