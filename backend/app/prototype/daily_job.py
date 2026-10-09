"""Runs the holding alerts once each weekday inside the backend process.

Off unless SIGNALLENS_ALERTS_ENABLED=true. At SIGNALLENS_ALERTS_UTC_TIME (default
22:30, after the US close) on weekdays, the job takes the research maintenance
lock: API requests that would read the databases get "temporarily unavailable"
(503, retry in seconds) for the few minutes the update writes, because DuckDB
allows one writer and no simultaneous readers. If another maintenance operation
holds the lock, the job waits and tries again a minute later. A run missed while
the service was down happens once at the next start on the same weekday.
The outcome is kept in daily-alerts-state.json on the volume.
"""
from __future__ import annotations

from datetime import datetime, time as clock_time, timedelta, timezone
import json
import logging
import os
from pathlib import Path
import threading

from ..research_sync import _maintenance

log = logging.getLogger(__name__)
STATE_FILE = 'daily-alerts-state.json'


def volume_path(settings) -> Path:
    return (settings.persistent_volume_path or settings.research_database_path.parent).expanduser().resolve()


def run_time(settings) -> clock_time:
    hours, minutes = (int(x) for x in str(settings.alerts_utc_time).split(':'))
    return clock_time(hours, minutes, tzinfo=timezone.utc)


class DailyAlertJob:
    def __init__(self, settings, *, runner=None, notifier_factory=None, quiescence_seconds=5):
        self.settings, self.quiescence = settings, quiescence_seconds
        self.runner = runner
        self.notifier_factory = notifier_factory
        self.state_path = volume_path(settings) / STATE_FILE
        self._stop = threading.Event()

    def state(self) -> dict:
        try: return json.loads(self.state_path.read_text(encoding='utf-8'))
        except (OSError, ValueError): return {}

    def _save(self, state: dict) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.state_path.with_suffix('.tmp')
        temporary.write_text(json.dumps(state, sort_keys=True), encoding='utf-8')
        os.replace(temporary, self.state_path)

    def due(self, now: datetime) -> bool:
        now = now.astimezone(timezone.utc)
        if now.weekday() >= 5 or now.timetz() < run_time(self.settings): return False
        return self.state().get('last_success_date') != now.date().isoformat()

    def next_run(self, now: datetime) -> str:
        """When the next run happens: now if one is due, else the next weekday at the run time."""
        now = now.astimezone(timezone.utc)
        if self.due(now): return now.isoformat()
        done = self.state().get('last_success_date')
        for offset in range(8):
            day = now.date() + timedelta(days=offset)
            at = datetime.combine(day, run_time(self.settings))
            if day.weekday() < 5 and at > now and done != day.isoformat(): return at.isoformat()
        return ''

    def run_once(self, now: datetime | None = None) -> dict:
        from . import alerts
        now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        state = self.state()
        state.update(last_attempt_at=now.isoformat())
        try:
            with _maintenance(volume_path(self.settings), 'daily-alerts', quiescence_seconds=self.quiescence):
                notifier = (self.notifier_factory or (lambda: alerts.TelegramNotifier(
                    os.environ.get('SIGNALLENS_TELEGRAM_BOT_TOKEN', ''), os.environ.get('SIGNALLENS_TELEGRAM_CHAT_ID', ''))))()
                result = (self.runner or alerts.run)(notifier=notifier, update=True, now=now)
        except FileExistsError:
            state.update(last_status='waiting_for_maintenance_lock')
            self._save(state); return state
        except Exception as exc:  # never let the thread die; tokens live in URLs, so no exception text
            state.update(last_status='failed', last_error=type(exc).__name__)
            self._save(state); log.warning('daily alerts failed: %s', type(exc).__name__); return state
        state.update(last_status='completed', last_error=None, last_success_date=now.date().isoformat(),
                     last_finished_at=datetime.now(timezone.utc).isoformat(), last_messages=len(result.get('messages', [])),
                     last_steps={k: (v or {}).get('status') for k, v in (result.get('steps') or {}).items() if isinstance(v, dict)})
        self._save(state); return state

    def loop(self, poll_seconds: int = 60) -> None:
        while not self._stop.is_set():
            try:
                if self.due(datetime.now(timezone.utc)): self.run_once()
            except Exception as exc:
                log.warning('daily alerts loop error: %s', type(exc).__name__)
            self._stop.wait(poll_seconds)

    def start(self) -> threading.Thread:
        thread = threading.Thread(target=self.loop, name='signallens-daily-alerts', daemon=True)
        thread.start()
        return thread

    def stop(self) -> None:
        self._stop.set()
