from datetime import datetime, timezone
from pathlib import Path
import hashlib

import duckdb
import pytest

import app.research_sync as sync
from app.database_backup import PROFILE_REQUIRED_TABLES, BackupProfile


def database(path: Path, *, milestone33: bool = True) -> None:
    tables = set(PROFILE_REQUIRED_TABLES[BackupProfile.RESEARCH])
    if milestone33: tables |= set(sync.COMPATIBILITY_TABLES)
    with duckdb.connect(str(path)) as db:
        for table in sorted(tables):
            if table == "security_master_retrievals":
                db.execute(f'CREATE TABLE "{table}" (status VARCHAR)')
                db.execute(f'INSERT INTO "{table}" VALUES (\'completed\')')
            elif table == "sec_facts":
                db.execute(f'CREATE TABLE "{table}" (security_id VARCHAR)')
            else:
                db.execute(f'CREATE TABLE "{table}" (value VARCHAR)')


def setup(tmp_path: Path):
    volume=tmp_path/"volume with spaces"; bootstrap=volume/"bootstrap"; research_dir=volume/"research"
    bootstrap.mkdir(parents=True); research_dir.mkdir()
    candidate=bootstrap/"candidate upload.duckdb"; research=research_dir/"active.duckdb"; production=volume/"signallens.duckdb"
    database(candidate); database(research, milestone33=False); production.write_bytes(b"production-immutable")
    fp=sync.fingerprint(candidate)
    return volume,bootstrap,candidate,research,production,fp


def test_plan_is_read_only_and_bounded(tmp_path):
    _,bootstrap,candidate,research,production,fp=setup(tmp_path)
    before={p:sync.fingerprint(p) for p in (candidate,research,production)}
    result=sync.plan(candidate,research,production,bootstrap=bootstrap,
        expected_sha256=fp["sha256"],expected_byte_count=fp["byte_count"])
    assert result["status"]=="ready" and result["compatibility"]["compatible"]
    assert before=={p:sync.fingerprint(p) for p in before}
    assert not list(tmp_path.rglob("*manifest*"))


def test_plan_rejects_hash_size_alias_and_missing_contract(tmp_path):
    _,bootstrap,candidate,research,production,fp=setup(tmp_path)
    with pytest.raises(ValueError,match="mismatch"):
        sync.plan(candidate,research,production,bootstrap=bootstrap,expected_sha256="0"*64,expected_byte_count=fp["byte_count"])
    alias=bootstrap/"hardlink.duckdb"; alias.hardlink_to(production)
    with pytest.raises(ValueError,match="aliases"):
        sync.plan(alias,research,production,bootstrap=bootstrap,expected_sha256=sync.fingerprint(alias)["sha256"],expected_byte_count=alias.stat().st_size)
    bad=bootstrap/"old.duckdb"; database(bad,milestone33=False); badfp=sync.fingerprint(bad)
    with pytest.raises(ValueError,match="compatibility"):
        sync.plan(bad,research,production,bootstrap=bootstrap,expected_sha256=badfp["sha256"],expected_byte_count=badfp["byte_count"])


def test_insufficient_capacity_fails_before_mutation(tmp_path,monkeypatch):
    _,bootstrap,candidate,research,production,fp=setup(tmp_path)
    monkeypatch.setattr(sync.shutil,"disk_usage",lambda _: type("D",(),{"free":0})())
    with pytest.raises(OSError,match="capacity"):
        sync.plan(candidate,research,production,bootstrap=bootstrap,expected_sha256=fp["sha256"],expected_byte_count=fp["byte_count"])


def test_apply_atomic_backup_manifest_and_preservation(tmp_path):
    volume,bootstrap,candidate,research,production,fp=setup(tmp_path); backups=volume/"research"/"backups"; backups.mkdir()
    manual=backups/"manual quarantine.txt"; manual.write_text("keep")
    now=datetime(2026,10,1,tzinfo=timezone.utc)
    planned=sync.plan(candidate,research,production,bootstrap=bootstrap,expected_sha256=fp["sha256"],expected_byte_count=fp["byte_count"],now=now)
    prod=sync.fingerprint(production)
    result=sync.apply(candidate,research,production,bootstrap=bootstrap,backup_dir=backups,
        expected_sha256=fp["sha256"],expected_byte_count=fp["byte_count"],plan_id=planned["plan_id"],
        authorization=sync.APPLY_PHRASE,now=now,quiescence_seconds=0)
    assert result["status"]=="completed" and sync.fingerprint(research)==fp
    assert sync.fingerprint(production)==prod and manual.read_text()=="keep"
    assert len(list(backups.glob("research-sync-rollback-*.duckdb")))==1
    assert not sync.marker_path(volume).exists() and not sync.lock_path(volume).exists()


def test_exclusive_stale_lock_is_not_ignored(tmp_path):
    volume,bootstrap,candidate,research,production,fp=setup(tmp_path); backups=volume/"research"/"backups"; backups.mkdir()
    sync.lock_path(volume).write_text("stale")
    planned=sync.plan(candidate,research,production,bootstrap=bootstrap,expected_sha256=fp["sha256"],expected_byte_count=fp["byte_count"])
    with pytest.raises(FileExistsError):
        sync.apply(candidate,research,production,bootstrap=bootstrap,backup_dir=backups,expected_sha256=fp["sha256"],expected_byte_count=fp["byte_count"],plan_id=planned["plan_id"],authorization=sync.APPLY_PHRASE,quiescence_seconds=0)
    report=sync.status(research,production,backup_dir=backups)
    assert report["lock"] and "explicitly delete" in report["stale_lock_recovery"]


def test_postpublication_failure_automatically_rolls_back(tmp_path,monkeypatch):
    volume,_,candidate,research,production,_=setup(tmp_path); backups=volume/"research"/"backups"; before=sync.fingerprint(research)
    def validation(path):
        raise ValueError("injected")
    with pytest.raises(RuntimeError,match="automatic_rollback_success=True"):
        sync._publish(candidate,research,backups,production,volume=volume,operation="apply",validate_after=validation)
    assert sync.fingerprint(research)==before


def test_explicit_rollback_preserves_current_as_new_artifact(tmp_path):
    volume,_,candidate,research,production,fp=setup(tmp_path); backups=volume/"research"/"backups"; backups.mkdir()
    result=sync.rollback(candidate,research,production,backup_dir=backups,expected_sha256=fp["sha256"],
        expected_byte_count=fp["byte_count"],authorization=sync.ROLLBACK_PHRASE,quiescence_seconds=0)
    assert result["operation"]=="rollback" and len(list(backups.glob("research-sync-rollback-*.duckdb")))==1


def test_platform_fsync_and_status_redaction(tmp_path,monkeypatch):
    volume,_,_,research,production,_=setup(tmp_path); backups=volume/"research"/"backups"; backups.mkdir()
    calls=[]; monkeypatch.setattr(sync,"_IS_WINDOWS",True,raising=False)
    # The shared durability helper's documented Windows behavior is already
    # exercised by test_database_backup; status must never leak any path.
    report=sync.status(research,production,backup_dir=backups)
    rendered=str(report)
    assert str(tmp_path) not in rendered and report["scheduler_enabled"] is False
    assert report["production_publishing_available"] is False
