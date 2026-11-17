"""Real external-engine and bundled pretrained-weight execution, not availability badges."""

from __future__ import annotations

import copy
import hashlib
import json
import re
from pathlib import Path
import subprocess
import sys

import numpy as np
import pandas as pd
from pandas.testing import assert_frame_equal
from PIL import Image
import pytest

from energybaseline.workbench import run_workbench
from energybaseline.workbench.common import digest
from energybaseline.workbench.external import OCR_MODELS, _make_ocr_engine, _ocr_images, _verified_ocr_models

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples/external"


def execute(operation, parameters, frame=None):
    before = copy.deepcopy(parameters)
    original = frame.copy(deep=True) if frame is not None else None
    request = dict(version=1, operation=operation, parameters=parameters)
    result = run_workbench(request, frame)
    assert result["spec_sha256"] == digest(request)
    assert before == parameters
    assert "PRIVATE" not in json.dumps(result, allow_nan=False)
    if frame is not None:
        assert_frame_equal(frame, original)
    return result["result"]


@pytest.fixture
def series():
    x = np.random.default_rng(7).normal(size=120)
    return pd.DataFrame({"time": pd.date_range("2025", periods=120, freq="h"), "x": x, "y": 3 * x + 0.2})


def flaml_spec():
    return dict(time="time", target="y", features=["x"], train_rows=90, validation_rows=20, gap=3, seed=5, iterations=6)


def test_real_flaml_search_refit_holdout_metrics_and_model_budget(series):
    row = execute("flaml-search", flaml_spec(), series)
    assert row["engine"] == "flaml" and row["search_method"] == "CFO"
    assert row["selected_family"] in ("rf", "extra_tree")
    assert row["iterations_executed"] == row["iterations_requested"] == 6
    assert row["search_fit_rows"] == 67 and row["validation_rows"] == 20 and row["purge_gap"] == 3
    assert row["train_rows"] == 90 and row["test_rows"] == 30
    assert 8 <= row["selected_parameters"]["n_estimators"] <= 32
    assert row["test_rmse"] < 1.5 and row["test_mae"] <= row["test_rmse"]
    assert not row["model_persisted"] and not row["baseline_changed"]


def test_real_flaml_outer_data_cannot_select_and_inner_purge_is_respected(series):
    spec = flaml_spec()
    initial = execute("flaml-search", spec, series)
    changed = series.copy()
    changed.loc[90:, ["x", "y"]] += 10000
    other = execute("flaml-search", spec, changed)
    for field in ("selected_family", "selected_parameters", "validation_rmse", "training_sha256"):
        assert initial[field] == other[field]
    assert other["test_rmse"] > 1000
    # Gap observations are excluded from search, but deliberately included in final refit.
    changed = series.copy()
    changed.loc[67:69, "y"] += 10000
    purged = execute("flaml-search", spec, changed)
    for field in ("selected_family", "selected_parameters", "validation_rmse"):
        assert initial[field] == purged[field]
    assert initial["training_sha256"] != purged["training_sha256"]


@pytest.mark.parametrize(
    "change",
    [
        {"features": ["y"]},
        {"features": ["time"]},
        {"features": []},
        {"target": "time"},
        {"iterations": True},
        {"iterations": 17},
        {"gap": 100},
        {"validation_rows": 80},
        {"seed": -1},
        {"engine": "PRIVATE"},
        {"train_rows": 120},
        {"validation_rows": 2},
    ],
)
def test_flaml_rejects_unsafe_or_unsupported_contracts(series, change):
    spec = flaml_spec() | change
    with pytest.raises(ValueError) as caught:
        execute("flaml-search", spec, series)
    assert "PRIVATE" not in str(caught.value)


def test_flaml_missing_predictor_is_not_silently_imputed(series):
    series.loc[0, "x"] = np.nan
    with pytest.raises(ValueError):
        execute("flaml-search", flaml_spec(), series)


def test_actual_pandera_rejects_cell_failures_without_exporting_values_or_private_index():
    frame = pd.DataFrame(
        {"PRIVATE_name": ["PRIVATE", "PRIVATE", None], "value": [1.0, 5.0, -2.0]},
        index=["PRIVATE_1", "PRIVATE_2", "PRIVATE_3"],
    )
    spec = {
        "columns": [
            {"column": "value", "dtype": "float64", "minimum": 0, "maximum": 3},
            {"column": "PRIVATE_name", "dtype": "str", "nullable": False, "unique": True},
        ]
    }
    row = execute("pandera-validation", spec, frame)
    assert row["engine"] == "pandera" and not row["passed"]
    assert row["failed_row_positions"] == [0, 1, 2] and row["failure_case_count"] >= 5
    assert not row["coercion_performed"] and not row["raw_values_exported"]


def test_actual_pandera_passes_valid_schema_and_reports_strict_and_dtype_failures():
    frame = pd.DataFrame({"x": [1.0, 2.0, 3.0], "flag": [True, False, True]})
    spec = {
        "columns": [
            {"column": "x", "dtype": "float64", "minimum": 0, "maximum": 3},
            {"column": "flag", "dtype": "bool"},
        ],
        "strict": True,
    }
    assert execute("pandera-validation", spec, frame)["passed"]
    subset = {"columns": [spec["columns"][0]], "strict": True}
    row = execute("pandera-validation", subset, frame)
    assert not row["passed"] and row["schema_level_failure_cases"] > 0
    spec["columns"][0]["dtype"] = "int64"
    assert not execute("pandera-validation", spec, frame)["passed"]


def test_pandera_truncates_failure_positions_without_fabricating_counts():
    frame = pd.DataFrame({"x": [-1.0] * 125})
    row = execute("pandera-validation", {"columns": [{"column": "x", "dtype": "float64", "minimum": 0}]}, frame)
    assert row["failure_case_count"] == 125
    assert row["failed_row_positions"] == list(range(100)) and row["row_positions_truncated"]


@pytest.mark.parametrize(
    "spec",
    [
        {"columns": []},
        {"columns": [{"column": "x", "dtype": "PRIVATE"}]},
        {"columns": [{"column": "x", "dtype": "str", "minimum": 1}]},
        {"columns": [{"column": "x", "dtype": "float64", "nullable": 1}]},
        {"columns": [{"column": "x", "dtype": "float64", "minimum": 5, "maximum": 1}]},
        {"columns": [{"column": "x", "dtype": "float64"}], "strict": 1},
        {"columns": [{"column": "x", "dtype": "float64", "callback": "PRIVATE"}]},
        {"columns": [{"column": "x", "dtype": "float64"}] * 2},
    ],
)
def test_pandera_rejects_unsupported_declarations_without_loading_code(spec):
    with pytest.raises(ValueError) as caught:
        execute("pandera-validation", spec, pd.DataFrame({"x": [1.0]}))
    assert "PRIVATE" not in str(caught.value)


def ocr_spec():
    return dict(image_root=str(EXAMPLES), image_files=["printed.png"])


def test_actual_pretrained_ocr_recognizes_known_print_and_public_result_suppresses_plaintext():
    spec = ocr_spec()
    hashes = _verified_ocr_models()
    assert hashes == {name: value[1] for name, value in OCR_MODELS.items()}
    images, sources = _ocr_images(spec)
    # This is actual pretrained semantic recognition, not a template lookup or mocked output.
    engine = _make_ocr_engine(0.5)
    recognized, _ = engine(images[0])
    assert any(text == "HELLO WORLD 123" and confidence > 0.85 for _, text, confidence in recognized)
    row = execute("pretrained-ocr", spec)
    assert row["engine"] == "rapidocr-onnxruntime" and row["pretrained_weights_used"]
    assert row["images"][0]["source_sha256"] == hashlib.sha256((EXAMPLES / "printed.png").read_bytes()).hexdigest()
    assert row["images"][0]["line_count"] == 1
    line = row["images"][0]["lines"][0]
    assert line["character_count"] == len("HELLO WORLD 123") and line["confidence"] > 0.85
    assert sources[0]["decoded_bgr_sha256"] == row["images"][0]["decoded_bgr_sha256"]
    assert "HELLO" not in json.dumps(row) and not row["recognized_plaintext_exported"]
    assert not row["training_performed"] and not row["runtime_downloads"]


def test_actual_ocr_blank_image_has_no_hallucinated_template_text(tmp_path):
    Image.new("RGB", (400, 120), "white").save(tmp_path / "blank.png")
    row = execute("pretrained-ocr", dict(image_root=str(tmp_path), image_files=["blank.png"]))
    assert row["images"][0]["line_count"] == 0 and row["images"][0]["lines"] == []


@pytest.mark.parametrize(
    "change",
    [
        {"image_files": []},
        {"image_files": ["printed.png"] * 5},
        {"image_files": ["../PRIVATE.png"]},
        {"image_files": ["https://PRIVATE/image.png"]},
        {"image_files": [7]},
        {"confidence": True},
        {"confidence": 0},
        {"model_path": "PRIVATE"},
        {"include_text": True},
    ],
)
def test_ocr_path_resource_and_model_overrides_fail_closed(change):
    with pytest.raises(ValueError) as caught:
        execute("pretrained-ocr", ocr_spec() | change)
    assert "PRIVATE" not in str(caught.value)


def test_actual_ocr_corrupt_decoding_and_excessive_pixels_are_private(tmp_path):
    (tmp_path / "PRIVATE.png").write_bytes(b"PRIVATE corrupt image")
    spec = dict(image_root=str(tmp_path), image_files=["PRIVATE.png"])
    with pytest.raises(ValueError) as caught:
        execute("pretrained-ocr", spec)
    assert "PRIVATE" not in str(caught.value)
    Image.new("L", (1500, 1500)).save(tmp_path / "large.png")
    with pytest.raises(ValueError):
        execute("pretrained-ocr", spec | {"image_files": ["large.png"]})


def test_pretrained_ocr_executes_in_fresh_process_with_all_network_connects_denied():
    code = """
import json,sys
from pathlib import Path
from energybaseline.workbench import run_workbench

def forbid(event, args):
    if event in ("socket.connect", "socket.getaddrinfo"):
        raise RuntimeError("Network is forbidden in this execution")
sys.addaudithook(forbid)
r=run_workbench(json.loads(Path("examples/external/pretrained-ocr.json").read_text()))
assert r["result"]["pretrained_weights_used"]
assert r["result"]["images"][0]["lines"][0]["character_count"]==15
print("actual offline pretrained inference passed")
"""
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "actual offline pretrained inference passed"


@pytest.mark.parametrize("name", ["flaml-search", "pandera-validation", "pretrained-ocr"])
def test_external_examples_execute_actual_cli_and_outputfile(name, tmp_path, capsys):
    from energybaseline.cli import main

    out = tmp_path / "result.json"
    args = ["workbench", str(EXAMPLES / (name + ".json")), "--out", str(out)]
    if name != "pretrained-ocr":
        args += ["--dataset", str(ROOT / "examples/advanced/series.csv")]
    assert main(args) == 0
    result = json.loads(capsys.readouterr().out)
    assert result == json.loads(out.read_text()) and result["operation"] == name
    assert "HELLO WORLD" not in out.read_text()


def test_real_pretrained_weight_copy_is_rejected_after_a_single_byte_change(tmp_path):
    import shutil
    from energybaseline.workbench.external import _rapidocr_module, _verify_model_directory

    installed = Path(_rapidocr_module().__file__).parent / "models"
    for name in OCR_MODELS:
        shutil.copyfile(installed / name, tmp_path / name)
    assert _verify_model_directory(tmp_path) == _verified_ocr_models()
    path = tmp_path / next(iter(OCR_MODELS))
    with path.open("r+b") as stream:
        value = stream.read(1)
        stream.seek(0)
        stream.write(bytes([value[0] ^ 1]))
    with pytest.raises(ValueError, match="hash binding"):
        _verify_model_directory(tmp_path)


def test_native_image_import_restores_qt_and_loader_environment_in_fresh_process():
    code = """
from energybaseline.config.runtime_env import get_process_environ
from energybaseline.workbench.external import _verified_ocr_models
keys=("PATH","LD_LIBRARY_PATH","QT_QPA_PLATFORM_PLUGIN_PATH","QT_QPA_FONTDIR")
before={key:get_process_environ().get(key) for key in keys}
_verified_ocr_models()
after={key:get_process_environ().get(key) for key in keys}
assert before==after
print("native Qt6 and loader environment preserved")
"""
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "native Qt6 and loader environment preserved"


@pytest.mark.parametrize("name", ["flaml-search", "pandera-validation", "pretrained-ocr"])
def test_real_external_cli_without_standard_streams_keeps_explicit_output(name, tmp_path):
    # Actual Windows windowed PyInstaller processes set both streams to None.
    # Execute the real engines in a fresh interpreter with that runtime contract,
    # not a mocked model or a replacement success response.
    out = tmp_path / "result.json"
    args = ["workbench", str(EXAMPLES / (name + ".json")), "--out", str(out)]
    if name != "pretrained-ocr":
        args += ["--dataset", str(ROOT / "examples/advanced/series.csv")]
    code = """
import json,sys
from pathlib import Path
from energybaseline.cli import main
args=json.loads(sys.argv[1])
sys.stdout=None
sys.stderr=None
result=main(args)
assert sys.stdout is None and sys.stderr is None
raise SystemExit(result)
"""
    child = subprocess.run(
        [sys.executable, "-c", code, json.dumps(args)], cwd=ROOT, capture_output=True, text=True, timeout=60
    )
    assert child.returncode == 0, child.stderr
    assert child.stdout == ""
    _assert_only_known_native_discovery_warning(child.stderr)
    output = json.loads(out.read_text())
    assert output["operation"] == name
    assert output["spec_sha256"] == digest(json.loads((EXAMPLES / (name + ".json")).read_text()))
    row = output["result"]
    assert row["baseline_changed"] is False
    if name == "flaml-search":
        assert row["engine"] == "flaml" and row["iterations_executed"] == 4 and row["test_rmse"] < 2
    elif name == "pandera-validation":
        assert row["engine"] == "pandera" and row["passed"]
    else:
        assert row["pretrained_weights_used"] and row["images"][0]["lines"][0]["character_count"] == 15
        assert row["recognized_plaintext_exported"] is False


def test_windowless_cli_without_destination_fails_closed_without_secondary_stream_error():
    code = """
import sys
from energybaseline.cli import main
sys.stdout=None
sys.stderr=None
result=main(['workbench','examples/external/pandera-validation.json',
             '--dataset','examples/advanced/series.csv'])
assert result==2
"""
    child = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=60)
    assert child.returncode == 0, child.stderr
    assert child.stdout == child.stderr == ""


def _assert_only_known_native_discovery_warning(stderr):
    # Python sys.stderr=None does not close OS descriptor 2. ORT's C++ logger can
    # independently write this hardware-enumeration warning on Azure Linux VMs.
    # Accept only the exact bounded diagnostic, never arbitrary stderr/tracebacks.
    assert len(stderr) <= 32768
    lines = re.sub(r"\x1b\[[0-9;]*m", "", stderr).splitlines()
    assert len(lines) <= 32
    expected = (
        r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d+ "
        r"\[W:onnxruntime:Default, device_discovery\.cc:\d+ GetPciBusId\] "
        r'Skipping pci_bus_id for PCI path at "/sys/devices/[^"\r\n]+" '
        r'because filename "[0-9a-f-]+" did not match expected pattern of '
        + re.escape("[0-9a-f]+:[0-9a-f]+:[0-9a-f]+[.][0-9a-f]+")
    )
    assert all(re.fullmatch(expected, line) for line in lines), stderr


def test_native_warning_allowlist_rejects_python_errors_unknown_logs_and_excessive_output():
    known = (
        "\x1b[0;93m2026-11-15 23:31:25.824591402 "
        "[W:onnxruntime:Default, device_discovery.cc:95 GetPciBusId] "
        'Skipping pci_bus_id for PCI path at "/sys/devices/test/5620e0c7-8062-4dce-aeb7-520c7ef76171" '
        'because filename "5620e0c7-8062-4dce-aeb7-520c7ef76171" did not match expected pattern of '
        "[0-9a-f]+:[0-9a-f]+:[0-9a-f]+[.][0-9a-f]+\x1b[m\n"
    )
    _assert_only_known_native_discovery_warning("")
    _assert_only_known_native_discovery_warning(known)
    for invalid in ("Traceback: PRIVATE", "Unexpected native error", known + "PRIVATE", known * 33, "x" * 32769):
        with pytest.raises(AssertionError):
            _assert_only_known_native_discovery_warning(invalid)
