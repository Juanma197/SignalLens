"""Weekday alert job: timing, maintenance lock, state, failures (offline)."""
from datetime import datetime, timezone

from app.config import Settings
from app.prototype.daily_job import DailyAlertJob
from app.research_sync import lock_path, marker_path

THURSDAY_EVENING = datetime(2026, 10, 8, 22, 45, tzinfo=timezone.utc)


def job(tmp_path, runner=None, **settings):
    s = Settings(persistent_volume_path=tmp_path, research_database_path=tmp_path / 'research' / 'r.duckdb',
                 database_path=tmp_path / 'p.duckdb', alerts_enabled=True, **settings)
    return DailyAlertJob(s, runner=runner, notifier_factory=lambda: 'notifier', quiescence_seconds=0)


def test_due_on_weekdays_after_the_run_time_once_per_day(tmp_path):
    j = job(tmp_path)
    assert not j.due(datetime(2026, 10, 8, 22, 0, tzinfo=timezone.utc))      # before 22:30
    assert j.due(THURSDAY_EVENING)
    assert not j.due(datetime(2026, 10, 10, 23, 0, tzinfo=timezone.utc))     # Saturday
    assert j.next_run(datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)) == '2026-10-08T22:30:00+00:00'
    assert j.next_run(datetime(2026, 10, 9, 23, 0, tzinfo=timezone.utc)) == '2026-10-09T23:00:00+00:00'  # missed Friday run: now
    assert job(tmp_path, alerts_utc_time='06:15').next_run(datetime(2026, 10, 10, 9, tzinfo=timezone.utc)) == '2026-10-12T06:15:00+00:00'


def test_run_holds_the_maintenance_marker_and_records_success(tmp_path):
    seen = {}
    def runner(*, notifier, update, now):
        seen.update(marker=marker_path(tmp_path).is_file(), notifier=notifier, update=update)
        return {'messages': ['x'], 'steps': {'prices': {'status': 'completed'}}}
    j = job(tmp_path, runner=runner)
    state = j.run_once(THURSDAY_EVENING)
    assert seen == {'marker': True, 'notifier': 'notifier', 'update': True}
    assert not marker_path(tmp_path).exists() and not lock_path(tmp_path).exists()
    assert state['last_status'] == 'completed' and state['last_messages'] == 1 and state['last_steps'] == {'prices': 'completed'}
    assert not j.due(THURSDAY_EVENING) and j.state()['last_success_date'] == '2026-10-08'


def test_busy_lock_and_failures_are_recorded_and_retried(tmp_path):
    lock_path(tmp_path).write_text('{}')
    j = job(tmp_path, runner=lambda **_: {'messages': []})
    assert j.run_once(THURSDAY_EVENING)['last_status'] == 'waiting_for_maintenance_lock' and j.due(THURSDAY_EVENING)
    lock_path(tmp_path).unlink()
    def broken(**_): raise RuntimeError('https://api.telegram.org/botSECRET/sendMessage')
    failed = job(tmp_path, runner=broken).run_once(THURSDAY_EVENING)
    assert failed['last_status'] == 'failed' and failed['last_error'] == 'RuntimeError' and 'SECRET' not in str(failed)
    assert not marker_path(tmp_path).exists() and job(tmp_path).due(THURSDAY_EVENING)  # retried next minute
