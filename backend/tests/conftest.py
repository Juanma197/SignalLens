import duckdb
import pytest


@pytest.fixture
def duckdb_session_timezone(request, monkeypatch):
    """Set every fixture and consumer connection, independently of host timezone."""
    original = duckdb.connect
    session_timezone = request.param

    def connect(*args, **kwargs):
        db = original(*args, **kwargs)
        db.execute("SET TimeZone = ?", [session_timezone])
        return db

    monkeypatch.setattr(duckdb, 'connect', connect)
    return session_timezone
