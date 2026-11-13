"""Command-line interface for Phase 2 dataset schema management and temporal preview (DAT-6, E2)."""

from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping, Sequence
import json
from pathlib import Path
import sys
from types import MappingProxyType
from typing import Any

from .config import ensure_dirs
from .core import ingest, store
from .core.auto import AutoLog, inspect_auto_log
from .core.cache import CachedComplianceRunner
from .core.integrity import IntegrityError
from .core.quality import preprocess_target
from .core.schema import (
    ColumnRole,
    ColumnSpec,
    DatasetSchema,
    Inclusion,
    NonRoutineEventSpec,
    OutlierActionPolicy,
    QuantityType,
    SchemaIntegrityError,
    SchemaValidationError,
    suggest_dataset_schema,
)
from .core.tcp import (
    MultipleTestingCorrection,
    TCPValidationError,
    TestConfigPanel,
    compute_impact_preview,
    compute_target_characterization_profile,
)
from .core.telemetry import build_system_health, build_system_sbom
from .core.temporal import TemporalError, prepare_temporal
from .core.vsg import ComplianceGateError, VSGValidationError, VariableSelectionGate
from .db.session import init_database
from .tests_catalog import (
    StaleRReferenceError,
    check_r_reference_freshness,
    run_full_test_catalog,
    validate_python_against_r_references,
)


def _parse_kv_updates(spec_arg: str) -> tuple[str, dict[str, Any]]:
    """Parse ``<col>:role=target,quantity_type=flow,inclusion=observed_only,unit=kWh,availability=always``."""
    if ":" not in spec_arg:
        raise SchemaValidationError(
            f"Invalid --column specification '{spec_arg}'. Expected '<col_name>:key=value[,key=value...]'."
        )
    col_name, rhs = spec_arg.split(":", 1)
    col_name = col_name.strip()
    if not col_name:
        raise SchemaValidationError("Column name in --column specification must not be empty.")
    updates: dict[str, Any] = {"suggested_by_heuristic": False}
    allowed_keys = {"role", "quantity_type", "inclusion", "unit", "availability", "quality_agg", "notes"}
    for item in rhs.split(","):
        item = item.strip()
        if not item:
            continue
        if "=" not in item:
            raise SchemaValidationError(f"Invalid key=value pair '{item}' in --column '{spec_arg}'.")
        k, v = item.split("=", 1)
        key = k.strip().lower()
        val = v.strip()
        if key not in allowed_keys:
            raise SchemaValidationError(f"Unsupported ColumnSpec attribute '{key}'. Allowed: {sorted(allowed_keys)}.")
        if key == "quantity_type" and val.lower() in ("none", "null", "-"):
            updates[key] = None
        else:
            updates[key] = val
    return col_name, updates


def _parse_nre_arg(raw: str) -> NonRoutineEventSpec:
    """Parse ``name,start,end[,action[,reason]]``."""
    parts = [p.strip() for p in raw.split(",")]
    if len(parts) < 3:
        raise SchemaValidationError(
            f"Invalid --add-nre '{raw}'. Expected 'name,start_iso,end_iso[,action[,reason]]'."
        )
    name, start, end = parts[0], parts[1], parts[2]
    action = parts[3] if len(parts) >= 4 and parts[3] else "exclude"
    reason = ",".join(parts[4:]) if len(parts) >= 5 else ""
    return NonRoutineEventSpec(name=name, start=start, end=end, action=action, reason=reason)


def _write_or_print(payload: Mapping[str, Any], out_path: str | None) -> None:
    rendered = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if out_path:
        p = Path(out_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(rendered, encoding="utf-8")
    sys.stdout.write(rendered)


def _cmd_schema_suggest(args: argparse.Namespace) -> int:
    df = ingest.load_file(args.dataset)
    sha = ingest.sha256_file(args.dataset)
    time_col = args.time_col or ingest.guess_time_column(df)
    targets = [t.strip() for t in (args.targets or "").split(",") if t.strip()]
    schema = suggest_dataset_schema(
        df,
        time_col=time_col,
        targets=targets or None,
        dataset_name=Path(args.dataset).name,
        dataset_sha256=sha,
        timezone=args.timezone or "",
        outlier_policy=args.outlier_policy or OutlierActionPolicy.FLAG_ONLY.value,
    )
    summary = ingest.build_ingestion_summary(
        df,
        source_name=Path(args.dataset).name,
        source_sha256=sha,
        time_col=schema.time_column,
    )
    schema_id: int | None = None
    if args.save_db:
        ensure_dirs()
        init_database()
        ds_id = store.register_dataset(Path(args.dataset).name, str(Path(args.dataset)), sha, df)
        schema_id = store.save_dataset_schema(schema, dataset_id=ds_id, ingestion_summary=summary)

    if args.out:
        schema.save_json(args.out)

    envelope = schema.to_envelope()
    envelope["ingestion_summary"] = summary
    if schema_id is not None:
        envelope["persisted_schema_id"] = schema_id
    sys.stdout.write(json.dumps(envelope, indent=2, sort_keys=True) + "\n")
    return 0


def _cmd_schema_edit(args: argparse.Namespace) -> int:
    if args.schema_in:
        base_schema = DatasetSchema.load_json(args.schema_in, verify_sha256=True)
    elif args.dataset:
        df = ingest.load_file(args.dataset)
        sha = ingest.sha256_file(args.dataset)
        base_schema = suggest_dataset_schema(
            df,
            time_col=ingest.guess_time_column(df),
            dataset_name=Path(args.dataset).name,
            dataset_sha256=sha,
        )
    else:
        raise SchemaValidationError("Provide either --schema-in or --dataset for schema-edit.")

    cols_by_name: dict[str, dict[str, Any]] = {c.name: c.to_dict() for c in base_schema.columns}
    ordered_names = [c.name for c in base_schema.columns]

    for col_arg in args.column or []:
        col_name, updates = _parse_kv_updates(col_arg)
        if col_name not in cols_by_name:
            cols_by_name[col_name] = {
                "name": col_name,
                "role": ColumnRole.PREDICTOR.value,
                "quantity_type": QuantityType.QUALITY.value,
                "inclusion": Inclusion.OBSERVED_ONLY.value,
                "unit": "",
                "availability": "always",
                "quality_agg": "mean",
                "suggested_by_heuristic": False,
                "notes": "",
            }
            ordered_names.append(col_name)
        cols_by_name[col_name].update(updates)
        if cols_by_name[col_name].get("role") in (ColumnRole.TIME.value, ColumnRole.IGNORE.value, ColumnRole.GROUP.value):
            if "quantity_type" not in updates:
                cols_by_name[col_name]["quantity_type"] = None

    nre_list = [NonRoutineEventSpec.from_dict(e.to_dict()) for e in base_schema.non_routine_events]
    for raw_nre in args.add_nre or []:
        nre_list.append(_parse_nre_arg(raw_nre))

    edited = DatasetSchema(
        columns=[ColumnSpec.from_dict(cols_by_name[nm]) for nm in ordered_names],
        dataset_name=base_schema.dataset_name,
        dataset_sha256=base_schema.dataset_sha256,
        timezone=args.timezone if args.timezone is not None else base_schema.timezone,
        storage_timezone=base_schema.storage_timezone,
        outlier_policy=args.outlier_policy if args.outlier_policy is not None else base_schema.outlier_policy,
        non_routine_events=nre_list,
        schema_version=base_schema.schema_version,
    )

    if args.out:
        edited.save_json(args.out)

    schema_id: int | None = None
    if args.save_db:
        ensure_dirs()
        init_database()
        schema_id = store.save_dataset_schema(edited)

    envelope = edited.to_envelope()
    if schema_id is not None:
        envelope["persisted_schema_id"] = schema_id
    sys.stdout.write(json.dumps(envelope, indent=2, sort_keys=True) + "\n")
    return 0


def _cmd_schema_validate(args: argparse.Namespace) -> int:
    schema = DatasetSchema.load_json(args.schema, verify_sha256=True)
    df_cols: list[str] | None = None
    if args.dataset:
        df = ingest.load_file(args.dataset)
        df_cols = list(df.columns)
        schema.validate(df_columns=df_cols)
    else:
        schema.validate()

    result = {
        "valid": True,
        "schema_sha256": schema.compute_sha256(),
        "time_column": schema.time_column,
        "target_columns": schema.target_columns,
        "predictor_columns": schema.predictor_columns,
        "outlier_policy": schema.outlier_policy.value,
        "non_routine_events_count": len(schema.non_routine_events),
    }
    _write_or_print(result, args.out)
    return 0


def _cmd_temporal_preview(args: argparse.Namespace) -> int:
    df_raw = ingest.load_file(args.dataset)
    ds_sha = ingest.sha256_file(args.dataset)
    if args.schema:
        schema = DatasetSchema.load_json(args.schema, verify_sha256=True)
        schema.validate(df_columns=list(df_raw.columns))
    else:
        schema = suggest_dataset_schema(
            df_raw,
            time_col=ingest.guess_time_column(df_raw),
            dataset_name=Path(args.dataset).name,
            dataset_sha256=ds_sha,
        )

    df_norm, unit_log = ingest.normalize_units(df_raw, schema.units_map())
    cfg: dict[str, Any] = {"dataset_schema": schema.to_dict()}
    if args.period:
        cfg["sampling_period"] = args.period
    if args.timezone:
        cfg["timezone"] = args.timezone
    if args.outlier_policy:
        cfg["outlier_action_policy"] = args.outlier_policy

    target = schema.target_columns[0]
    auto = AutoLog(cfg, target)
    tdf, trep, gran = prepare_temporal(df_norm, schema.time_column, auto, schema=schema)
    prep = preprocess_target(
        tdf,
        target,
        schema.target_columns,
        gran,
        auto,
        seed=42,
        n_dups=trep.n_duplicates,
        schema=schema,
    )

    payload: dict[str, Any] = {
        "format": "energybaseline.phase2_temporal_preview",
        "dataset": {
            "name": Path(args.dataset).name,
            "sha256": ds_sha,
            "dataframe_sha256": ingest.sha256_dataframe(df_raw),
            "input_rows": int(len(df_raw)),
            "input_columns": list(df_raw.columns),
        },
        "schema": schema.to_envelope(),
        "unit_conversions": unit_log,
        "temporal_report": trep.as_dict(),
        "preprocessing": {
            "target": target,
            "retained_predictors": prep.predictors,
            "group_columns": prep.group_cols,
            "weight_column": prep.weight_col,
            "quality": prep.quality,
            "outlier_policy_record": prep.outliers.get("action_policy_record", {}),
            "non_routine_events_applied": prep.nre_applied,
            "exclusions": prep.exclusions,
            "notes": prep.notes,
        },
        "auto_resolutions": auto.as_dicts(),
    }
    _write_or_print(payload, args.out)
    return 0


def _cmd_system_health(args: argparse.Namespace) -> int:
    ensure_dirs()
    init_database()
    payload = build_system_health(run_db_selftest=True)
    _write_or_print(payload, args.out)
    return 0


def _cmd_system_sbom(args: argparse.Namespace) -> int:
    payload = build_system_sbom()
    _write_or_print(payload, args.out)
    return 0


def _load_dataset_and_vsg(
    dataset_path: str,
    targets_arg: str = "",
    preset: str = "",
) -> tuple[Any, str, VariableSelectionGate]:
    df = ingest.load_file(dataset_path)
    sha = ingest.sha256_file(dataset_path)
    time_col = ingest.guess_time_column(df)
    explicit_targets = [t.strip() for t in (targets_arg or "").split(",") if t.strip()]
    schema = suggest_dataset_schema(
        df,
        time_col=time_col,
        targets=explicit_targets or None,
        dataset_name=Path(dataset_path).name,
        dataset_sha256=sha,
    )
    vsg = VariableSelectionGate(
        df,
        targets=schema.target_columns,
        time_col=schema.time_column,
        schema=schema,
        dataset_sha256=sha,
    )
    if preset:
        for t in vsg.targets:
            vsg.apply_preset(t, preset)
    return df, sha, vsg


def _cmd_vsg_inspect(args: argparse.Namespace) -> int:
    _, _, vsg = _load_dataset_and_vsg(args.dataset, args.targets, args.preset or "")
    if args.accept:
        vsg.accept()
    tcp = TestConfigPanel(vsg)
    payload = {
        "impact_preview": compute_impact_preview(vsg, tcp),
        "vsg": vsg.to_dict(),
    }
    _write_or_print(payload, args.out)
    return 0


def _cmd_tcp_lock(args: argparse.Namespace) -> int:
    df, sha, vsg = _load_dataset_and_vsg(args.dataset, args.targets)
    vsg.accept()
    tcp = TestConfigPanel(vsg)
    if args.alpha is not None:
        tcp.set_alpha(args.alpha)
    if args.correction is not None:
        tcp.set_correction(args.correction)
    lock_sha = tcp.lock_preregistration()
    char_profiles = {
        t: compute_target_characterization_profile(
            df,
            target=t,
            predictors=vsg.included_variables(t),
            time_col=vsg.time_col,
            dataset_sha256=sha,
        )
        for t in vsg.targets
    }
    payload = {
        "auto_log_hash": vsg.auto_log.compute_hash(),
        "characterization_by_target": char_profiles,
        "preregistration_sha256": lock_sha,
        "provenance": vsg.provenance.to_dict(),
        "tcp": tcp.to_dict(),
        "vsg_sha256": vsg.vsg_sha256,
    }
    _write_or_print(payload, args.out)
    return 0


def _cmd_what_if(args: argparse.Namespace) -> int:
    df, _, vsg = _load_dataset_and_vsg(args.dataset, args.targets)
    vsg.accept()
    tcp = TestConfigPanel(vsg)
    tcp.lock_preregistration()
    runner = CachedComplianceRunner(seed=42)
    initial = runner.execute_tests(df, vsg, tcp)
    calls_after_initial = runner.engine_call_count

    probe_tcp = tcp.clone_for_what_if(
        alpha=args.alpha,
        correction=args.correction,
    )
    what_if_res = runner.what_if(probe_tcp)
    sens = runner.decision_sensitivity_table(tcp)
    payload = {
        "baseline_alpha": tcp.alpha,
        "baseline_correction": tcp.correction,
        "baseline_test_count": len(initial),
        "initial_engine_calls": calls_after_initial,
        "post_what_if_engine_calls": runner.engine_call_count,
        "sensitivity_summary": {
            "engine_calls_delta": sens["engine_calls_delta"],
            "fragile_test_count": sens["fragile_test_count"],
            "scenario_count": len(sens["scenarios"]),
        },
        "what_if": what_if_res,
    }
    _write_or_print(payload, args.out)
    return 0


def _cmd_auto_log_hash(args: argparse.Namespace) -> int:
    _, _, vsg1 = _load_dataset_and_vsg(args.dataset, args.targets)
    vsg1.accept()
    tcp1 = TestConfigPanel(vsg1)
    prereg1 = tcp1.lock_preregistration()
    hash1 = vsg1.auto_log.compute_hash()

    _, _, vsg2 = _load_dataset_and_vsg(args.dataset, args.targets)
    vsg2.accept()
    tcp2 = TestConfigPanel(vsg2)
    prereg2 = tcp2.lock_preregistration()
    hash2 = vsg2.auto_log.compute_hash()

    insp = inspect_auto_log(vsg1.auto_log.records)
    payload = {
        "all_auto_have_empirical_signals": insp["all_auto_have_empirical_signals"],
        "auto_entries": insp["auto_entries"],
        "auto_log_hash_run1": hash1,
        "auto_log_hash_run2": hash2,
        "byte_identical": hash1 == hash2 and prereg1 == prereg2,
        "override_entries": insp["override_entries"],
        "preregistration_sha256_run1": prereg1,
        "preregistration_sha256_run2": prereg2,
        "records": insp["records"],
        "total_entries": insp["total_entries"],
    }
    _write_or_print(payload, args.out)
    return 0


def _cmd_test_catalog(args: argparse.Namespace) -> int:
    df, schema, vsg = _load_dataset_and_vsg(args.dataset, args.targets)
    if args.group_col and args.group_col in df.columns:
        schema = schema.with_column(
            args.group_col,
            role=ColumnRole.GROUP.value,
            quantity_type=None,
            inclusion=Inclusion.OBSERVED_ONLY.value,
            suggested_by_heuristic=False,
        )
        vsg = VariableSelectionGate(df, time_col=schema.time_column, targets=vsg.targets, schema=schema)
    vsg.accept()
    tcp_cfg: dict[str, Any] = {}
    if args.alpha is not None:
        tcp_cfg["tcp_alpha"] = args.alpha
    if args.correction is not None:
        tcp_cfg["tcp_correction"] = args.correction
    tcp = TestConfigPanel(vsg, config=tcp_cfg)
    for tid in args.disable_test or []:
        t_up = tid.strip().upper()
        if t_up in tcp.test_panels:
            tcp.configure_test_panel(t_up, enabled=False)
    tcp.accept()

    suites: dict[str, list[dict[str, Any]]] = {}
    for target in vsg.targets:
        res_list = run_full_test_catalog(
            df,
            target_col=target,
            predictor_cols=vsg.included_variables(target),
            group_col=args.group_col if (args.group_col and args.group_col in df.columns) else None,
            panel=tcp,
            disabled_tests=args.disable_test or (),
            mode=args.mode,
            allow_hac=not args.no_hac,
            seed=42,
        )
        suites[target] = [r.to_dict() for r in res_list]

    payload = {
        "alpha": tcp.alpha,
        "confidence_level": tcp.confidence_level,
        "correction": tcp.correction,
        "decision_rule": tcp.decision_rule,
        "disabled_tests": sorted({t.strip().upper() for t in (args.disable_test or [])}),
        "mode": args.mode,
        "targets": suites,
    }
    _write_or_print(payload, args.out)
    return 0


def _cmd_r_references_check(args: argparse.Namespace) -> int:
    ref_dir = Path(args.reference_dir) if args.reference_dir else None
    scripts_dir = Path(args.scripts_dir) if args.scripts_dir else None
    freshness = check_r_reference_freshness(
        ref_dir,
        r_scripts_dir=scripts_dir,
        max_age_days=int(args.max_age_days),
        raise_on_failure=True,
    )
    parity = validate_python_against_r_references(ref_dir)
    if not parity["ok"]:
        raise StaleRReferenceError("R reference parity mismatch: " + "; ".join(parity["mismatches"]))
    payload = {
        "freshness": freshness,
        "ok": True,
        "parity": parity,
    }
    _write_or_print(payload, args.out)
    return 0


CLI_COMMAND_HANDLERS: Mapping[str, Callable[[argparse.Namespace], int]] = MappingProxyType(
    {
        "schema-suggest": _cmd_schema_suggest,
        "schema-edit": _cmd_schema_edit,
        "schema-validate": _cmd_schema_validate,
        "temporal-preview": _cmd_temporal_preview,
        "system.health": _cmd_system_health,
        "system-health": _cmd_system_health,
        "system.sbom": _cmd_system_sbom,
        "system-sbom": _cmd_system_sbom,
        "vsg-inspect": _cmd_vsg_inspect,
        "tcp-lock": _cmd_tcp_lock,
        "what-if": _cmd_what_if,
        "auto-log-hash": _cmd_auto_log_hash,
        "test-catalog": _cmd_test_catalog,
        "r-references-check": _cmd_r_references_check,
    }
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="energybaseline-cli",
        description="EnergyBaseline Phase 2 column schema & temporal validation CLI (DAT-6).",
    )
    sub = parser.add_subparsers(dest="subcommand", required=True)

    p_sug = sub.add_parser("schema-suggest", help="Inspect a dataset and emit a suggested DatasetSchema JSON envelope.")
    p_sug.add_argument("dataset", help="Path to CSV / TSV / Excel / Parquet dataset.")
    p_sug.add_argument("--time-col", default=None, help="Explicit timestamp column name.")
    p_sug.add_argument("--targets", default="", help="Comma-separated target column names.")
    p_sug.add_argument("--timezone", default="", help="Optional IANA timezone.")
    p_sug.add_argument(
        "--outlier-policy",
        default=OutlierActionPolicy.FLAG_ONLY.value,
        choices=[p.value for p in OutlierActionPolicy],
    )
    p_sug.add_argument("--out", default=None, help="Write schema JSON envelope to path.")
    p_sug.add_argument("--save-db", action="store_true", help="Also persist DatasetSchema in SQLite/DB.")

    p_edit = sub.add_parser("schema-edit", help="Assign or edit column metadata on a DatasetSchema.")
    p_edit.add_argument("--schema-in", default=None, help="Existing DatasetSchema JSON path.")
    p_edit.add_argument("--dataset", default=None, help="Dataset path (if creating from suggestion).")
    p_edit.add_argument(
        "--column",
        action="append",
        default=[],
        help="Column metadata spec '<col>:role=...,quantity_type=...,inclusion=...,unit=...,availability=...'",
    )
    p_edit.add_argument("--timezone", default=None, help="Override DatasetSchema IANA timezone.")
    p_edit.add_argument(
        "--outlier-policy",
        default=None,
        choices=[p.value for p in OutlierActionPolicy],
        help="Override OutlierActionPolicy.",
    )
    p_edit.add_argument(
        "--add-nre",
        action="append",
        default=[],
        help="Append Non-Routine Event 'name,start_iso,end_iso[,action[,reason]]'.",
    )
    p_edit.add_argument("--out", default=None, help="Write updated DatasetSchema JSON envelope to path.")
    p_edit.add_argument("--save-db", action="store_true", help="Persist updated DatasetSchema in SQLite/DB.")

    p_val = sub.add_parser("schema-validate", help="Verify DatasetSchema SHA-256 integrity and DAT-7 rules.")
    p_val.add_argument("schema", help="Path to DatasetSchema JSON file.")
    p_val.add_argument("--dataset", default=None, help="Optional dataset path to check column presence.")
    p_val.add_argument("--out", default=None, help="Optional output path for validation JSON.")

    p_prev = sub.add_parser("temporal-preview", help="Run temporal aggregation & outlier policy preview (E2 JSON).")
    p_prev.add_argument("dataset", help="Path to dataset file.")
    p_prev.add_argument("--schema", default=None, help="Path to DatasetSchema JSON file.")
    p_prev.add_argument("--period", default=None, help="Target sampling period (e.g. daily, hourly).")
    p_prev.add_argument("--timezone", default=None, help="IANA timezone override.")
    p_prev.add_argument(
        "--outlier-policy",
        default=None,
        choices=[p.value for p in OutlierActionPolicy],
        help="OutlierActionPolicy override.",
    )
    p_prev.add_argument("--out", default=None, help="Write machine-readable E2 JSON report to path.")

    for name in ("system.health", "system-health"):
        p_h = sub.add_parser(name, help="Emit machine-readable runtime system.health JSON.")
        p_h.add_argument("--out", default=None, help="Write JSON output to path.")

    for name in ("system.sbom", "system-sbom"):
        p_s = sub.add_parser(name, help="Emit CycloneDX 1.5 runtime system.sbom JSON.")
        p_s.add_argument("--out", default=None, help="Write JSON output to path.")

    p_vsg = sub.add_parser("vsg-inspect", help="Run Variable Selection Gate profiling and Impact Preview.")
    p_vsg.add_argument("dataset", help="Path to dataset file.")
    p_vsg.add_argument("--targets", default="", help="Optional comma-separated target columns.")
    p_vsg.add_argument("--preset", default="", help="Optional VSG preset name.")
    p_vsg.add_argument("--accept", action="store_true", help="Mark VSG gate accepted if integrity passes.")
    p_vsg.add_argument("--out", default=None, help="Write JSON output to path.")

    p_tcp = sub.add_parser("tcp-lock", help="Accept VSG/TCP and emit pre-registration lock SHA-256 + 14-D profile.")
    p_tcp.add_argument("dataset", help="Path to dataset file.")
    p_tcp.add_argument("--targets", default="", help="Optional comma-separated target columns.")
    p_tcp.add_argument("--alpha", type=float, default=None, help="Optional significance alpha in [0.001, 0.20].")
    p_tcp.add_argument(
        "--correction",
        default=None,
        choices=[c.value for c in MultipleTestingCorrection],
        help="Optional multiple-testing correction.",
    )
    p_tcp.add_argument("--out", default=None, help="Write JSON output to path.")

    p_wi = sub.add_parser("what-if", help="Run zero-recomputation What-If re-evaluation and sensitivity sweep.")
    p_wi.add_argument("dataset", help="Path to dataset file.")
    p_wi.add_argument("--targets", default="", help="Optional comma-separated target columns.")
    p_wi.add_argument("--alpha", type=float, default=0.01, help="What-if significance alpha.")
    p_wi.add_argument(
        "--correction",
        default=MultipleTestingCorrection.BONFERRONI.value,
        choices=[c.value for c in MultipleTestingCorrection],
        help="What-if multiple-testing correction.",
    )
    p_wi.add_argument("--out", default=None, help="Write JSON output to path.")

    p_ah = sub.add_parser("auto-log-hash", help="Verify deterministic AUTO-log SHA-256 across two runs.")
    p_ah.add_argument("dataset", help="Path to dataset file.")
    p_ah.add_argument("--targets", default="", help="Optional comma-separated target columns.")
    p_ah.add_argument("--out", default=None, help="Write JSON output to path.")

    p_tc = sub.add_parser("test-catalog", help="Execute Phase 4 statistical test catalog T1..T14 (E2 JSON).")
    p_tc.add_argument("dataset", help="Path to dataset file.")
    p_tc.add_argument("--targets", default="", help="Optional comma-separated target columns.")
    p_tc.add_argument("--group-col", default="", help="Optional causal group column for T14.")
    p_tc.add_argument("--alpha", type=float, default=None, help="Optional significance alpha.")
    p_tc.add_argument(
        "--correction",
        default=None,
        choices=[c.value for c in MultipleTestingCorrection],
        help="Optional multiple-testing correction.",
    )
    p_tc.add_argument("--mode", default="full", choices=["fast", "full"], help="Execution mode (fast or full).")
    p_tc.add_argument("--disable-test", action="append", default=[], help="Disable specific test ID (e.g. T7).")
    p_tc.add_argument("--no-hac", action="store_true", help="Disable HAC/block-bootstrap correction (forces naive label).")
    p_tc.add_argument("--out", default=None, help="Write JSON output to path.")

    p_rr = sub.add_parser("r-references-check", help="Validate versioned R reference JSON fixtures & <=14-day freshness.")
    p_rr.add_argument("--reference-dir", default=None, help="Optional directory containing <slug>.json fixtures.")
    p_rr.add_argument("--scripts-dir", default=None, help="Optional directory containing <slug>.R scripts.")
    p_rr.add_argument("--max-age-days", type=int, default=14, help="Maximum allowed R reference age in days.")
    p_rr.add_argument("--out", default=None, help="Write JSON output to path.")

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    handler = CLI_COMMAND_HANDLERS.get(args.subcommand)
    if handler is None:
        sys.stderr.write(f"ERROR: Unknown subcommand '{args.subcommand}'.\n")
        return 2
    try:
        return handler(args)
    except (
        SchemaIntegrityError,
        SchemaValidationError,
        TemporalError,
        ingest.IngestError,
        VSGValidationError,
        TCPValidationError,
        ComplianceGateError,
        IntegrityError,
        StaleRReferenceError,
    ) as exc:
        err_payload = {"valid": False, "error_type": type(exc).__name__, "error": str(exc)}
        sys.stderr.write(json.dumps(err_payload) + "\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
