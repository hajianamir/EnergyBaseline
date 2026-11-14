"""Analysis-to-JSON boundary. Renderers receive only the immutable report model."""

from __future__ import annotations
from dataclasses import asdict, is_dataclass
import hashlib
import math
from collections.abc import Mapping
import numpy as np
import pandas as pd

from ..report.model import ReportModel
from ..report.provenance import canonical, provenance_manifest
from ..report.sections import mandatory_sections
from ..report.narrative import metric_statement
from ..report.charts import chart_spec


def json_value(value):
    if value is None or value is pd.NA or value is pd.NaT:
        return None
    if isinstance(value, np.generic):
        return json_value(value.item())
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, pd.DataFrame):
        frame = value.copy()
        if not isinstance(frame.index, pd.RangeIndex):
            frame = frame.reset_index(names="observation")
        return [json_value(row) for row in frame.to_dict("records")]
    if isinstance(value, pd.Series):
        return json_value(value.to_dict())
    if isinstance(value, Mapping):
        return {str(k): json_value(v) for k, v in value.items()}
    if isinstance(value, (tuple, list, np.ndarray)):
        return [json_value(v) for v in value]
    if isinstance(value, (pd.Timestamp, np.datetime64)):
        return str(value)
    if is_dataclass(value):
        return json_value(asdict(value))
    raise ValueError(f"Unsupported report state type: {type(value).__name__}")


def assemble_report(state, *, provenance=None):
    """Build sections from persisted JSON state, without fitting or inferential testing."""
    state = json_value(state)
    artifacts = {}
    tables = {}
    sentences = {}
    charts = {}
    chart_links = {}
    metrics = []
    disclosures = {key: [] for key in ("manual_overrides", "skipped_families", "assumption_fallbacks")}

    def add_table(section, aid, rows, source):
        if isinstance(rows, dict):
            rows = [{"field": k, "value": v} for k, v in rows.items()]
        rows = [row if isinstance(row, dict) else {"value": row} for row in (rows or [])]
        artifacts[aid] = {"kind": "table", "value": rows, "source": source}
        tables.setdefault(section, []).append(aid)
        return aid

    add_table("vsg_audit", "vsg", state.get("vsg", {}), "VSG_Audit")
    add_table("vsg_audit", "data_dictionary", state.get("data_dictionary", []), "Data_Dictionary")
    add_table("tcp", "tcp", state.get("tcp", {}), "TCP")
    add_table("auto_log", "auto", state.get("auto", []), "AUTO_Resolutions")
    add_table("selections", "selections", state.get("selections", []), "User_Selections")
    add_table("audit_appendix", "audit", state.get("audit", []), "Audit_Log")
    add_table(
        "storage",
        "storage",
        [{"policy": "Offline local storage; connection material excluded from reports"}],
        "Local_Storage",
    )
    add_table(
        "glossary",
        "glossary",
        [
            {"term": "EnPI", "meaning": "Energy performance indicator"},
            {"term": "UQ", "meaning": "Uncertainty quantification"},
            {"term": "CV(RMSE)", "meaning": "Root mean squared error normalized by mean energy"},
            {"term": "NMBE", "meaning": "Normalized mean bias error"},
            {"term": "Artifact ID", "meaning": "Stable reference to persisted source evidence"},
        ],
        "Glossary",
    )
    for selection in state.get("selections", []):
        if selection.get("note"):
            disclosures["manual_overrides"].append(str(selection["note"]))
    for i, target in enumerate(state.get("targets", []), 1):
        name = target["name"]
        prefix = f"t{i}"
        shortlist = target.get("shortlist", [])
        best = shortlist[0] if shortlist else {}
        quality = target.get("quality", {})
        m = best.get("metrics", {})
        leaderboard = target.get("leaderboard", [])
        series = target.get("series", [])
        actual = [r["actual"] for r in series if isinstance(r.get("actual"), (float, int))]
        values = [
            ("n_obs", "Observations", target.get("n_obs"), "observations"),
            ("quality", "Quality score", quality.get("score"), "score"),
            ("completeness", "Completeness", quality.get("completeness"), "fraction"),
            ("validity", "Validity", quality.get("validity"), "fraction"),
            ("uniqueness", "Uniqueness", quality.get("uniqueness"), "fraction"),
            ("cvrmse", "CV(RMSE)", m.get("cvrmse"), "%"),
            ("nmbe", "NMBE", m.get("nmbe"), "%"),
            ("r_squared", "R-squared", m.get("r2"), "dimensionless"),
            ("rmse", "RMSE", m.get("rmse"), target.get("unit", "unspecified unit")),
            ("mae", "MAE", m.get("mae"), target.get("unit", "unspecified unit")),
            ("mape", "MAPE", m.get("mape"), "%"),
            ("smape", "sMAPE", m.get("smape"), "%"),
            ("aic", "AIC", m.get("aic"), "score"),
            ("bic", "BIC", m.get("bic"), "score"),
            ("n_params", "Parameter count", best.get("n_params", m.get("n_params")), "parameters"),
            ("score", "Composite score", best.get("score"), "score"),
            ("n_models", "Evaluated models", len(leaderboard), "models"),
            ("n_qualified", "Qualified models", sum(bool(r.get("qualified")) for r in leaderboard), "models"),
            ("n_features", "Selected features", len(target.get("selected_features", [])), "features"),
            (
                "actual_mean",
                "Observed mean",
                sum(actual) / len(actual) if actual else None,
                target.get("unit", "unspecified unit"),
            ),
        ]
        for key, label, value, unit in values:
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                value = None
            aid = f"metric.{prefix}.{key}"
            artifacts[aid] = {
                "kind": "metric",
                "value": value,
                "source": f"{name}: persisted analysis / rank-one diagnostic",
            }
            metrics.append({"artifact_id": aid, "label": name + " / " + label, "unit": unit})
            sentences.setdefault("executive", []).append(metric_statement("Recorded metric", value, aid))
        for section, key, sheet in [
            ("test_catalog", "test_catalog", f"TestCatalog_{i}_{name}"),
            ("model_register", "leaderboard", f"Leaderboard_{i}_{name}"),
            ("shortlist", "shortlist", f"Top5_{i}_{name}"),
            ("rejected", "register", f"Rejected_{i}_{name}"),
            ("not_shortlisted", "qualified_not_shortlisted_register", f"QualifiedOther_{i}"),
            ("sensitivity", "weight_sensitivity_20pct", f"Sensitivity_{i}"),
            ("model_cards", "shortlist", f"ModelCards_{i}"),
            ("selected_baseline", "selected_baseline", f"Selected_{i}"),
            ("monitoring", "monitoring", f"Monitoring_{i}"),
            ("drift", "drift", f"Drift_{i}"),
            ("vsg_audit", "exclusions", f"Exclusions_{i}"),
            ("vsg_audit", "temporal", f"Temporal_{i}"),
            ("eda_atlas", "eda", f"EDA_{i}"),
            ("correlation_atlas", "relationships", f"Relationships_{i}"),
        ]:
            aid = add_table(section, f"{prefix}.{section}.{key}", target.get(key, []), sheet)
            if key == "test_catalog":
                tables.setdefault("test_appendix", []).append(aid)
        # Persist the prediction companion even when some series fields are unavailable.
        rows = []
        cumulative = 0.0
        for raw in series:
            a = raw.get("actual")
            p = raw.get("predicted", raw.get("baseline_oof"))
            residual = a - p if a is not None and p is not None else None
            cumulative += residual or 0.0
            rows.append({**raw, "predicted": p, "residual": residual, "cusum": raw.get("cusum", cumulative)})
        aid = add_table("monitoring", f"{prefix}.predictions", rows, f"Predictions_{i}")
        if rows:
            kinds = ["actual_predicted", "residual_acf", "residual_qq", "cusum", "eda", "correlation"]
            if any(r.get("lower") is not None and r.get("upper") is not None for r in rows):
                kinds.append("prediction_intervals")
            for kind in kinds:
                cid = f"{prefix}.{kind}"
                charts[cid] = chart_spec(
                    aid,
                    kind,
                    unit=target.get("unit", "unspecified unit"),
                    n=len(rows),
                    title=name + " / " + kind + " (rank-one diagnostic)",
                    alt=name
                    + " "
                    + kind.replace("_", " ")
                    + " from persisted observations; missing values remain gaps",
                )
                chart_links.setdefault(
                    "correlation_atlas"
                    if kind == "correlation"
                    else "eda_atlas"
                    if kind == "eda"
                    else "selected_baseline"
                    if kind == "prediction_intervals"
                    else "monitoring",
                    [],
                ).append(cid)
        for driver, pdp in (best.get("importance", {}).get("pdp", {}) or {}).items():
            if not pdp.get("x"):
                continue
            response = [{"driver": x, "response": y, "driver_unit": driver} for x, y in zip(pdp["x"], pdp["y"])]
            aid = add_table("eda_atlas", f"{prefix}.response", response, f"DriverResponse_{i}")
            charts[f"{prefix}.driver_response"] = chart_spec(
                aid,
                "driver_response",
                unit=target.get("unit", "unspecified unit"),
                n=len(response),
                title="Persisted driver partial response",
                alt="Model response to " + driver + " over its persisted grid",
            )
            chart_links.setdefault("eda_atlas", []).append(f"{prefix}.driver_response")
            break
        for entry in target.get("model_evaluation_register", []) + target.get("register", []):
            if "skip" in str(entry.get("status", "")).lower():
                disclosures["skipped_families"].append(str(entry))
        for result in target.get("test_catalog", []):
            if result.get("inference_label") and "fallback" in canonical(result).lower():
                disclosures["assumption_fallbacks"].append(str(result))
        disclosures["assumption_fallbacks"].extend(
            str(n) for n in target.get("notes", []) if "warning" in str(n).lower()
        )
    provenance = provenance or state.get("provenance")
    if not provenance:
        raise ValueError("Persisted provenance manifest is required")
    add_table("reproducibility", "provenance", provenance, "Reproducibility")
    for key, details in disclosures.items():
        add_table("disclosures", "disclosure." + key, [{"detail": d} for d in details], key)
    from ..report.narrative import bind_annotation

    for category, details in disclosures.items():
        linked = []
        for j, text in enumerate(details):
            aid = f"annotation.{category}.{j}"
            artifacts[aid] = {"kind": "annotation", "value": text, "source": "persisted disclosure annotation"}
            linked.extend(bind_annotation(text, aid))
        disclosures[category] = linked
    sections = mandatory_sections(artifacts, tables=tables, sentences=sentences, charts=chart_links)
    return ReportModel(
        {
            "schema_version": 1,
            "title": state.get("title", "Energy Baseline Analytics Report"),
            "artifacts": artifacts,
            "metrics": metrics,
            "sections": sections,
            "charts": charts,
            "provenance": provenance,
            "disclosures": disclosures,
        }
    )


def model_from_analysis(run, selections, audit, data_dictionary=None):
    """The sole boundary permitted to inspect the analysis dataclasses."""
    targets = []
    for name, tr in run.targets.items():
        data = {k: v for k, v in vars(tr).items() if k not in ("eda", "series", "seconds")}
        data.update(
            name=name,
            series=json_value(tr.series) if tr.series is not None else [],
            eda={k: v for k, v in tr.eda.items() if k != "relationships"},
            relationships=tr.eda.get("relationships", []),
            unit=run.dataset.get("units", {}).get(name, "unspecified unit"),
        )
        active = next((s for s in reversed(selections) if s.get("target") == name and s.get("active", True)), None)
        data["selected_baseline"] = [active] if active else []
        targets.append(json_value(data))
    dataset = json_value(run.dataset)
    sha = dataset.get("sha256", dataset.get("dataset_sha256", ""))
    if not isinstance(sha, str) or len(sha) != 64:
        sha = hashlib.sha256(canonical(dataset).encode()).hexdigest()
    config = json_value(run.config)
    lineage = run.provenance.get("seed_lineage", {})
    sha = lineage.get("dataset_sha256", sha)
    seeds = lineage.get("target_seeds", {})
    # Old persisted analyses have no recorded target seed lineage; do not fabricate it.
    provenance = run.provenance.get("runtime_manifest")
    if provenance is None:
        provenance = provenance_manifest(sha, config, target_seeds=seeds)
        provenance["capture_stage"] = "legacy export environment; original full runtime manifest unavailable"
        provenance["analysis_versions"] = json_value(run.versions)
    return assemble_report(
        {
            "targets": targets,
            "vsg": run.vsg_summary,
            "tcp": run.tcp_summary,
            "auto": run.auto,
            "selections": selections,
            "audit": audit,
            "data_dictionary": json_value(data_dictionary) if data_dictionary is not None else [],
            "provenance": provenance,
        }
    )
