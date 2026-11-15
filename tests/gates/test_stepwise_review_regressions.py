"""Fresh stepwise review: reproduce input, transport, gate and renderer defects."""

from __future__ import annotations

import ssl
from urllib.parse import parse_qs, urlsplit

import numpy as np
import pandas as pd
import pytest
from openpyxl import load_workbook
from sqlalchemy import create_engine

from energybaseline import sdk
from energybaseline.contracts import AnalysisRequest
from energybaseline.core import sqlsource
from energybaseline.core.ingest import detect_csv_dialect
from energybaseline.core.tcp import TestConfigPanel as TCP
from energybaseline.core.vsg import ComplianceGateError, VariableSelectionGate
from energybaseline.report import ReportModel, render_excel
from energybaseline.report.charts import figure_for
from energybaseline.uq.savings_uncertainty import savings_uncertainty
from tests.fixtures.phase8 import report_fixture


@pytest.mark.parametrize("driver", ["MySQL", "PostgreSQL", "SQL Server"])
def test_tls_requires_certificate_and_hostname_verification(monkeypatch, driver):
    captured = {}

    def capture(url, **kwargs):
        captured.update(url=url, **kwargs)
        return create_engine("sqlite://")

    monkeypatch.setattr(sqlsource, "create_engine", capture)
    engine = sqlsource._engine(sqlsource.ConnectionProfile("test", driver=driver, host="db.example", ssl=True))
    try:
        args = captured["connect_args"]
        if driver == "MySQL":
            context = args["ssl"]
            assert isinstance(context, ssl.SSLContext)
            assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname
        elif driver == "PostgreSQL":
            assert args["sslmode"] == "verify-full"
        else:
            cs = parse_qs(urlsplit(captured["url"]).query)["odbc_connect"][0]
            assert "Encrypt=yes;TrustServerCertificate=no;" in cs
    finally:
        engine.dispose()


def test_generated_odbc_credentials_cannot_add_connection_attributes(monkeypatch):
    captured = {}

    def capture(url, **kwargs):
        captured["url"] = url
        return create_engine("sqlite://")

    monkeypatch.setattr(sqlsource, "create_engine", capture)
    p = sqlsource.ConnectionProfile(
        "test", driver="SQL Server", host="db;Encrypt=no", database="d}b", username="u;id", password="p};UID=attacker"
    )
    engine = sqlsource._engine(p)
    try:
        cs = parse_qs(urlsplit(captured["url"]).query)["odbc_connect"][0]
        assert "SERVER={db;Encrypt=no,1433};" in cs
        assert "DATABASE={d}}b};UID={u;id};PWD={p}};UID=attacker};" in cs
    finally:
        engine.dispose()


@pytest.mark.parametrize("field", ["max_rows", "timeout_seconds"])
@pytest.mark.parametrize("value", [True, 1.5, float("nan"), float("inf"), "2", None])
def test_query_bounds_reject_noninteger_values_before_connect(monkeypatch, field, value):
    def forbidden(*args, **kwargs):
        pytest.fail("Malformed bounds reached the connection boundary")

    monkeypatch.setattr(sqlsource, "_engine", forbidden)
    with pytest.raises(sqlsource.SourceError, match="integer"):
        sqlsource.run_query(sqlsource.ConnectionProfile("test"), "SELECT 1", **{field: value})


@pytest.mark.parametrize("config", [False, 0, "", []])
def test_sdk_does_not_replace_falsey_malformed_config(monkeypatch, config):
    monkeypatch.setattr(sdk, "run_analysis", lambda *a, **k: pytest.fail("Malformed request executed"))
    with pytest.raises(ValueError, match="mapping"):
        sdk.analyze(pd.DataFrame({"t": [1], "y": [2]}), "t", ["y"], config)


@pytest.mark.parametrize("payload", [{}, {"time_col": "t"}, {"targets": ["y"]}])
def test_missing_request_fields_raise_contract_error(payload):
    with pytest.raises(ValueError, match="required"):
        AnalysisRequest.from_dict(payload)


def test_long_single_line_csv_content_is_not_a_filesystem_path():
    assert detect_csv_dialect(",".join("column" + str(i) for i in range(2000))) == (",", ".")


@pytest.mark.parametrize("field", ["measurement_sd", "static_factor_sd"])
@pytest.mark.parametrize("value", [float("nan"), float("inf"), -1.0])
def test_uncertainty_rejects_nonfinite_or_negative_standard_uncertainties(field, value):
    with pytest.raises(ValueError):
        savings_uncertainty([2, 3], np.arange(10.0), **{field: value})


def locked_panel():
    frame = pd.DataFrame({"x": np.arange(40.0), "y": np.arange(40.0) + 10})
    gate = VariableSelectionGate(frame, targets=["y"])
    gate.accept()
    panel = TCP(gate)
    panel.accept()
    return frame, gate, panel


@pytest.mark.parametrize("state", ["unlocked", "mutated"])
@pytest.mark.parametrize("gateway", ["uq", "drift", "causal", "optional", "models"])
def test_all_inference_gateways_require_unchanged_locked_preregistration(gateway, state):
    from energybaseline.causal import optional_backend_effect, run_causal_mv
    from energybaseline.models import evaluate_f1_f10_catalog
    from energybaseline.monitoring import run_drift_monitor
    from energybaseline.uq import run_uq_catalog

    frame, gate, panel = locked_panel()
    if state == "mutated":
        panel.lock_preregistration()
        panel.alpha = 0.001
    # Invalid data are deliberate: authorization must fail before computation.
    with pytest.raises(ComplianceGateError):
        if gateway == "uq":
            run_uq_catalog(None, None, None, tcp=panel)
        elif gateway == "drift":
            run_drift_monitor({}, {}, [], [], tcp=panel)
        elif gateway == "causal":
            run_causal_mv([], [], [], [], [], tcp=panel)
        elif gateway == "optional":
            optional_backend_effect(None, treatment="g", outcome="y", common_causes=[], tcp=panel)
        else:
            evaluate_f1_f10_catalog(frame, target_col="y", vsg=gate, tcp=panel)


def altered_report(**changes):
    payload = report_fixture().data
    payload.pop("report_id")
    payload.update(changes)
    return payload


def test_approved_single_color_palette_renders_without_index_error():
    payload = altered_report()
    for chart in payload["charts"].values():
        chart["palette"] = ["#000000"]
    model = ReportModel(payload)
    for chart_id in model.data["charts"]:
        figure = figure_for(model, chart_id)
        figure.clear()


def test_excel_contents_titles_are_literals_and_links_escape_apostrophes(tmp_path):
    payload = altered_report()
    payload["sections"][0]["title"] = "=1+1"
    payload["sections"][1]["title"] = "Engineer's findings"
    model = ReportModel(payload)
    path = tmp_path / "literal.xlsx"
    render_excel(model, path, depth="full")
    wb = load_workbook(path)
    try:
        cell = wb["Contents"]["A2"]
        assert cell.value == "=1+1" and cell.data_type == "s"
        links = [row[1].hyperlink.target for row in list(wb["Contents"])[1:]]
        assert "#'Engineer''s findings'!A1" in links
    finally:
        wb.close()


def test_mysql_rejects_plaintext_downgrade_before_readonly_setup(monkeypatch):
    from types import SimpleNamespace

    listeners = []
    monkeypatch.setattr(sqlsource.event, "listens_for", lambda *a: lambda fn: listeners.append(fn) or fn)
    engine = sqlsource._engine(sqlsource.ConnectionProfile("test", driver="MySQL", ssl=True))
    try:
        with pytest.raises(sqlsource.SourceError, match="TLS"):
            listeners[0](SimpleNamespace(_sock=object()), None)
    finally:
        engine.dispose()


def test_odbc_fallback_braces_sensitive_attributes(monkeypatch):
    import pyodbc

    captured = []

    def connect(cs, **kwargs):
        captured.append(cs)
        raise sqlsource.SourceError("Stop before network I/O")

    monkeypatch.setattr(pyodbc, "connect", connect)
    profile = sqlsource.ConnectionProfile("test", driver="ODBC", database="a;b", password="p};UID=bad")
    with pytest.raises(sqlsource.SourceError, match="Stop"):
        sqlsource._odbc_df(profile, "SELECT 1", max_rows=1, timeout_seconds=1)
    assert "DATABASE={a;b};UID={};PWD={p}};UID=bad};" in captured[0]


def test_sqlserver_custom_ca_is_not_silently_ignored():
    with pytest.raises(sqlsource.SourceError, match="trust store"):
        sqlsource._engine(sqlsource.ConnectionProfile("test", driver="SQL Server", ssl=True, ssl_ca="custom.pem"))


@pytest.mark.parametrize("value", ["a\x00b", "a\nb", "a\rb"])
def test_generated_odbc_attributes_reject_control_characters(value):
    with pytest.raises(sqlsource.SourceError, match="control"):
        sqlsource._odbc_value(value)


def test_excel_rejects_oversized_text_before_openpyxl_truncation(tmp_path):
    payload = altered_report()
    # Explanatory template text has no numeric facts and is otherwise valid.
    payload["sections"][0]["template"]["Purpose"] = "Long explanation " * 3000
    model = ReportModel(payload)
    with pytest.raises(ValueError, match="storage limit"):
        render_excel(model, tmp_path / "oversized.xlsx", depth="full")


def test_html_agreement_supports_all_valid_metric_artifact_ids():
    from energybaseline.report import render_html
    from energybaseline.report.agreement import html_metrics, verify_metric_agreement

    payload = altered_report()
    original = payload["metrics"][0]["artifact_id"]
    payload["artifacts"]["summary"] = payload["artifacts"][original].copy()
    payload["metrics"][0]["artifact_id"] = "summary"
    model = ReportModel(payload)
    assert verify_metric_agreement(model, html_metrics(render_html(model, depth="executive")))


def test_pinned_mysql_driver_rejects_no_tls_before_authentication(monkeypatch):
    import pymysql
    from pymysql.constants import CLIENT

    captured = {}

    def capture(url, **kwargs):
        captured.update(kwargs)
        return create_engine("sqlite://")

    monkeypatch.setattr(sqlsource, "create_engine", capture)
    engine = sqlsource._engine(sqlsource.ConnectionProfile("test", driver="MySQL", ssl=True))
    try:
        connection = pymysql.Connection(user="test", defer_connect=True, **captured["connect_args"])
        connection.server_version = "8.0.0"
        connection.server_capabilities = 0
        assert connection.client_flag & CLIENT.SSL
        monkeypatch.setattr(connection, "write_packet", lambda *a: pytest.fail("Credentials sent without TLS"))
        with pytest.raises(pymysql.OperationalError, match="SSL is required"):
            connection._request_authentication()
    finally:
        engine.dispose()


@pytest.mark.parametrize("kwargs", [{"ssl": True}, {"ssl_ca": "custom.pem"}])
def test_generic_odbc_never_silently_ignores_profile_tls_policy(monkeypatch, kwargs):
    import pyodbc

    monkeypatch.setattr(pyodbc, "connect", lambda *a, **k: pytest.fail("Unverified TLS profile connected"))
    with pytest.raises(sqlsource.SourceError, match="TLS policy"):
        sqlsource._odbc_df(
            sqlsource.ConnectionProfile("test", driver="ODBC", **kwargs), "SELECT 1", max_rows=1, timeout_seconds=1
        )


def test_odbc_plain_database_compatibility_never_leaves_delimiters_unescaped():
    assert sqlsource._odbc_value("/safe/database.sqlite", brace=False) == "/safe/database.sqlite"
    for value in ("x;PWD=bad", "x}y", "{x", " padded ", ""):
        assert sqlsource._odbc_value(value, brace=False).startswith("{")
