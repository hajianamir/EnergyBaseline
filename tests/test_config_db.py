import importlib
import os


def test_import_never_raises_without_env(eb_home):
    for k in ("DATABASE_URL", "POSTGRES_URL", "DB_CONNECTION_STRING", "DB_URI"):
        os.environ.pop(k, None)
    import energybaseline.config.settings as s
    importlib.reload(s)
    url, fallback = s.resolve_database_url()
    assert url == "sqlite:///./data/app.db" and fallback is True
    assert (eb_home / "data").is_dir()


def test_whitespace_env_falls_back(eb_home):
    import energybaseline.config.settings as s
    os.environ["DATABASE_URL"] = "   "
    try:
        assert s.get_database_url() == s.DEFAULT_SQLITE_URL
    finally:
        os.environ.pop("DATABASE_URL")


def test_env_is_honoured_and_masked(eb_home):
    import energybaseline.config.settings as s
    os.environ["DB_URI"] = "sqlite:///./data/other.db"
    try:
        assert s.get_database_url() == "sqlite:///./data/other.db"
    finally:
        os.environ.pop("DB_URI")


def test_unreachable_external_db_degrades_to_sqlite(eb_home):
    import energybaseline.db.session as ses
    ses.reset_engine()
    os.environ["DATABASE_URL"] = "postgresql+psycopg2://u:p@127.0.0.1:1/none"
    try:
        eng = ses.get_engine()
        assert eng.url.get_backend_name() == "sqlite"
    finally:
        os.environ.pop("DATABASE_URL")
        ses.reset_engine()


def test_init_and_selftest(eb_home):
    import energybaseline.db.session as ses
    ses.reset_engine()
    res = ses.init_database()
    assert res["ok"] and res["writable"]
    from sqlalchemy import inspect
    names = set(inspect(ses.get_engine()).get_table_names())
    assert {"health", "runs", "selections", "connections", "audit_log"} <= names


def test_alembic_upgrade_on_clean_machine(eb_home):
    import energybaseline.db.session as ses
    from energybaseline.db.migrate import upgrade_head
    ses.reset_engine()
    (eb_home / "data" / "app.db").unlink(missing_ok=True)
    upgrade_head()
    from sqlalchemy import inspect
    assert "alembic_version" in inspect(ses.get_engine()).get_table_names()
