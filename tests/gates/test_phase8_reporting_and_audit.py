"""Gate G8 contracts: immutable report facts, exported values, metadata, links and hostile archives."""

from __future__ import annotations
import hashlib
import json
import re
import zipfile
from html.parser import HTMLParser

import numpy as np
from openpyxl import load_workbook
from pypdf import PdfReader
import pytest

from energybaseline.core.report_adapter import assemble_report
from energybaseline.report import (
    ReportModel,
    render_pdf,
    render_excel,
    render_html,
    export_archive,
    verify_archive,
    export_companions,
)
from energybaseline.report.agreement import pdf_metrics, excel_metrics, verify_metric_agreement
from energybaseline.report.provenance import provenance_manifest, derive_seed, seed_runtime, canonical
from energybaseline.report.depth import selected_sections
from energybaseline.report.template import FIELDS
from energybaseline.report.sections import SECTIONS
from energybaseline.report.charts import OKABE_ITO, figure_for, render_chart
from energybaseline.report.banner import disclosure_banner
from energybaseline.report.diff import report_diff, export_formula_workbook
from tests.fixtures.phase8 import report_fixture


@pytest.fixture(scope="module")
def model():
    return report_fixture()


class HTMLFacts(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.ids = set()
        self.hrefs = []
        self.images = []
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if "id" in attrs:
            self.ids.add(attrs["id"])
        if tag == "a":
            self.hrefs.append(attrs.get("href", ""))
        if tag == "img":
            self.images.append(attrs)


def test_report_model_roundtrip_and_immutable(model, tmp_path):
    path = tmp_path / "model.json"
    model.save(path)
    assert ReportModel.load(path).to_json() == model.to_json()
    copy = model.data
    copy["title"] = "changed"
    assert model.data["title"] != "changed"
    with pytest.raises(TypeError):
        model._json = "changed"
    with pytest.raises(TypeError):
        del model._json
    copy = model.data
    copy["artifacts"]["metric.t1.cvrmse"]["value"] = 999
    with pytest.raises(ValueError):
        ReportModel(copy)
    copy = model.data
    copy["schema_version"] = 100
    with pytest.raises(ValueError):
        ReportModel(copy)


def test_seed_determinism_target_user_dataset_and_runtime():
    seed = derive_seed("a" * 64, "Electricity", 42)
    assert seed == derive_seed("a" * 64, "Electricity", 42)
    assert (
        len(
            {
                seed,
                derive_seed("b" * 64, "Electricity", 42),
                derive_seed("a" * 64, "Gas", 42),
                derive_seed("a" * 64, "Electricity", 43),
            }
        )
        == 4
    )
    import random
    import torch

    seed_runtime(seed, torch_backend=True)
    first = (np.random.normal(size=10), torch.rand(10), random.random())
    seed_runtime(seed, torch_backend=True)
    second = (np.random.normal(size=10), torch.rand(10), random.random())
    np.testing.assert_array_equal(first[0], second[0])
    assert torch.equal(first[1], second[1]) and first[2] == second[2]
    from sklearn.ensemble import RandomForestRegressor
    from optuna.samplers import TPESampler
    import optuna

    X = np.arange(40).reshape(-1, 1)
    y = np.sin(X[:, 0])
    a = RandomForestRegressor(n_estimators=5, random_state=seed).fit(X, y).predict(X)
    b = RandomForestRegressor(n_estimators=5, random_state=seed).fit(X, y).predict(X)
    np.testing.assert_array_equal(a, b)
    studies = [optuna.create_study(sampler=TPESampler(seed=seed)) for _ in range(2)]
    assert studies[0].ask().suggest_float("x", 0, 1) == studies[1].ask().suggest_float("x", 0, 1)
    for bad in ["", "z" * 64]:
        with pytest.raises(ValueError):
            derive_seed(bad, "Energy", 1)


def test_provenance_manifest_complete_and_no_connection_material(model):
    p = model.data["provenance"]
    assert p["dataset_sha256"] == "a" * 64 and p["commit_sha"] == "b" * 40
    assert p["packages"] and p["cpu_count"] > 0 and p["os"] and p["python"] and p["config"] == {"seed": 42}
    with pytest.raises(ValueError):
        provenance_manifest("a" * 64, {"password": "never-persist"})


@pytest.mark.parametrize("section_id,title", SECTIONS)
def test_rep2_rpt3_mandatory_sections(model, section_id, title):
    section = next(s for s in model.data["sections"] if s["id"] == section_id)
    assert section["title"] == title and tuple(section["template"]) != ()
    assert set(section["template"]) == set(FIELDS)
    assert section["sentences"]


@pytest.mark.parametrize("depth", ["executive", "standard", "full"])
def test_renderers_twenty_metrics_links_and_page_budget(model, tmp_path, depth):
    pdf = tmp_path / (depth + ".pdf")
    xlsx = tmp_path / (depth + ".xlsx")
    result = render_pdf(model, pdf, depth=depth)
    render_excel(model, xlsx, depth=depth)
    assert len(model.metrics) == 20 and all(m["value"] is not None for m in model.metrics)
    assert verify_metric_agreement(model, pdf_metrics(pdf), excel_metrics(xlsx))
    reader = PdfReader(pdf)
    assert result["engine"] == "reportlab" and "ReportLab" in str(reader.metadata.producer)
    if depth == "executive":
        assert len(reader.pages) <= 10
    expected = {s["title"] for s in selected_sections(model, depth)}
    destinations = reader.outline
    assert {d.title for d in destinations} == expected
    assert all(0 <= reader.get_destination_page_number(d) < len(reader.pages) for d in destinations)
    assert any("/Annots" in page for page in reader.pages)
    html = render_html(model, depth=depth)
    facts = HTMLFacts(html)
    assert facts.hrefs and all(h.startswith("#") and h[1:] in facts.ids for h in facts.hrefs)
    assert all(image.get("alt") and image["src"].startswith("data:image/png;base64,") for image in facts.images)
    assert not re.search(r"(?:src|href)=[\"\']https?://", html)
    wb = load_workbook(xlsx)
    try:
        links = [cell.hyperlink.target for row in wb["Contents"] for cell in row if cell.hyperlink]
        assert len(links) == len(expected)
        assert all(target.split("'")[1] in wb.sheetnames for target in links)
    finally:
        wb.close()


def test_pdf_deterministic_and_empty_report_all_depths(model, tmp_path):
    a, b = tmp_path / "a.pdf", tmp_path / "b.pdf"
    render_pdf(model, a, depth="executive")
    render_pdf(model, b, depth="executive")
    assert a.read_bytes() == b.read_bytes()
    empty = assemble_report({}, provenance=provenance_manifest("0" * 64, {}))
    for depth in ["executive", "standard", "full"]:
        render_pdf(empty, a, depth=depth)
        render_excel(empty, tmp_path / "empty.xlsx", depth=depth)
        assert "Executive Summary" in render_html(empty, depth=depth)
        assert PdfReader(a).pages


def test_rpt4_chart_units_sample_size_palette_bands_and_alt_text(model):
    assert {c["kind"] for c in model.data["charts"].values()} >= {
        "actual_predicted",
        "residual_acf",
        "residual_qq",
        "cusum",
        "prediction_intervals",
        "driver_response",
    }
    for cid, chart in model.data["charts"].items():
        assert chart["alt"] and chart["unit"] and set(chart["palette"]) <= set(OKABE_ITO)
        fig = figure_for(model, cid)
        ax = fig.axes[0]
        assert f"n={chart['n']}" in ax.get_title() and ax.get_ylabel()
        assert all(line.get_color() in OKABE_ITO for line in ax.lines)
        if chart["kind"] == "prediction_intervals":
            assert ax.collections
        assert b"<svg" in render_chart(model, cid, format="svg")
        assert render_chart(model, cid).startswith(b"\x89PNG")
        fig.clear()


def test_numeric_artifact_build_failures_and_no_template_metrics(model):
    data = model.data
    data.pop("report_id")
    data["sections"][0]["sentences"][0] = "Invented metric 123"
    with pytest.raises(ValueError):
        ReportModel(data)
    data = model.data
    data.pop("report_id")
    data["sections"][0]["sentences"][0] = "Invented metric 123 [artifact:metric.t1.cvrmse]"
    with pytest.raises(ValueError):
        ReportModel(data)
    data = model.data
    data.pop("report_id")
    data["sections"][0]["template"]["Results"] = "Result is 123"
    with pytest.raises(ValueError):
        ReportModel(data)
    data = model.data
    data.pop("report_id")
    data["charts"][next(iter(data["charts"]))]["palette"] = ["red"]
    with pytest.raises(ValueError):
        ReportModel(data)
    data = model.data
    data.pop("report_id")
    data["sections"][0]["sentences"][0] = "Bad reference [artifact:missing]"
    with pytest.raises(ValueError):
        ReportModel(data)


def test_companions_and_mandatory_disclosure_banner(model, tmp_path):
    outputs = export_companions(model, tmp_path)
    assert ReportModel.load(outputs["model"]).report_id == model.report_id
    tables = json.loads((tmp_path / "tables.json").read_text())
    assert any(k.endswith(".predictions") for k in tables)
    assert sum(1 for _ in (tmp_path / "metrics.csv").open()) == 21
    banner = disclosure_banner(model)
    assert all(item["details"] != ["None recorded"] for item in banner)
    html = render_html(model, depth="executive")
    assert "Engineer documented selection override" in html and "optional backend unavailable" in html


def rewrite_archive(source, destination, change, *, rehash=False):
    with zipfile.ZipFile(source) as z:
        files = {n: z.read(n) for n in z.namelist()}
    change(files)
    if rehash:
        manifest = json.loads(files["SHA256SUMS.json"])
        manifest["files"] = {n: hashlib.sha256(raw).hexdigest() for n, raw in files.items() if n != "SHA256SUMS.json"}
        files["SHA256SUMS.json"] = canonical(manifest).encode()
    with zipfile.ZipFile(destination, "w") as z:
        for name, raw in files.items():
            z.writestr(name, raw)


def test_archive_hashes_tables_and_renderer_agreement(model, tmp_path):
    path = tmp_path / "audit.zip"
    export = export_archive(model, path, depth="standard")
    verified = verify_archive(path, expected_sha256=export["sha256"])
    assert verified["ok"] and verified["metrics_reproduced"] == 20 and verified["tables_reproduced"] > 0
    bad = tmp_path / "bad.zip"
    rewrite_archive(path, bad, lambda files: files.update({"tables.json": b"{}"}))
    with pytest.raises(ValueError):
        verify_archive(bad)
    rewrite_archive(path, bad, lambda files: files.update({"tables.json": b"{}"}), rehash=True)
    with pytest.raises(ValueError, match="reproduce"):
        verify_archive(bad)
    rewrite_archive(path, bad, lambda files: files.update({"../outside.json": b"{}"}), rehash=True)
    with pytest.raises(ValueError):
        verify_archive(bad)
    with pytest.raises(ValueError):
        verify_archive(path, expected_sha256="0" * 64)


def test_report_diff_and_formula_export_preserves_live_formulas(model, tmp_path):
    assert report_diff(model, ReportModel(model.data)) == {}
    data = model.data
    data.pop("report_id")
    data["artifacts"]["glossary"]["value"].append({"term": "new", "meaning": "extra"})
    assert "glossary" in report_diff(model, ReportModel(data))
    from energybaseline.lock.baseline_lock import BaselineLockManager, verify_excel_formula_roundtrip
    import pandas as pd
    from sklearn.linear_model import LinearRegression

    X = pd.DataFrame({"driver": np.arange(10, dtype=float)})
    y = 4 + 2 * X.driver.to_numpy()
    fit = LinearRegression().fit(X, y)
    rec = BaselineLockManager(tmp_path / "locks").lock_baseline(
        run_id="g8",
        target="energy",
        model_name="OLS",
        family="F1",
        is_parametric=True,
        fitted_artifact=fit,
        formula_or_architecture="b0+b1*driver",
        coefficients={"intercept": 4.0, "driver": 2.0},
        selected_features=["driver"],
    )
    path = tmp_path / "formulas.xlsx"
    export_formula_workbook(rec, X, y, path)
    assert verify_excel_formula_roundtrip(path, fit.predict(X), y).passed


def test_cli_report_export_and_archive_verify(model, tmp_path, capsys):
    from energybaseline.cli import main

    model_path = tmp_path / "model.json"
    model.save(model_path)
    assert main(["report-export", str(model_path), "--directory", str(tmp_path / "out"), "--depth", "executive"]) == 0
    assert main(["audit-verify", str(tmp_path / "out/audit.zip")]) == 0
    capsys.readouterr()


def test_canonical_audit_spelling_delegates_to_real_pinned_tool():
    import subprocess
    import sys

    alias = subprocess.check_output([sys.executable, "-m", "pip-audit", "--version"], text=True)
    official = subprocess.check_output([sys.executable, "-m", "pip_audit", "--version"], text=True)
    assert alias == official and "pip-audit" in alias


def test_report_content_and_spreadsheet_injection_are_rejected_or_literal(model, tmp_path):
    data = model.data
    data.pop("report_id")
    data["provenance"]["config"]["token"] = "do-not-export"
    with pytest.raises(ValueError):
        ReportModel(data)
    data = model.data
    data.pop("report_id")
    data["title"] = "https://remote.example/tracker"
    with pytest.raises(ValueError):
        ReportModel(data)
    data = model.data
    data.pop("report_id")
    data["artifacts"]["glossary"]["value"].append({"term": "=1+1", "meaning": "literal external input"})
    literal = ReportModel(data)
    path = tmp_path / "literal.xlsx"
    render_excel(literal, path, depth="full")
    wb = load_workbook(path, data_only=False)
    try:
        found = [cell for sheet in wb for row in sheet for cell in row if cell.value == "=1+1"]
        assert found and all(c.data_type == "s" for c in found)
    finally:
        wb.close()


def test_pipeline_actual_target_seed_lineage_and_repeatability():
    import pandas as pd
    from energybaseline.core.pipeline import run_analysis
    from energybaseline.core import ingest

    source = "samples/monthly_building.csv"
    df = pd.read_csv(source)
    sha = ingest.sha256_file(source)
    run, bundles = run_analysis(
        df,
        "Month",
        ["Energy_kWh"],
        {"seed": 71, "hpo_trials": "0", "enable_mining": "false", "nn_max_epochs": "10"},
        dataset_sha=sha,
    )
    lineage = run.provenance["seed_lineage"]
    expected = derive_seed(sha, "Energy_kWh", 71)
    assert lineage["dataset_sha256"] == sha and lineage["target_seeds"]["Energy_kWh"] == expected
    assert all(bundle["seed"] == expected for bundle in bundles["Energy_kWh"].values())


def test_archive_malformed_and_duplicate_members_fail_closed(model, tmp_path):
    path = tmp_path / "malformed.zip"
    path.write_bytes(b"not a ZIP")
    with pytest.raises(ValueError):
        verify_archive(path)
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("same.json", "{}")
        z.writestr("same.json", "{}")
    with pytest.raises(ValueError):
        verify_archive(path)
    for cid, c in model.data["charts"].items():
        if c["kind"] == "correlation":
            fig = figure_for(model, cid)
            assert fig.axes[0].images and all(image.get_cmap().name == "viridis" for image in fig.axes[0].images)
            fig.clear()
