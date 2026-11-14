"""Generate Gate G8 report/audit evidence from a real gated analysis and actual renderers."""

from __future__ import annotations

# ruff: noqa: E402
import argparse
import hashlib
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication
from energybaseline.core import ingest
from energybaseline.core.schema import suggest_dataset_schema
from energybaseline.core.vsg import VariableSelectionGate
from energybaseline.core.tcp import TestConfigPanel
from energybaseline.core.pipeline import run_analysis
from energybaseline.core.report_adapter import model_from_analysis
from energybaseline.report import (
    render_pdf,
    render_html,
    render_excel,
    export_archive,
    verify_archive,
    export_companions,
)
from energybaseline.report.agreement import pdf_metrics, excel_metrics, verify_metric_agreement
from energybaseline.report.provenance import canonical
from energybaseline.gui.report_preview import ReportPreview


def produce(directory):
    directory.mkdir(parents=True, exist_ok=True)
    source = ROOT / "samples/monthly_building.csv"
    df = ingest.load_file(source)
    sha = ingest.sha256_file(source)
    time_col = ingest.guess_time_column(df)
    schema = suggest_dataset_schema(df, time_col=time_col, targets=["Energy_kWh"], dataset_sha256=sha)
    vsg = VariableSelectionGate(df, targets=["Energy_kWh"], time_col=time_col, schema=schema, dataset_sha256=sha)
    vsg.accept()
    tcp = TestConfigPanel(vsg)
    tcp.lock_preregistration()
    run, _ = run_analysis(
        df,
        time_col,
        ["Energy_kWh"],
        {"hpo_trials": "0", "enable_mining": "false", "nn_max_epochs": "10", "seed": 42},
        dataset_sha=sha,
        dataset_info={"name": source.name, "sha256": sha, "units": {"Energy_kWh": "kWh"}},
        schema=schema,
        vsg=vsg,
        tcp=tcp,
        enforce_compliance_gates=True,
    )
    model = model_from_analysis(run, [], [])
    export_companions(model, directory)
    (directory / "provenance.json").write_text(canonical(model.data["provenance"]) + "\n", encoding="utf-8")
    pages = render_pdf(model, directory / "report.pdf", depth="executive")
    render_pdf(model, directory / "repeat.pdf", depth="executive")
    deterministic = (directory / "report.pdf").read_bytes() == (directory / "repeat.pdf").read_bytes()
    (directory / "repeat.pdf").unlink()
    render_excel(model, directory / "report.xlsx", depth="executive")
    (directory / "report.html").write_text(render_html(model, depth="executive"), encoding="utf-8")
    app = QApplication.instance() or QApplication([])
    preview = ReportPreview()
    preview.set_report(model)
    agreement = verify_metric_agreement(
        model, pdf_metrics(directory / "report.pdf"), excel_metrics(directory / "report.xlsx"), preview.metric_values()
    )
    preview.close()
    app.processEvents()
    exported = export_archive(model, directory / "audit.zip", depth="standard")
    verification = verify_archive(directory / "audit.zip", expected_sha256=exported["sha256"])
    (directory / "audit_archive.json").write_text(canonical({"export": exported, "verification": verification}) + "\n")
    (directory / "renderer_agreement.json").write_text(
        canonical(
            {
                "ok": agreement,
                "metrics": model.metrics,
                "count": len(model.metrics),
                "pdf_metadata": pages,
                "pdf_byte_deterministic": deterministic,
                "relative_and_absolute_tolerance": 1e-12,
            }
        )
        + "\n"
    )
    for command, name in [("system.health", "system_health.json"), ("system.sbom", "sbom.json")]:
        subprocess.run(
            [sys.executable, "-m", "energybaseline.cli", command, "--out", str(directory / name)],
            cwd=ROOT,
            check=True,
            stdout=subprocess.DEVNULL,
        )
    (directory / "manifest.json").write_text(
        canonical(
            {
                "source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
                "sha256": {
                    p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in sorted(directory.iterdir())
                    if p.is_file() and p.name != "manifest.json"
                },
            }
        )
        + "\n"
    )
    if not agreement or not deterministic or pages["pages"] > 10:
        raise RuntimeError("Report evidence gate failed")
    print(
        canonical(
            {"ok": True, "report_id": model.report_id, "metrics": len(model.metrics), "executive_pages": pages["pages"]}
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, default=ROOT / "artifacts/phase8")
    produce(parser.parse_args().out_dir.resolve())
