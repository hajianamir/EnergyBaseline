"""Catalog authorization and opt-out persistence boundary regressions."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from energybaseline.core import secrets, sqlsource
from energybaseline.core.tcp import TestConfigPanel as Panel
from energybaseline.core.vsg import VariableSelectionGate, ComplianceGateError
from energybaseline.db import models, session as db
from energybaseline.tests_catalog import run_full_test_catalog


def test_opt_out_does_not_persist_sensitive_sql_literals(tmp_path, monkeypatch):
    import keyring

    monkeypatch.setenv("EB_HOME", str(tmp_path))
    monkeypatch.delenv("EB_ALLOW_INSECURE_KEYFILE", raising=False)
    monkeypatch.setattr(keyring, "get_password", lambda *args: None)
    monkeypatch.setattr(keyring, "set_password", lambda *args: None)
    monkeypatch.setattr(secrets.sys, "platform", "linux")
    monkeypatch.setattr(secrets, "_session_key", None)
    db.reset_engine()
    name = "ephemeral-query"
    try:
        db.init_database()
        query = {"saved": "SELECT 'SENSITIVE_LITERAL_TEST_ONLY'"}
        sqlsource.save_profile(sqlsource.ConnectionProfile(name, queries=query), remember_credentials=False)
        assert next(p for p in sqlsource.load_profiles() if p.name == name).queries == query
        with db.session_scope() as session:
            row = session.query(models.Connection).filter_by(name=name).one()
            assert secrets.decrypt_json(row.protected_payload)["queries"] == {}
        sqlsource._session_credentials.pop(name)
        assert next(p for p in sqlsource.load_profiles() if p.name == name).queries == {}
    finally:
        sqlsource._session_credentials.pop(name, None)
        db.reset_engine()


def test_catalog_cannot_execute_with_accepted_but_unlocked_or_mutated_inputs():
    rng = np.random.default_rng(93)
    frame = pd.DataFrame(
        {
            "time": pd.date_range("2020-01-01", periods=48, freq="MS"),
            "target": rng.normal(size=48),
            "setting": rng.normal(size=48),
        }
    )
    gate = VariableSelectionGate(frame, targets=["target"], time_col="time")
    gate.accept()
    panel = Panel(gate)
    panel.accept()
    with pytest.raises(ComplianceGateError, match="locked"):
        run_full_test_catalog(frame, target_col="target", panel=panel)
    panel.lock_preregistration()
    changed = frame.copy()
    changed.loc[20, "setting"] += 77
    with pytest.raises(ComplianceGateError, match="input"):
        run_full_test_catalog(changed, target_col="target", panel=panel)
    with pytest.raises(ComplianceGateError, match="requires"):
        run_full_test_catalog(frame, target_col="target")
