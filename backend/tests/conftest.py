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


@pytest.fixture(scope='session')
def _prototype_fixture_template(tmp_path_factory):
    """Build the synthetic prototype databases once per test run (about 9 s)."""
    from app.prototype.fixture import create_fixture
    return create_fixture(tmp_path_factory.mktemp('prototype-template') / 'new-synthetic-fixture')


@pytest.fixture
def prototype_fixture(_prototype_fixture_template, tmp_path):
    """A fresh, private copy of the synthetic databases for each test, so tests
    can still modify them independently. Returns (research, production)."""
    import shutil
    folder = tmp_path / 'new-synthetic-fixture'
    folder.mkdir()
    return tuple(shutil.copy2(source, folder / source.name) for source in _prototype_fixture_template)
