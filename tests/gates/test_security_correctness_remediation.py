"""Behavioral regressions for safe diagnostics, SQL binding and feature contracts."""

from __future__ import annotations

import json
import logging
import os
import sqlite3

import pytest

from energybaseline.config import settings
from energybaseline.core import features, secrets, sqlsource
from energybaseline.db import session as db

CANARY = "REMEDIATION_TEST_ONLY_VALUE"


@pytest.mark.parametrize(
    "url",
    [
        f"postgresql://u:{CANARY}@host/db",
        f"postgresql://host/db?password={CANARY}",
        f"DRIVER={{x}};PWD={{{CANARY};suffix}};UID=u",
        f"mssql+pyodbc:///?odbc_connect=PWD%3D{CANARY}",
        f"postgresql://host/db?unrecognized_vendor_option={CANARY}",
        f"not-a-url:{CANARY}",
    ],
)
def test_safe_database_labels_never_echo_connection_secrets(url):
    assert CANARY not in settings.mask_database_url(url)


def test_selftest_and_status_never_export_raw_external_url(tmp_path, monkeypatch, caplog):
    db.reset_engine()
    monkeypatch.setenv("EB_HOME", str(tmp_path))
    monkeypatch.setenv("DATABASE_URL", f"postgresql://u:{CANARY}@host/db?password={CANARY}")
    monkeypatch.delenv("EB_DB_STRICT", raising=False)
    engine = db._make_sqlite_engine("sqlite:///:memory:", is_memory=True)
    db.Base.metadata.create_all(engine)
    monkeypatch.setattr(db, "_probe_external_engine", lambda *args: (engine, "", 0.1))
    try:
        with caplog.at_level(logging.INFO, logger="energybaseline"):
            db.get_engine()
            result = db.self_test()
            status = db.db_status()
        assert result["ok"]
        assert CANARY not in json.dumps(result)
        assert CANARY not in json.dumps(status)
        assert CANARY not in caplog.text
    finally:
        db.reset_engine()


def test_probe_exception_text_is_not_a_diagnostic_channel(monkeypatch):
    def unavailable(*args, **kwargs):
        raise RuntimeError(f"driver included password={CANARY}")

    monkeypatch.setattr(db, "create_engine", unavailable)
    engine, error, _ = db._probe_external_engine(f"postgresql://u:{CANARY}@host/db")
    assert engine is None and "RuntimeError" in error and CANARY not in error


def test_strict_policy_refuses_local_and_cached_fallback(tmp_path, monkeypatch):
    db.reset_engine()
    monkeypatch.setenv("EB_HOME", str(tmp_path))
    monkeypatch.setenv("DATABASE_URL", "postgresql://invalid/review")
    monkeypatch.setattr(db, "_probe_external_engine", lambda *args: (None, "unavailable", 0.1))
    monkeypatch.setenv("EB_DB_STRICT", "1")
    try:
        with pytest.raises(db.ExternalDatabaseRequired):
            db.get_engine()
        assert not list(tmp_path.rglob("*.db"))
        db.get_engine(strict_external=False)
        with pytest.raises(db.ExternalDatabaseRequired):
            db.get_engine(strict_external=True)
    finally:
        db.reset_engine()


def test_storage_boolean_configuration_is_not_truthy_string(monkeypatch):
    monkeypatch.setenv("EB_DB_STRICT", "false")
    assert settings.external_database_required() is False
    monkeypatch.setenv("EB_DB_STRICT", "probably")
    with pytest.raises(ValueError):
        settings.external_database_required()


@pytest.fixture
def no_secure_store(monkeypatch, tmp_path):
    def unavailable(*args):
        raise secrets.CredentialStorageUnavailable("Provider unavailable in this unit fixture")

    # Patch the actual provider boundary used by the product. Patching the
    # public keyring facade does not disable Windows' approved native backend.
    # Genuine OS behavior is separately proved by remediation_credentials.py.
    monkeypatch.setattr(secrets, "_secure_keyring", unavailable)
    monkeypatch.setenv("EB_HOME", str(tmp_path))
    monkeypatch.delenv("EB_ALLOW_INSECURE_KEYFILE", raising=False)
    monkeypatch.setattr(secrets.sys, "platform", "linux")
    monkeypatch.setattr(secrets, "_session_key", None)
    return tmp_path


def test_secure_store_unavailable_defaults_to_session_only(no_secure_store, monkeypatch):
    token = secrets.encrypt_text(CANARY)
    assert token.startswith("session-fernet:")
    assert secrets.decrypt_text(token) == CANARY
    assert not secrets.backend_status()["persistent"]
    assert not list(no_secure_store.rglob(".eb_master.key"))
    monkeypatch.setattr(secrets, "_session_key", None)
    with pytest.raises(secrets.CredentialStorageUnavailable):
        secrets.decrypt_text(token)
    assert not list(no_secure_store.rglob(".eb_master.key"))


def test_legacy_decryption_does_not_create_a_replacement_key(no_secure_store):
    with pytest.raises(secrets.CredentialStorageUnavailable):
        secrets.decrypt_text("fernet:unavailable")
    assert not list(no_secure_store.rglob(".eb_master.key"))


def test_file_key_requires_opt_in_and_is_private(no_secure_store, monkeypatch):
    monkeypatch.setenv("EB_ALLOW_INSECURE_KEYFILE", "true")
    token = secrets.encrypt_text(CANARY)
    key = no_secure_store / "data" / ".eb_master.key"
    assert key.is_file()
    if os.name == "posix":
        assert key.stat().st_mode & 0o077 == 0
    assert secrets.decrypt_text(token) == CANARY
    assert secrets.backend_status()["persistent"] and not secrets.backend_status()["secure_store"]


def test_bound_builder_executes_real_read_only_sqlite(tmp_path):
    path = tmp_path / "source.sqlite"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE meters (ts TEXT, reading REAL, tag TEXT)")
        conn.executemany("INSERT INTO meters VALUES (?, ?, ?)", [("2025-01-01", 1, "a"), ("2025-02-01", 2, "b")])
    profile = sqlsource.ConnectionProfile("review", database=str(path))
    query = sqlsource.build_query("meters", ["ts", "reading"], "ts", "2025-01-15", "2025-03-01")
    assert "2025-01-15" not in query.sql and query.params["eb_start"] == "2025-01-15"
    assert sqlsource.run_query(profile, query)["reading"].tolist() == [2.0]
    assert sqlsource.run_query(
        profile, "SELECT reading FROM meters WHERE tag=:value", params={"value": "a' OR 1=1 --"}
    ).empty
    with pytest.raises(sqlsource.SourceError, match="parameters"):
        sqlsource.run_query(profile, query.sql)
    with pytest.raises(sqlsource.SourceError):
        sqlsource.run_query(profile, "DELETE FROM meters")


@pytest.mark.parametrize("identifier", ["meters CROSS JOIN other", "meters; DROP TABLE meters", "meters --", "x..y"])
def test_builder_rejects_identifier_fragments(identifier):
    with pytest.raises(sqlsource.SourceError):
        sqlsource.build_query(identifier, ["reading"])


@pytest.mark.parametrize(
    "start,end",
    [("NaT", None), ("tomorrow", None), ("2025-02-01", "2025-01-01"), ("2025-01-01", "2025-02-01T00:00:00Z")],
)
def test_builder_rejects_invalid_or_ambiguous_ranges(start, end):
    with pytest.raises(sqlsource.SourceError):
        sqlsource.build_query("meters", ["reading"], "ts", start, end)


@pytest.mark.parametrize("dialect", ["sqlite", "mysql", "postgres", "tsql"])
def test_binding_survives_dialect_pagination(dialect):
    query = sqlsource.build_query("meters", ["ts"], "ts", "2025-01-01", dialect=dialect)
    assert ":eb_start" in query.sql
    assert ("TOP 100000" in query.sql) if dialect == "tsql" else ("LIMIT 100000" in query.sql)
    assert sqlsource.validate_select(query.sql, dialect=dialect)


@pytest.mark.parametrize(
    "name",
    [
        "attempt_count",
        "template_id",
        "temporary",
        "tempo",
        "boat_count",
        "supply_temperature",
        "return_temp",
        "temp_setpoint",
    ],
)
def test_temperature_name_false_positives(name):
    assert features.find_temperature_col([name]) is None


@pytest.mark.parametrize(
    "name", ["temperature_C", "outdoor_temp_C", "dry_bulb", "oa_temp", "OutdoorTemperature", "OAT", "t_out"]
)
def test_temperature_aliases_and_ambiguity(name):
    assert features.find_temperature_col([name]) == name
    assert features.find_temperature_col([name, "ambient_temperature"]) is None
    assert features.find_temperature_col([name], explicit=name) == name
    with pytest.raises(ValueError):
        features.find_temperature_col([name], explicit="missing")


def test_missing_integrity_sidecar_fails_before_model_load(tmp_path):
    from energybaseline.core.store import _verify_sha256_sidecar_if_present
    from energybaseline.lock.baseline_lock import ArtifactIntegrityError

    path = tmp_path / "unknown.joblib"
    path.write_bytes(b"not deserialized")
    with pytest.raises(ArtifactIntegrityError):
        _verify_sha256_sidecar_if_present(path)


def test_untrusted_plaintext_keyring_is_never_used(no_secure_store, monkeypatch):
    import keyring

    class PlaintextBackend:
        def get_password(self, *args):
            pytest.fail("An untrusted keyring provider must never be read")

        def set_password(self, *args):
            pytest.fail("An untrusted keyring provider must never be written")

    monkeypatch.setattr(keyring, "get_keyring", lambda: PlaintextBackend())
    assert secrets.encrypt_text(CANARY).startswith("session-fernet:")
    assert not secrets.backend_status()["secure_store"]


@pytest.mark.parametrize("content", ["", "nohash", "z" * 64])
def test_malformed_sidecar_fails_before_deserialization(tmp_path, content):
    from energybaseline.core.store import _verify_sha256_sidecar_if_present
    from energybaseline.lock.baseline_lock import ArtifactIntegrityError

    path = tmp_path / "unknown.joblib"
    path.write_bytes(b"not deserialized")
    (tmp_path / "unknown.joblib.sha256").write_text(content)
    with pytest.raises(ArtifactIntegrityError):
        _verify_sha256_sidecar_if_present(path)


def test_purge_recommendation_never_shrinks_to_fit_a_small_dataset():
    from energybaseline.models.base import recommend_autocorr_embargo_gap
    from energybaseline.core.models import make_folds

    assert recommend_autocorr_embargo_gap([1, 2, 3, 4, 5], max_lag=72, steps_per_day=24) >= 72
    assert make_folds(30, 3, "forward_chaining", 72) == []
    for scheme in ("blocked_kfold", "forward_chaining"):
        for train, valid in make_folds(100, 3, scheme, 7):
            assert min(abs(int(a) - int(b)) for a in train for b in valid) > 7


def test_profile_without_remembering_retains_session_credentials_only(no_secure_store):
    db.reset_engine()
    try:
        db.init_database()
        profile = sqlsource.ConnectionProfile("ephemeral", password=CANARY, odbc_string=f"PWD={CANARY}")
        sqlsource.save_profile(profile, remember_credentials=False)
        loaded = next(p for p in sqlsource.load_profiles() if p.name == profile.name)
        assert loaded.password == CANARY
        from energybaseline.db import models

        with db.session_scope() as session:
            row = session.query(models.Connection).filter_by(name=profile.name).one()
            assert CANARY not in row.protected_payload
            assert secrets.decrypt_json(row.protected_payload)["password"] == ""
        sqlsource._session_credentials.pop(profile.name)
        loaded = next(p for p in sqlsource.load_profiles() if p.name == profile.name)
        assert loaded.password == "" and loaded.odbc_string == ""
    finally:
        db.reset_engine()
