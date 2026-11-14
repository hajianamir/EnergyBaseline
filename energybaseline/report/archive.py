"""Deterministic audit ZIP export and bounded, extraction-free verification."""

from __future__ import annotations
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import tempfile
import zipfile
from .provenance import canonical
from .model import ReportModel
from .excel import table_projection, render_excel, export_companions
from .html import render_html
from .pdf import render_pdf


def export_archive(model, path, *, depth="full"):
    with tempfile.TemporaryDirectory(prefix="eb_audit_") as temp:
        root = Path(temp)
        export_companions(model, root)
        (root / "provenance.json").write_text(canonical(model.data["provenance"]) + "\n", encoding="utf-8")
        (root / "config.json").write_text(canonical(model.data["provenance"]["config"]) + "\n", encoding="utf-8")
        (root / "report.html").write_text(render_html(model, depth=depth), encoding="utf-8")
        render_pdf(model, root / "report.pdf", depth=depth)
        render_excel(model, root / "report.xlsx", depth=depth)
        files = {p.name: p.read_bytes() for p in root.iterdir()}
    hashes = {name: hashlib.sha256(raw).hexdigest() for name, raw in files.items()}
    files["SHA256SUMS.json"] = (canonical({"report_id": model.report_id, "files": hashes}) + "\n").encode()
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in sorted(files):
            info = zipfile.ZipInfo(name, (2000, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, files[name])
    return {
        "report_id": model.report_id,
        "sha256": hashlib.sha256(Path(path).read_bytes()).hexdigest(),
        "files": hashes,
    }


def _verify_archive(path, *, expected_sha256=None):
    if Path(path).stat().st_size > 100_000_000:
        raise ValueError("Archive size limit exceeded")
    raw = Path(path).read_bytes()
    if len(raw) > 100_000_000:
        raise ValueError("Archive size limit exceeded")
    digest = hashlib.sha256(raw).hexdigest()
    if expected_sha256 is not None and digest != expected_sha256:
        raise ValueError("Archive digest mismatch")
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        infos = archive.infolist()
        names = [i.filename for i in infos]
        if len(set(names)) != len(names) or len(names) > 100:
            raise ValueError("Duplicate or excessive archive entries")
        if sum(i.file_size for i in infos) > 150_000_000:
            raise ValueError("Expanded archive size limit exceeded")
        for item in infos:
            name = PurePosixPath(item.filename)
            if (
                name.is_absolute()
                or len(name.parts) != 1
                or "\\" in item.filename
                or item.file_size > 80_000_000
                or (item.external_attr >> 16) & 0o170000 == 0o120000
            ):
                raise ValueError("Unsafe archive entry")
        payload = {name: archive.read(name) for name in names}
    manifest = json.loads(payload.pop("SHA256SUMS.json"))
    if set(payload) != set(manifest["files"]):
        raise ValueError("Archive manifest membership mismatch")
    for name, value in payload.items():
        if hashlib.sha256(value).hexdigest() != manifest["files"][name]:
            raise ValueError("Archive file hash mismatch")
    required = {
        "report-model.json",
        "tables.json",
        "metrics.csv",
        "provenance.json",
        "config.json",
        "report.html",
        "report.pdf",
        "report.xlsx",
    }
    if set(payload) != required:
        raise ValueError("Audit files missing or unrecognized")
    model = ReportModel(json.loads(payload["report-model.json"]))
    if model.report_id != manifest["report_id"]:
        raise ValueError("Archive report binding mismatch")
    if table_projection(model) != json.loads(payload["tables.json"]):
        raise ValueError("Persisted report tables do not reproduce")
    if model.data["provenance"] != json.loads(payload["provenance.json"]) or model.data["provenance"][
        "config"
    ] != json.loads(payload["config.json"]):
        raise ValueError("Provenance binding mismatch")
    from .agreement import pdf_metrics, excel_metrics, html_metrics, csv_metrics, verify_metric_agreement

    verify_metric_agreement(
        model,
        pdf_metrics(io.BytesIO(payload["report.pdf"])),
        excel_metrics(io.BytesIO(payload["report.xlsx"])),
        html_metrics(payload["report.html"]),
        csv_metrics(payload["metrics.csv"]),
    )
    return {
        "ok": True,
        "sha256": digest,
        "report_id": model.report_id,
        "tables_reproduced": len(table_projection(model)),
        "metrics_reproduced": len(model.metrics),
        "files_verified": len(payload),
    }


def verify_archive(path, *, expected_sha256=None):
    from pypdf.errors import PdfReadError

    try:
        return _verify_archive(path, expected_sha256=expected_sha256)
    except (
        zipfile.BadZipFile,
        KeyError,
        TypeError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        PdfReadError,
        RuntimeError,
    ) as exc:
        raise ValueError("Malformed or unsupported audit archive") from exc
