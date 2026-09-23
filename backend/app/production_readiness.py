from __future__ import annotations

import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import duckdb

from .config import Settings


REQUIRED_TABLES = {"price_bars", "prediction_vintages", "prediction_records"}


def _check(name: str, ok: bool, detail: str) -> dict[str, Any]:
    return {"name": name, "status": "pass" if ok else "fail", "detail": detail}


def run_preflight(settings: Settings, *, now: datetime | None = None) -> dict[str, Any]:
    """Validate the production writer without changing the database or its directory."""
    checked_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    path = settings.database_path.expanduser().resolve()
    checks: list[dict[str, Any]] = []
    checks.append(_check("database_exists", path.is_file(), f"database={path}"))

    volume = settings.persistent_volume_path
    if volume is None:
        checks.append(_check("persistent_volume", False, "SIGNALLENS_PERSISTENT_VOLUME_PATH is not set"))
    else:
        resolved_volume = volume.expanduser().resolve()
        on_volume = path == resolved_volume or resolved_volume in path.parents
        writable = False
        detail = f"volume={resolved_volume}; database_on_volume={on_volume}"
        if resolved_volume.is_dir():
            try:
                descriptor, probe = tempfile.mkstemp(prefix=".signallens-preflight-", dir=resolved_volume)
                os.close(descriptor)
                Path(probe).unlink()
                writable = True
            except OSError as exc:
                detail += f"; write_probe={type(exc).__name__}"
        else:
            detail += "; mount_missing"
        checks.append(_check("persistent_volume", on_volume and writable, detail))

    if path.is_file():
        try:
            connection = duckdb.connect(str(path), read_only=True)
            tables = {row[0] for row in connection.execute("SHOW TABLES").fetchall()}
            connection.execute("SELECT COUNT(*) FROM price_bars").fetchone()
            connection.close()
            missing = sorted(REQUIRED_TABLES - tables)
            checks.append(_check("database_readable", not missing, f"missing_tables={missing}"))
        except Exception as exc:
            checks.append(_check("database_readable", False, f"read_only_open={type(exc).__name__}"))
    else:
        checks.append(_check("database_readable", False, "database file is missing"))

    missing_secrets = [
        name for name, value in (
            ("SIGNALLENS_SEC_USER_AGENT", settings.sec_user_agent),
            ("SIGNALLENS_FRED_API_KEY", settings.fred_api_key),
            ("SIGNALLENS_API_TOKEN", settings.api_token),
        ) if not value
    ]
    checks.append(_check("required_secrets", not missing_secrets, f"missing={missing_secrets}"))
    checks.append(_check(
        "upstream_configuration",
        bool(settings.sec_user_agent and settings.fred_api_key),
        "providers=yfinance,sec_edgar,fred,google_news_rss",
    ))

    backup = settings.backup_path
    latest: Path | None = None
    if backup:
        backup = backup.expanduser().resolve()
        candidates = [backup] if backup.is_file() else ([item for item in backup.iterdir() if item.is_file()] if backup.is_dir() else [])
        latest = max(candidates, key=lambda item: item.stat().st_mtime, default=None)
    age_hours = ((checked_at.timestamp() - latest.stat().st_mtime) / 3600) if latest else None
    fresh = age_hours is not None and 0 <= age_hours <= settings.backup_max_age_hours
    checks.append(_check("latest_backup", fresh, f"latest={latest}; age_hours={round(age_hours, 1) if age_hours is not None else None}; max_age_hours={settings.backup_max_age_hours}"))

    return {
        "command": "production_preflight",
        "checked_at": checked_at.isoformat(),
        "status": "ready" if all(item["status"] == "pass" for item in checks) else "not_ready",
        "checks": checks,
    }
