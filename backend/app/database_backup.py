"""Consistent, application-managed DuckDB backups.

Backups are built with DuckDB's ``EXPORT DATABASE`` and ``IMPORT DATABASE``
operations rather than a filesystem copy, so an open live database is never
copied in an inconsistent state. The live database is opened read-only.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import duckdb

from .config import get_settings


REQUIRED_TABLES = frozenset({"price_bars", "prediction_vintages", "prediction_records"})
BACKUP_PREFIX = "signallens-backup-"


def _sql_string(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


@dataclass(frozen=True)
class BackupVerification:
    path: Path
    tables: frozenset[str]
    sha256: str


def verify_backup(path: Path) -> BackupVerification:
    """Open a backup read-only and prove the required application tables exist."""
    path = path.expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Backup does not exist: {path}")
    connection = duckdb.connect(str(path), read_only=True)
    try:
        tables = frozenset(row[0] for row in connection.execute("SHOW TABLES").fetchall())
        missing = sorted(REQUIRED_TABLES - tables)
        if missing:
            raise ValueError(f"Backup is missing required tables: {', '.join(missing)}")
        for table in sorted(REQUIRED_TABLES):
            connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()
    finally:
        connection.close()
    return BackupVerification(path=path, tables=tables, sha256=hashlib.sha256(path.read_bytes()).hexdigest())


def validated_backups(directory: Path) -> list[Path]:
    """Return newest-first application backups which pass read-only validation."""
    directory = directory.expanduser().resolve()
    if not directory.is_dir():
        return []
    candidates = sorted(
        directory.glob(f"{BACKUP_PREFIX}*.duckdb"),
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )
    valid: list[Path] = []
    for candidate in candidates:
        try:
            verify_backup(candidate)
        except (OSError, duckdb.Error, ValueError):
            continue
        valid.append(candidate)
    return valid


def create_backup(
    database_path: Path,
    backup_directory: Path = Path("/data/backups"),
    *,
    retention_count: int = 3,
    now: datetime | None = None,
    production_database_path: Path | None = None,
) -> Path:
    """Create, validate, atomically publish, and prune a DuckDB backup."""
    if retention_count < 1:
        raise ValueError("retention_count must be at least one")
    source = database_path.expanduser().resolve()
    if production_database_path is not None and source == production_database_path.expanduser().resolve():
        raise ValueError("production database backup is prohibited by research backup workflow")
    if not source.is_file():
        raise FileNotFoundError(f"Live database does not exist: {source}")
    destination_dir = backup_directory.expanduser().resolve()
    destination_dir.mkdir(parents=True, exist_ok=True)
    captured_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    stamp = captured_at.strftime("%Y%m%dT%H%M%S.%fZ")
    final = destination_dir / f"{BACKUP_PREFIX}{stamp}.duckdb"
    temporary = destination_dir / f".{BACKUP_PREFIX}{stamp}-{uuid4().hex}.tmp"
    export_directory = destination_dir / f".{BACKUP_PREFIX}{stamp}-{uuid4().hex}.export"

    connection = None
    try:
        # EXPORT observes one consistent snapshot; IMPORT builds a wholly new
        # database. The live source connection is explicitly read-only.
        connection = duckdb.connect(str(source), read_only=True)
        connection.execute(f"EXPORT DATABASE {_sql_string(str(export_directory))}")
        connection.close()
        connection = None
        connection = duckdb.connect(str(temporary))
        connection.execute(f"IMPORT DATABASE {_sql_string(str(export_directory))}")
        connection.close()
        connection = None
        verify_backup(temporary)
        os.replace(temporary, final)
        # Directory durability matters for the atomic rename on abrupt restart.
        directory_fd = os.open(destination_dir, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except BaseException:
        if connection is not None:
            connection.close()
        temporary.unlink(missing_ok=True)
        raise
    finally:
        shutil.rmtree(export_directory, ignore_errors=True)

    for stale in validated_backups(destination_dir)[retention_count:]:
        stale.unlink()
    return final


def create_initial_research_backup(
    research_database_path: Path,
    production_database_path: Path,
    backup_directory: Path,
    *,
    authorized: bool = False,
    retention_count: int = 3,
) -> Path:
    """Create only the first managed research backup after explicit authorization."""
    if not authorized:
        raise PermissionError("explicit initial research backup authorization is required")
    destination = backup_directory.expanduser().resolve()
    existing = list(destination.glob(f"{BACKUP_PREFIX}*.duckdb")) if destination.is_dir() else []
    if existing:
        raise FileExistsError("a managed backup already exists; no files were changed")
    return create_backup(
        research_database_path,
        destination,
        retention_count=retention_count,
        production_database_path=production_database_path,
    )


def restore_plan(source: Path, destination: Path) -> dict[str, object]:
    """Read-only verification and plan. Deliberately performs no restore."""
    verified = verify_backup(source)
    target = destination.expanduser().resolve()
    return {"status": "verified", "sha256": verified.sha256, "required_tables": sorted(REQUIRED_TABLES),
            "destination_exists": target.exists(), "automatic_restore": False}


def backup_status(directory: Path | None, *, configured: bool = True) -> dict[str, object]:
    """Bounded status without disclosing filesystem paths.

    Only files with the application-managed prefix are evidence of a managed
    backup.  Other files in the directory are deliberately neither adopted nor
    removed.
    """
    if not configured or directory is None:
        return {"status": "not_configured", "validated_count": 0, "latest_at": None}
    directory = directory.expanduser().resolve()
    candidates = list(directory.glob(f"{BACKUP_PREFIX}*.duckdb")) if directory.is_dir() else []
    backups = validated_backups(directory)
    if not backups:
        return {"status": "unvalidated" if candidates else "missing",
                "validated_count": 0, "latest_at": None}
    latest = backups[0]
    verification = verify_backup(latest)
    return {"status": "validated", "validated_count": min(len(backups), 100),
            "latest_at": datetime.fromtimestamp(latest.stat().st_mtime, timezone.utc).isoformat(),
            "sha256": verification.sha256}


def restore_backup(source: Path, destination: Path) -> Path:
    """Restore into an absent path; never overwrite a live database."""
    verified = verify_backup(source)
    destination = destination.expanduser().resolve()
    if destination.exists():
        raise FileExistsError(f"Restore destination already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{uuid4().hex}.restore")
    export_directory = destination.with_name(f".{destination.name}.{uuid4().hex}.export")
    connection = duckdb.connect(str(verified.path), read_only=True)
    try:
        connection.execute(f"EXPORT DATABASE {_sql_string(str(export_directory))}")
    finally:
        connection.close()
    try:
        connection = duckdb.connect(str(temporary))
        try:
            connection.execute(f"IMPORT DATABASE {_sql_string(str(export_directory))}")
        finally:
            connection.close()
        verify_backup(temporary)
        os.replace(temporary, destination)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    finally:
        shutil.rmtree(export_directory, ignore_errors=True)
    return destination


def main() -> None:
    parser = argparse.ArgumentParser(description="Create or verify SignalLens DuckDB backups")
    subparsers = parser.add_subparsers(dest="command", required=True)
    create = subparsers.add_parser("create", help="create and validate a backup")
    create.add_argument("--database", type=Path)
    create.add_argument("--destination", type=Path)
    create.add_argument("--retention", type=int)
    initial = subparsers.add_parser(
        "create-initial",
        help="create the first managed backup of the isolated research database",
    )
    initial.add_argument("--destination", type=Path)
    initial.add_argument("--retention", type=int)
    initial.add_argument(
        "--authorize",
        required=True,
        help='must be exactly "CREATE INITIAL RESEARCH BACKUP"',
    )
    verify = subparsers.add_parser("verify", help="verify a backup read-only")
    verify.add_argument("backup", type=Path)
    restore = subparsers.add_parser("restore", help="restore into a path which does not exist")
    restore.add_argument("backup", type=Path)
    restore.add_argument("destination", type=Path)
    args = parser.parse_args()
    settings = get_settings()
    if args.command == "create":
        result = create_backup(
            args.database or settings.database_path,
            args.destination or settings.backup_path,
            retention_count=args.retention or settings.backup_retention_count,
        )
        print(json.dumps({"status": "validated", "backup": str(result)}))
    elif args.command == "create-initial":
        if args.authorize != "CREATE INITIAL RESEARCH BACKUP":
            parser.error("explicit authorization phrase is required")
        destination = args.destination or settings.backup_path
        if destination is None:
            parser.error("a backup destination must be configured or supplied")
        result = create_initial_research_backup(
            settings.research_database_path,
            settings.database_path,
            destination,
            authorized=True,
            retention_count=args.retention or settings.backup_retention_count,
        )
        print(json.dumps({"status": "validated", "backup": str(result)}))
    elif args.command == "verify":
        result = verify_backup(args.backup)
        print(json.dumps({"status": "validated", "backup": str(result.path),
                          "tables": sorted(result.tables)}))
    else:
        result = restore_backup(args.backup, args.destination)
        print(json.dumps({"status": "restored", "database": str(result)}))


if __name__ == "__main__":
    main()
