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
    return BackupVerification(path=path, tables=tables)


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
) -> Path:
    """Create, validate, atomically publish, and prune a DuckDB backup."""
    if retention_count < 1:
        raise ValueError("retention_count must be at least one")
    source = database_path.expanduser().resolve()
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
    elif args.command == "verify":
        result = verify_backup(args.backup)
        print(json.dumps({"status": "validated", "backup": str(result.path),
                          "tables": sorted(result.tables)}))
    else:
        result = restore_backup(args.backup, args.destination)
        print(json.dumps({"status": "restored", "database": str(result)}))


if __name__ == "__main__":
    main()
