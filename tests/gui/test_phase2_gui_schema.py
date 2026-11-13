"""Phase 2 headless GUI tests: SetupPage Column Metadata editor, Parquet open, schema JSON/DB roundtrip (DAT-6..DAT-8)."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from PySide6.QtWidgets import QApplication

from energybaseline.core import store
from energybaseline.core.schema import (
    ColumnRole,
    Inclusion,
    OutlierActionPolicy,
    QuantityType,
    SchemaValidationError,
)
from energybaseline.db.session import init_database, reset_engine
from energybaseline.gui.main_window import MainWindow
from energybaseline.gui.state import AppState


def test_setup_page_schema_editor_json_db_roundtrip_and_parquet_import(
    qapp: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("EB_NONINTERACTIVE", "1")
    monkeypatch.setenv("EB_HOME", str(tmp_path / "eb_gui_home"))
    db_url = f"sqlite:///{(tmp_path / 'gui_phase2.sqlite3').as_posix()}"
    monkeypatch.setenv("DATABASE_URL", db_url)
    reset_engine()
    boot = init_database()

    win = MainWindow(AppState(), boot)
    try:
        setup = win.setup_page
        sample_csv = Path("samples/monthly_building.csv").resolve()
        assert setup.open_file(sample_csv, allowed_roots=[sample_csv.parent, tmp_path]) is True
        qapp.processEvents()

        # Heuristic schema populated and persisted automatically
        assert setup.schema_tbl.rowCount() == len(win.state.df.columns)
        assert win.state.dataset_schema is not None
        assert win.state.schema_id is not None

        # Edit column metadata via SetupPage API and verify source flips to 'explicit'
        col_names = list(win.state.df.columns)
        pred_col = [c for c in col_names if c != win.state.time_col and c not in win.state.targets][0]
        setup.set_column_metadata(
            pred_col,
            role=ColumnRole.PREDICTOR,
            quantity_type=QuantityType.FLOW,
            inclusion=Inclusion.KNOWN_IN_ADVANCE,
            unit="kWh",
            availability="scheduled",
        )
        setup.outlier_policy.setCurrentText(OutlierActionPolicy.ASK_USER.value)
        setup.tz.setText("Europe/Berlin")

        sid2 = setup.persist_schema_to_db()
        loaded_db = store.load_dataset_schema(schema_id=sid2)
        assert loaded_db is not None
        assert loaded_db.timezone == "Europe/Berlin"
        assert loaded_db.outlier_policy == OutlierActionPolicy.ASK_USER
        spec = loaded_db.get_column(pred_col)
        assert spec.quantity_type == QuantityType.FLOW
        assert spec.inclusion == Inclusion.KNOWN_IN_ADVANCE
        assert spec.suggested_by_heuristic is False

        # Save Schema JSON and Load Schema JSON roundtrip
        schema_json = tmp_path / "exported_schema.json"
        saved_path = setup.save_schema_file(schema_json, allowed_roots=[tmp_path])
        assert saved_path == schema_json and schema_json.exists()

        # Reject path traversal on save/load
        assert setup.save_schema_file(str(tmp_path / ".." / "escape.json"), allowed_roots=[tmp_path]) is None

        # Mutate UI then reload from JSON and verify restoration
        setup.outlier_policy.setCurrentText(OutlierActionPolicy.FLAG_ONLY.value)
        assert setup.load_schema_file(schema_json, allowed_roots=[tmp_path]) is True
        assert setup.outlier_policy.currentText() == OutlierActionPolicy.ASK_USER.value

        # Corrupt JSON SHA-256 and verify load_schema_file rejects it cleanly
        bad_json = tmp_path / "tampered_schema.json"
        obj = json.loads(schema_json.read_text(encoding="utf-8"))
        obj["schema"]["timezone"] = "UTC"
        bad_json.write_text(json.dumps(obj), encoding="utf-8")
        assert setup.load_schema_file(bad_json, allowed_roots=[tmp_path]) is False

        # Verify invalid UI configuration (0 targets) raises SchemaValidationError
        for t in list(win.state.targets):
            setup.set_column_metadata(t, role=ColumnRole.PREDICTOR, quantity_type=QuantityType.QUALITY)
        with pytest.raises(SchemaValidationError, match="target"):
            setup.build_schema_from_ui()

        # Verify direct Parquet file import through SetupPage.open_file
        pq_path = tmp_path / "building_meter.parquet"
        pd.DataFrame(
            {
                "Timestamp": pd.date_range("2024-01-01", periods=24, freq="h"),
                "Electricity_kWh": [15.0 + (i % 5) for i in range(24)],
                "Temp_C": [10.0 + 0.5 * i for i in range(24)],
            }
        ).to_parquet(pq_path, engine="pyarrow", index=False)
        assert setup.open_file(pq_path, allowed_roots=[tmp_path]) is True
        assert len(win.state.df) == 24
        assert win.state.dataset_schema is not None
        assert win.state.dataset_schema.time_column == "Timestamp"
        assert "Electricity_kWh" in win.state.dataset_schema.target_columns
    finally:
        win.close()
        reset_engine()
