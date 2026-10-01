"""Fail-closed, atomic staging research-database synchronization.

The workflow is deliberately filesystem-only.  It has no provider, scheduler,
publisher, or production-write capability.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import duckdb

from .database_backup import BackupProfile, verify_backup, _fsync_file, _fsync_parent_directory

APPLY_PHRASE = "I AUTHORIZE STAGING RESEARCH DATABASE REPLACEMENT"
ROLLBACK_PHRASE = "I AUTHORIZE STAGING RESEARCH DATABASE ROLLBACK"
PLAN_TTL_SECONDS = 900
DEFAULT_SAFETY_MARGIN = 32 * 1024 * 1024
COMPATIBILITY_TABLES = frozenset({
    "security_master_retrievals", "security_listings", "global_price_observations",
    "global_fx_observations", "global_corporate_actions", "global_exchange_sessions",
    "eodhd_ingestion_checkpoints", "sec_issuers", "sec_filings", "sec_facts",
    "sec_ingestion_runs", "sec_checkpoints", "sec_event_metadata",
    "sec_event_ingestion_runs", "sec_event_checkpoints",
})


def fingerprint(path: Path) -> dict[str, Any]:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return {"sha256": digest.hexdigest(), "byte_count": path.stat().st_size}


def _same_file(left: Path, right: Path) -> bool:
    try:
        a, b = left.stat(), right.stat()
        return left.resolve() == right.resolve() or (a.st_dev, a.st_ino) == (b.st_dev, b.st_ino)
    except FileNotFoundError:
        return left.resolve() == right.resolve()


def _validate_paths(candidate: Path, research: Path, production: Path,
                    bootstrap: Path, *, staging: bool) -> tuple[Path, Path, Path]:
    paths = [Path(value).expanduser() for value in (candidate, research, production)]
    if any(not value.exists() or not value.is_file() for value in paths):
        raise ValueError("candidate, research and production must be explicit existing regular files")
    if any(value.is_symlink() for value in paths):
        raise ValueError("symlink database paths are prohibited")
    candidate, research, production = (value.resolve() for value in paths)
    if any(_same_file(production, other) for other in (candidate, research)):
        raise ValueError("production database aliases are prohibited")
    if _same_file(candidate, research):
        raise ValueError("candidate and active research database must be distinct")
    if staging:
        root = bootstrap.expanduser().resolve()
        if not candidate.is_relative_to(root):
            raise ValueError("staging candidate must be beneath the bootstrap directory")
    return candidate, research, production


def compatibility(path: Path) -> dict[str, Any]:
    verify_backup(path, profile=BackupProfile.RESEARCH)
    with duckdb.connect(str(path), read_only=True) as db:
        tables = {str(row[0]) for row in db.execute("SHOW TABLES").fetchall()}
        missing = sorted(COMPATIBILITY_TABLES - tables)
        if missing:
            raise ValueError("Milestone 33 compatibility tables missing: " + ", ".join(missing))
        def count(table: str, where: str = "") -> int:
            return int(db.execute(f'SELECT COUNT(*) FROM "{table}" {where}').fetchone()[0])
        evidence = {
            "active_catalogue_retrievals": count("security_master_retrievals", "WHERE status='completed'"),
            "price_observations": count("global_price_observations"),
            "sec_fundamental_observations": count("sec_facts"),
            "sec_events": count("sec_event_metadata"),
            "scored_company_brief_availability": int(db.execute(
                "SELECT COUNT(DISTINCT security_id) FROM sec_facts").fetchone()[0]),
        }
    return {"compatible": True, "contract": "milestone-33", "evidence": evidence}


def _plan_token(candidate_fp: dict[str, Any], research_fp: dict[str, Any],
                production_fp: dict[str, Any], expires: int) -> str:
    body = "|".join([candidate_fp["sha256"], str(candidate_fp["byte_count"]),
        research_fp["sha256"], production_fp["sha256"], str(expires)])
    return f"m33-{expires}-{hashlib.sha256(body.encode()).hexdigest()}"


def plan(candidate: Path, research: Path, production: Path, *, bootstrap: Path,
         expected_sha256: str, expected_byte_count: int, staging: bool = True,
         now: datetime | None = None, safety_margin: int = DEFAULT_SAFETY_MARGIN,
         _expires: int | None = None) -> dict[str, Any]:
    candidate, research, production = _validate_paths(candidate, research, production, bootstrap, staging=staging)
    before = fingerprint(production)
    candidate_fp, research_fp = fingerprint(candidate), fingerprint(research)
    if candidate_fp != {"sha256": expected_sha256.lower(), "byte_count": expected_byte_count}:
        raise ValueError("candidate SHA-256 or byte count mismatch")
    evidence = compatibility(candidate)
    free = shutil.disk_usage(research.parent).free
    required_free = candidate_fp["byte_count"] + research_fp["byte_count"] + safety_margin
    if free < required_free:
        raise OSError("insufficient volume capacity for rollback, temporary publication and safety margin")
    after = fingerprint(production)
    if before != after: raise RuntimeError("production fingerprint changed during planning")
    expires = _expires or int((now or datetime.now(timezone.utc)).timestamp()) + PLAN_TTL_SECONDS
    return {"operation": "plan-research-sync", "status": "ready", "plan_id":
        _plan_token(candidate_fp, research_fp, before, expires), "expires_at": expires,
        "candidate": candidate_fp, "active_research": research_fp, "compatibility": evidence,
        "capacity": {"available_bytes": free, "required_free_bytes": required_free,
                     "safety_margin_bytes": safety_margin},
        "production": {"sha256": before["sha256"], "byte_count": before["byte_count"], "unchanged": True},
        "scheduler_enabled": False, "production_publishing_available": False}


def marker_path(volume: Path) -> Path: return volume / "research-maintenance.json"
def lock_path(volume: Path) -> Path: return volume / "research-sync.lock"


@contextmanager
def _maintenance(volume: Path, operation: str, *, quiescence_seconds: float):
    volume.mkdir(parents=True, exist_ok=True)
    lock, marker = lock_path(volume), marker_path(volume)
    descriptor = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        payload = json.dumps({"operation": operation, "created_at": datetime.now(timezone.utc).isoformat(),
                              "pid": os.getpid()})
        os.write(descriptor, payload.encode()); os.fsync(descriptor); os.close(descriptor); descriptor = -1
        marker.write_text(payload, encoding="utf-8"); _fsync_file(marker); _fsync_parent_directory(volume)
        time.sleep(max(0, min(quiescence_seconds, 30)))
        yield
    finally:
        if descriptor >= 0: os.close(descriptor)
        marker.unlink(missing_ok=True); lock.unlink(missing_ok=True)
        _fsync_parent_directory(volume)


def _copy_validated(source: Path, destination: Path, *, milestone33: bool = True) -> dict[str, Any]:
    shutil.copyfile(source, destination)
    result = (compatibility(destination) if milestone33 else
              {"compatible": bool(verify_backup(destination, profile=BackupProfile.RESEARCH))})
    _fsync_file(destination)
    return result


def _write_manifest(volume: Path, payload: dict[str, Any]) -> None:
    path, temporary = volume / "research-sync-manifest.json", volume / ".research-sync-manifest.tmp"
    temporary.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    _fsync_file(temporary); os.replace(temporary, path); _fsync_file(path); _fsync_parent_directory(volume)


def _volume(research: Path, production: Path) -> Path:
    return Path(os.path.commonpath((research.resolve().parent, production.resolve().parent)))


def _publish(source: Path, research: Path, backup_dir: Path, production: Path, *, volume: Path,
             operation: str, validate_after: Callable[[Path], Any] = compatibility) -> dict[str, Any]:
    production_before = fingerprint(production)
    active_fp = fingerprint(research)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    backup_dir.mkdir(parents=True, exist_ok=True)
    rollback = backup_dir / f"research-sync-rollback-{stamp}.duckdb"
    temporary = research.parent / f".research-sync-{stamp}.tmp"
    restored = None
    try:
        _copy_validated(research, rollback, milestone33=False)
        rollback_fp = fingerprint(rollback)
        _copy_validated(source, temporary)
        intended = fingerprint(temporary)
        os.replace(temporary, research); _fsync_file(research); _fsync_parent_directory(research.parent)
        try:
            validate_after(research)
            if fingerprint(research) != intended: raise OSError("published fingerprint mismatch")
        except BaseException:
            recovery = research.parent / f".research-sync-recovery-{stamp}.tmp"
            try:
                _copy_validated(rollback, recovery, milestone33=False); os.replace(recovery, research)
                _fsync_file(research); _fsync_parent_directory(research.parent)
                restored = fingerprint(research) == active_fp
            except BaseException:
                restored = False
            raise RuntimeError(f"post-publication validation failed; automatic_rollback_success={restored}")
        if fingerprint(production) != production_before: raise RuntimeError("production fingerprint changed")
        manifest = {"version": 1, "operation": operation, "status": "completed",
            "completed_at": datetime.now(timezone.utc).isoformat(), "active": intended,
            "rollback": rollback_fp, "production_before": production_before,
            "production_after": fingerprint(production), "scheduler_enabled": False,
            "production_publishing_available": False}
        _write_manifest(volume, manifest)
        return manifest
    finally:
        temporary.unlink(missing_ok=True)
        if fingerprint(production) != production_before:
            raise RuntimeError("production fingerprint changed during synchronization")


def apply(candidate: Path, research: Path, production: Path, *, bootstrap: Path,
          backup_dir: Path, expected_sha256: str, expected_byte_count: int, plan_id: str,
          authorization: str, staging: bool = True, now: datetime | None = None,
          quiescence_seconds: float = 2) -> dict[str, Any]:
    if authorization != APPLY_PHRASE: raise PermissionError("exact replacement authorization phrase required")
    parts = plan_id.split("-")
    current = int((now or datetime.now(timezone.utc)).timestamp())
    if len(parts) != 3 or not parts[1].isdigit() or current > int(parts[1]):
        raise PermissionError("an immediately preceding, unexpired database-bound plan is required")
    report = plan(candidate, research, production, bootstrap=bootstrap, expected_sha256=expected_sha256,
                  expected_byte_count=expected_byte_count, staging=staging, now=now, _expires=int(parts[1]))
    if plan_id != report["plan_id"]:
        raise PermissionError("an immediately preceding, unexpired database-bound plan is required")
    volume = _volume(Path(research), Path(production))
    with _maintenance(volume, "apply", quiescence_seconds=quiescence_seconds):
        # Authorization never substitutes for revalidation inside isolation.
        candidate_path = Path(candidate)
        if candidate_path.is_symlink() or not candidate_path.is_file():
            raise ValueError("candidate ceased to be a regular non-symlink file")
        if fingerprint(candidate_path) != {"sha256": expected_sha256.lower(),
                                           "byte_count": expected_byte_count}:
            raise ValueError("candidate SHA-256 or byte count changed after authorization")
        compatibility(candidate_path.resolve())
        return _publish(Path(candidate).resolve(), Path(research).resolve(), Path(backup_dir).resolve(),
                        Path(production).resolve(), volume=volume, operation="apply")


def rollback(artifact: Path, research: Path, production: Path, *, backup_dir: Path,
             expected_sha256: str, expected_byte_count: int, authorization: str,
             quiescence_seconds: float = 2) -> dict[str, Any]:
    if authorization != ROLLBACK_PHRASE: raise PermissionError("exact rollback authorization phrase required")
    artifact, research, production = _validate_paths(artifact, research, production, artifact.parent, staging=False)
    production_before = fingerprint(production)
    if fingerprint(artifact) != {"sha256": expected_sha256.lower(), "byte_count": expected_byte_count}:
        raise ValueError("rollback SHA-256 or byte count mismatch")
    compatibility(artifact)
    free = shutil.disk_usage(research.parent).free
    if free < research.stat().st_size + artifact.stat().st_size + DEFAULT_SAFETY_MARGIN:
        raise OSError("insufficient capacity to preserve current research database")
    if fingerprint(production) != production_before:
        raise RuntimeError("production fingerprint changed during rollback validation")
    volume = _volume(research, production)
    with _maintenance(volume, "rollback", quiescence_seconds=quiescence_seconds):
        return _publish(artifact, research, backup_dir.resolve(), production, volume=volume, operation="rollback")


def status(research: Path, production: Path, *, backup_dir: Path) -> dict[str, Any]:
    research, production = Path(research).resolve(), Path(production).resolve()
    if not research.is_file() or not production.is_file() or _same_file(research, production):
        raise ValueError("distinct existing research and production files required")
    active, prod = fingerprint(research), fingerprint(production)
    try: comp = compatibility(research)
    except (OSError, ValueError, duckdb.Error): comp = {"compatible": False, "evidence": {}}
    volume = _volume(research, production)
    manifest_path = volume / "research-sync-manifest.json"
    manifest = {}
    if manifest_path.is_file():
        try: manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError): manifest = {}
    backups = sorted(backup_dir.glob("research-sync-rollback-*.duckdb")) if backup_dir.is_dir() else []
    evidence = comp.get("evidence", {})
    if fingerprint(production) != prod:
        raise RuntimeError("production fingerprint changed during status")
    return {"operation": "research-sync-status", "active_research": active,
        "compatibility": bool(comp.get("compatible")),
        "sec_fundamental_observation_count": min(int(evidence.get("sec_fundamental_observations", 0)), 1_000_000_000),
        "sec_event_count": min(int(evidence.get("sec_events", 0)), 1_000_000_000),
        "scored_company_brief_availability_count": min(int(evidence.get("scored_company_brief_availability", 0)), 1_000_000_000),
        "last_synchronization": {"status": manifest.get("status"), "timestamp": manifest.get("completed_at")},
        "rollback_available": bool(backups), "scheduler_enabled": False,
        "production_publishing_available": False,
        "maintenance": marker_path(volume).exists(), "lock": lock_path(volume).exists(),
        "stale_lock_recovery": "verify no sync process is active, inspect lock metadata, then explicitly delete the lock and marker",
        "production": {"sha256": prod["sha256"], "byte_count": prod["byte_count"],
            "unchanged": bool(manifest and manifest.get("production_after") == prod)}}
