"""Phase 2 gate tests: No Target Fabrication (DAT-5), Leak-Free Missing Policy, Outlier Action Policy, NREs, and CLI (DAT-6, E2)."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import numpy as np
import pandas as pd
import pytest

from energybaseline import cli
from energybaseline.core.auto import AutoLog
from energybaseline.core.pipeline import run_analysis
from energybaseline.core.quality import (
    FabricationError,
    apply_leak_free_missing_policy,
    apply_outlier_action_policy,
    detect_outliers,
    preprocess_target,
    verify_no_target_fabrication,
)
from energybaseline.core.schema import (
    ColumnRole,
    ColumnSpec,
    DatasetSchema,
    Inclusion,
    NonRoutineEventSpec,
    OutlierActionPolicy,
    QuantityType,
)
from energybaseline.core.temporal import granularity


def test_target_never_fabricated_and_verifier_catches_fabrication():
    idx = pd.date_range("2024-01-01", periods=48, freq="D")
    rng = np.random.default_rng(42)
    temp = rng.normal(12.0, 5.0, size=48)
    kwh = 200.0 + 8.0 * np.maximum(0.0, 18.0 - temp) + rng.normal(0.0, 3.0, size=48)
    # Inject missing target values and missing predictor values
    kwh[5] = np.nan
    kwh[19] = np.nan
    kwh[33] = np.nan
    temp[6] = np.nan
    temp[20] = np.nan

    df = pd.DataFrame({"ts": idx, "elec_kwh": kwh, "temp_c": temp}).set_index("ts")
    schema = DatasetSchema(
        columns=[
            ColumnSpec(name="ts", role=ColumnRole.TIME),
            ColumnSpec(name="elec_kwh", role=ColumnRole.TARGET, quantity_type=QuantityType.FLOW),
            ColumnSpec(
                name="temp_c",
                role=ColumnRole.PREDICTOR,
                quantity_type=QuantityType.QUALITY,
                inclusion=Inclusion.KNOWN_IN_ADVANCE,
            ),
        ],
        outlier_policy=OutlierActionPolicy.FLAG_ONLY,
    )
    prep = preprocess_target(
        df,
        "elec_kwh",
        ["elec_kwh"],
        granularity("daily"),
        AutoLog({"imputation": "median"}),
        seed=42,
        n_dups=0,
        schema=schema,
    )
    # Missing target rows MUST still be NaN!
    assert prep.df["elec_kwh"].isna().iloc[5]
    assert prep.df["elec_kwh"].isna().iloc[19]
    assert prep.df["elec_kwh"].isna().iloc[33]
    # Predictor was imputed cleanly
    assert int(prep.df["temp_c"].isna().sum()) == 0

    # Verify FabricationError fires if target NaN is overwritten
    fabricated = df["elec_kwh"].copy()
    fabricated.iloc[5] = 250.0
    with pytest.raises(FabricationError, match="DAT-5"):
        verify_no_target_fabrication(df["elec_kwh"], fabricated)


def test_leak_free_predictor_missing_policy_never_peeks_at_holdout():
    # Baseline rows (first 10) have median 10.0; reporting rows (last 10) have extreme values 10000.0 + one NaN
    idx = pd.date_range("2024-01-01", periods=20, freq="D")
    vals = [10.0] * 10 + [10000.0] * 9 + [np.nan]
    df = pd.DataFrame({"y": [100.0] * 20, "pred": vals}, index=idx)
    eval_mask = np.array([False] * 10 + [True] * 10)

    imputed, summary = apply_leak_free_missing_policy(
        df,
        target="y",
        predictors=["pred"],
        eval_mask=eval_mask,
        strategy="median",
        seed=1,
    )
    # If fit strictly on ~eval_mask, imputed value at index 19 MUST be 10.0, not ~5000!
    assert float(imputed["pred"].iloc[19]) == 10.0
    assert summary["strategy"] == "median"


def test_outlier_action_policies_flag_only_ask_user_and_auto_apply():
    idx = pd.date_range("2024-01-01", periods=40, freq="D")
    y = np.full(40, 100.0)
    y[10] = 950.0  # clear extreme spike
    x = np.linspace(10.0, 20.0, 40)
    x[15] = 500.0  # predictor spike
    df = pd.DataFrame({"elec_kwh": y, "temp_c": x}, index=idx)
    flags = detect_outliers("robust_z", df, 3.5, seed=1)

    # 1. flag_only: zero values modified!
    df_flag, rec_flag = apply_outlier_action_policy(
        df,
        target="elec_kwh",
        predictors=["temp_c"],
        adj=df,
        flags=flags,
        method="robust_z",
        threshold=3.5,
        treatment="winsorize",
        policy=OutlierActionPolicy.FLAG_ONLY,
    )
    assert df_flag.equals(df)
    assert len(rec_flag.flagged_target_rows) >= 1
    assert rec_flag.training_set_delta == 0
    assert rec_flag.applied is False

    # 2. ask_user: zero values modified + pending_user_confirmation=True
    df_ask, rec_ask = apply_outlier_action_policy(
        df,
        target="elec_kwh",
        predictors=["temp_c"],
        adj=df,
        flags=flags,
        method="robust_z",
        threshold=3.5,
        treatment="exclude",
        policy=OutlierActionPolicy.ASK_USER,
    )
    assert df_ask.equals(df)
    assert rec_ask.pending_user_confirmation is True
    assert rec_ask.applied is False

    # 3. auto_apply: masks outlier target row (never fabricating synthetic target values!) and winsorizes predictor
    df_apply, rec_apply = apply_outlier_action_policy(
        df,
        target="elec_kwh",
        predictors=["temp_c"],
        adj=df,
        flags=flags,
        method="robust_z",
        threshold=3.5,
        treatment="winsorize",
        policy=OutlierActionPolicy.AUTO_APPLY,
    )
    assert rec_apply.applied is True
    assert rec_apply.training_set_delta == -1
    assert pd.isna(df_apply["elec_kwh"].iloc[10])
    assert float(df_apply["temp_c"].iloc[15]) < 500.0


def test_non_routine_event_exclusion_and_indicator_in_preprocessing():
    idx = pd.date_range("2024-03-01", periods=30, freq="D")
    df = pd.DataFrame(
        {
            "elec_kwh": np.linspace(100.0, 130.0, 30),
            "temp_c": np.linspace(5.0, 15.0, 30),
        },
        index=idx,
    )
    schema = DatasetSchema(
        columns=[
            ColumnSpec(name="ts", role="time"),
            ColumnSpec(name="elec_kwh", role="target", quantity_type="flow"),
            ColumnSpec(name="temp_c", role="predictor", quantity_type="quality"),
        ],
        non_routine_events=[
            NonRoutineEventSpec(
                name="Plant shutdown",
                start="2024-03-05",
                end="2024-03-07",
                action="exclude",
            ),
            NonRoutineEventSpec(
                name="New shift schedule",
                start="2024-03-20",
                end="2024-03-25",
                action="indicator",
            ),
        ],
    )
    prep = preprocess_target(
        df,
        "elec_kwh",
        ["elec_kwh"],
        granularity("daily"),
        AutoLog({}),
        seed=1,
        n_dups=0,
        schema=schema,
    )
    # 3 shutdown days masked to NaN on target (excluded from baseline fit)
    assert int(prep.df.loc["2024-03-05":"2024-03-07", "elec_kwh"].isna().sum()) == 3
    # Indicator predictor created for second NRE
    ind_cols = [c for c in prep.predictors if c.startswith("nre_")]
    assert len(ind_cols) == 1
    assert int(prep.df[ind_cols[0]].sum()) == 6


def test_pipeline_run_analysis_accepts_dataset_schema():
    rng = np.random.default_rng(9)
    n = 36
    temp = rng.uniform(5.0, 25.0, size=n)
    df = pd.DataFrame(
        {
            "Month": pd.date_range("2021-01-01", periods=n, freq="MS"),
            "Electricity_kWh": 1000.0 + 40.0 * temp + rng.normal(0.0, 10.0, size=n),
            "Outdoor_Temp_C": temp,
            "Audit_Ticket_ID": np.arange(1000, 1000 + n),
        }
    )
    schema = DatasetSchema(
        columns=[
            ColumnSpec(name="Month", role="time"),
            ColumnSpec(name="Electricity_kWh", role="target", quantity_type="flow", unit="kWh"),
            ColumnSpec(
                name="Outdoor_Temp_C",
                role="predictor",
                quantity_type="quality",
                inclusion="known_in_advance",
                unit="degC",
            ),
            ColumnSpec(name="Audit_Ticket_ID", role="ignore"),
        ],
        outlier_policy="flag_only",
    )
    run, _ = run_analysis(
        df,
        "Month",
        ["Electricity_kWh"],
        {"enable_nn": False, "enable_mining": False},
        schema=schema,
    )
    tr = run.targets["Electricity_kWh"]
    assert tr.status == "ok"
    # Ignored column must have been excluded with role=ignore reason
    ignored_items = [e["item"] for e in tr.exclusions if "ignore" in e["reason"]]
    assert "Audit_Ticket_ID" in ignored_items


def test_cli_schema_suggest_edit_validate_and_temporal_preview(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("EB_HOME", str(tmp_path / "eb_cli_home"))
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{(tmp_path / 'cli.db').as_posix()}")

    csv_path = Path("samples/monthly_building.csv").resolve()
    schema_path = tmp_path / "suggested_schema.json"
    edited_path = tmp_path / "edited_schema.json"
    preview_path = tmp_path / "temporal_preview.json"

    rc1 = cli.main(["schema-suggest", str(csv_path), "--out", str(schema_path), "--save-db"])
    assert rc1 == 0 and schema_path.exists()

    rc2 = cli.main(
        [
            "schema-edit",
            "--schema-in",
            str(schema_path),
            "--column",
            "Avg_Temp_C:role=predictor,quantity_type=quality,inclusion=known_in_advance",
            "--outlier-policy",
            "flag_only",
            "--out",
            str(edited_path),
            "--save-db",
        ]
    )
    assert rc2 == 0 and edited_path.exists()

    rc3 = cli.main(["schema-validate", str(edited_path), "--dataset", str(csv_path)])
    assert rc3 == 0

    rc4 = cli.main(
        [
            "temporal-preview",
            str(csv_path),
            "--schema",
            str(edited_path),
            "--out",
            str(preview_path),
        ]
    )
    assert rc4 == 0 and preview_path.exists()
    preview = json.loads(preview_path.read_text(encoding="utf-8"))
    assert preview["format"] == "energybaseline.phase2_temporal_preview"
    assert preview["schema"]["schema"]["outlier_policy"] == "flag_only"

    # Tamper with edited_path and verify schema-validate exits with code 2
    tampered = json.loads(edited_path.read_text(encoding="utf-8"))
    tampered["schema"]["outlier_policy"] = "auto_apply"
    edited_path.write_text(json.dumps(tampered), encoding="utf-8")
    assert cli.main(["schema-validate", str(edited_path)]) == 2

    # Also verify launcher `cli` subcommand entry point via subprocess
    proc = subprocess.run(
        [sys.executable, "-m", "energybaseline.launcher", "cli", "schema-validate", str(schema_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0
    assert json.loads(proc.stdout)["valid"] is True
