"""Persistence: runs, AUTO resolutions, model results, selections (history), audit log, snapshots."""
from __future__ import annotations

import datetime as dt
import gzip
import io
import json
import logging
from pathlib import Path

import joblib
import pandas as pd
from sqlalchemy import select

from ..config import ensure_dirs
from ..db import models as M
from ..db.session import session_scope
from .pipeline import RunResult
from .schema import DatasetSchema, NonRoutineEventSpec, SchemaIntegrityError, SchemaValidationError

log = logging.getLogger("energybaseline.store")


def audit(message: str, category: str = "general", level: str = "INFO", run_id: int | None = None, detail: dict | None = None) -> None:
    try:
        with session_scope() as s:
            s.add(M.AuditLog(run_id=run_id, level=level, category=category, message=message, detail_json=json.dumps(detail or {}, default=str)))
    except Exception as exc:  # audit must never break the app
        log.warning("audit write failed: %s", exc)


def register_dataset(name: str, path: str, sha: str, df: pd.DataFrame) -> int:
    schema = {c: str(t) for c, t in df.dtypes.items()}
    with session_scope() as s:
        d = M.Dataset(name=name, source_path=path, sha256=sha, n_rows=len(df), n_cols=df.shape[1], schema_json=json.dumps(schema))
        s.add(d)
        s.flush()
        did = d.id
    audit(f"Dataset imported: {name} ({len(df)} rows, {df.shape[1]} columns, sha256={sha[:12]})", "ingest")
    return did


def new_run(dataset_id: int | None, targets: list[str], config: dict) -> int:
    with session_scope() as s:
        r = M.Run(dataset_id=dataset_id, targets_json=json.dumps(targets), config_json=json.dumps(config, default=str))
        s.add(r)
        s.flush()
        rid = r.id
    audit(f"Analysis run {rid} started for targets {targets}", "run", run_id=rid, detail={"config": config})
    return rid


def artifact_dir(run_id: int, target: str) -> Path:
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in target)
    p = ensure_dirs()["models"] / f"run_{run_id}" / safe
    p.mkdir(parents=True, exist_ok=True)
    return p


def artifact_path(run_id: int, target: str, model: str) -> Path:
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in model)
    return artifact_dir(run_id, target) / f"{safe}.joblib"


def save_run(run_id: int, run: RunResult, bundles: dict) -> str:
    run.run_id = run_id
    for t, bs in bundles.items():
        for name, b in bs.items():
            b["run_id"] = run_id
            joblib.dump(b, artifact_path(run_id, t, name))
    rdir = ensure_dirs()["runs"] / f"run_{run_id}"
    rdir.mkdir(parents=True, exist_ok=True)
    path = rdir / "results.joblib"
    slim = RunResult(**{**run.__dict__})
    joblib.dump(slim, path)
    with session_scope() as s:
        r = s.get(M.Run, run_id)
        r.finished_at = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
        r.status = "cancelled" if run.cancelled else "completed"
        r.seed = run.seed
        r.versions_json = json.dumps(run.versions)
        r.results_path = str(path)
        r.summary_json = json.dumps({t: {"status": tr.status, "error": tr.error, "n_obs": tr.n_obs, "granularity": tr.granularity,
                                         "best": (tr.shortlist[0]["name"] if tr.shortlist else None)} for t, tr in run.targets.items()})
        for a in run.auto:
            s.add(M.AutoResolution(run_id=run_id, target=a["target"], setting=a["setting"], mode=a["mode"], value=a["value"], signals=a["signals"], rule=a["rule"]))
        for t, tr in run.targets.items():
            for row in tr.leaderboard:
                s.add(M.ModelResult(run_id=run_id, target=t, model_name=row["model"], family=row["family"], rank=row.get("rank"), score=row.get("score"),
                                    status=row.get("status", ""), reason=row.get("reason", ""), metrics_json=json.dumps({k: row[k] for k in ("r2", "cvrmse", "nmbe", "mae", "mape", "smape") if k in row}),
                                    formula=next((sl["formula"]["text"] for sl in tr.shortlist if sl["name"] == row["model"]), ""),
                                    artifact_path=str(artifact_path(run_id, t, row["model"])) if row["model"] in bundles.get(t, {}) else ""))
    audit(f"Analysis run {run_id} {'cancelled' if run.cancelled else 'completed'}", "run", run_id=run_id)
    return str(path)


def load_run(run_id: int) -> RunResult:
    with session_scope() as s:
        r = s.get(M.Run, run_id)
        if r is None or not r.results_path:
            raise FileNotFoundError(f"Run {run_id} has no stored results")
        path = r.results_path
    run = joblib.load(path)
    run.run_id = run_id
    return run


def list_runs() -> list[dict]:
    with session_scope() as s:
        rows = s.execute(select(M.Run).order_by(M.Run.id.desc())).scalars().all()
        return [{"id": r.id, "started": str(r.started_at), "status": r.status, "targets": json.loads(r.targets_json or "[]")} for r in rows]


def lock_baseline(run_id: int, target: str, model_name: str, note: str = "") -> dict:
    """Lock (or change) the baseline selection; previous selections are retained as history."""
    with session_scope() as s:
        prev = s.execute(select(M.Selection).where(M.Selection.run_id == run_id, M.Selection.target == target, M.Selection.active.is_(True))).scalars().all()
        for p in prev:
            p.active = False
        sel = M.Selection(run_id=run_id, target=target, model_name=model_name, note=note, active=True)
        s.add(sel)
        s.flush()
        out = {"id": sel.id, "run_id": run_id, "target": target, "model": model_name, "note": note, "at": str(sel.selected_at),
               "replaced": [p.model_name for p in prev]}
    audit(f"Baseline selected for '{target}': {model_name}" + (f" (replaces {out['replaced']})" if out["replaced"] else "") + (f" - note: {note}" if note else ""),
          "selection", run_id=run_id, detail=out)
    return out


def selection_history(run_id: int | None = None) -> list[dict]:
    with session_scope() as s:
        q = select(M.Selection).order_by(M.Selection.id)
        if run_id is not None:
            q = q.where(M.Selection.run_id == run_id)
        return [{"id": r.id, "run_id": r.run_id, "target": r.target, "model": r.model_name, "note": r.note, "at": str(r.selected_at), "active": bool(r.active)} for r in s.execute(q).scalars()]


def active_selection(run_id: int, target: str) -> dict | None:
    for h in reversed(selection_history(run_id)):
        if h["target"] == target and h["active"]:
            return h
    return None


def load_bundle(run_id: int, target: str, model: str) -> dict:
    return joblib.load(artifact_path(run_id, target, model))


def audit_entries(run_id: int | None = None, limit: int = 2000) -> list[dict]:
    with session_scope() as s:
        q = select(M.AuditLog).order_by(M.AuditLog.id.desc()).limit(limit)
        if run_id is not None:
            q = q.where((M.AuditLog.run_id == run_id) | (M.AuditLog.run_id.is_(None)))
        return [{"ts": str(r.ts), "level": r.level, "category": r.category, "message": r.message} for r in reversed(list(s.execute(q).scalars()))]


def save_snapshot(run_id: int | None, target: str, name: str, df: pd.DataFrame, kpis: dict) -> int:
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb") as gz:
        gz.write(df.to_csv().encode("utf-8"))
    with session_scope() as s:
        sn = M.Snapshot(run_id=run_id, target=target, name=name, kpis_json=json.dumps(kpis, default=str), payload=buf.getvalue())
        s.add(sn)
        s.flush()
        sid = sn.id
    audit(f"Monitoring snapshot saved: {name} ({target}, {len(df)} rows)", "monitoring", run_id=run_id)
    return sid


def list_snapshots() -> list[dict]:
    with session_scope() as s:
        return [{"id": r.id, "name": r.name, "target": r.target, "created": str(r.created_at), "kpis": json.loads(r.kpis_json or "{}")} for r in s.execute(select(M.Snapshot).order_by(M.Snapshot.id.desc())).scalars()]


def load_snapshot(snapshot_id: int) -> pd.DataFrame:
    with session_scope() as s:
        sn = s.get(M.Snapshot, snapshot_id)
        raw = gzip.decompress(sn.payload).decode("utf-8")
    return pd.read_csv(io.StringIO(raw), index_col=0, parse_dates=True)


# ------------------------------------------------------------------ Phase 2 dataset schema & NRE persistence (DAT-2, DAT-5, DAT-8)
def save_dataset_schema(
    schema: DatasetSchema,
    *,
    dataset_id: int | None = None,
    name: str | None = None,
    ingestion_summary: dict | None = None,
) -> int:
    """Validate and persist ``DatasetSchema`` + SHA-256 + Non-Routine Events to SQLite/DB."""
    if not isinstance(schema, DatasetSchema):
        raise SchemaValidationError("save_dataset_schema expects a DatasetSchema instance.")
    schema.validate()
    payload = schema.canonical_payload()
    canonical_json = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    sha = schema.compute_sha256()
    summary_json = json.dumps(ingestion_summary or {}, sort_keys=True, default=str)
    now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    eff_name = (name if name is not None else schema.dataset_name) or "dataset_schema"

    with session_scope() as s:
        rec = M.DatasetSchemaRecord(
            dataset_id=dataset_id,
            name=eff_name,
            schema_version=schema.schema_version,
            dataset_sha256=schema.dataset_sha256,
            schema_sha256=sha,
            time_column=schema.time_column,
            targets_json=json.dumps(schema.target_columns),
            outlier_policy=schema.outlier_policy.value,
            schema_json=canonical_json,
            ingestion_summary_json=summary_json,
            created_at=now,
            updated_at=now,
        )
        s.add(rec)
        s.flush()
        sid = rec.id
        for ev in schema.non_routine_events:
            s.add(
                M.NonRoutineEvent(
                    dataset_id=dataset_id,
                    schema_id=sid,
                    name=ev.name,
                    start_ts=ev.start,
                    end_ts=ev.end,
                    action=ev.action,
                    affected_targets_json=json.dumps(list(ev.affected_targets)),
                    reason=ev.reason,
                    created_at=now,
                )
            )
    audit(
        f"Dataset schema #{sid} saved for '{eff_name}' (sha256={sha[:12]}, targets={schema.target_columns})",
        "schema",
        detail={"schema_id": sid, "dataset_id": dataset_id, "schema_sha256": sha},
    )
    return sid


def load_dataset_schema(
    schema_id: int | None = None,
    *,
    dataset_id: int | None = None,
    verify_sha256: bool = True,
) -> DatasetSchema:
    """Load ``DatasetSchema`` by ``schema_id`` or latest ``dataset_id`` and verify SHA-256 (DAT-5, DAT-8)."""
    if schema_id is None and dataset_id is None:
        raise ValueError("Specify either schema_id or dataset_id to load a DatasetSchema.")

    with session_scope() as s:
        if schema_id is not None:
            rec = s.get(M.DatasetSchemaRecord, schema_id)
        else:
            rec = (
                s.execute(
                    select(M.DatasetSchemaRecord)
                    .where(M.DatasetSchemaRecord.dataset_id == dataset_id)
                    .order_by(M.DatasetSchemaRecord.id.desc())
                    .limit(1)
                )
                .scalars()
                .first()
            )
        if rec is None:
            raise FileNotFoundError(
                f"No persisted DatasetSchema found (schema_id={schema_id}, dataset_id={dataset_id})."
            )
        raw_json = rec.schema_json or ""
        stored_sha = (rec.schema_sha256 or "").strip().lower()
        nre_rows = (
            s.execute(
                select(M.NonRoutineEvent)
                .where(M.NonRoutineEvent.schema_id == rec.id)
                .order_by(M.NonRoutineEvent.id)
            )
            .scalars()
            .all()
        )
        nre_dicts = [
            {
                "name": r.name,
                "start": r.start_ts,
                "end": r.end_ts,
                "action": r.action,
                "affected_targets": json.loads(r.affected_targets_json or "[]"),
                "reason": r.reason,
            }
            for r in nre_rows
        ]

    if verify_sha256:
        import hashlib

        if not stored_sha:
            raise SchemaIntegrityError("Persisted DatasetSchema row is missing schema_sha256.")
        actual_sha = hashlib.sha256(raw_json.encode("utf-8")).hexdigest()
        if actual_sha != stored_sha:
            raise SchemaIntegrityError(
                f"Persisted DatasetSchema SHA-256 mismatch: expected {stored_sha}, computed {actual_sha}."
            )

    try:
        parsed = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        raise SchemaIntegrityError(f"Persisted DatasetSchema JSON is corrupt: {exc}") from exc

    schema = DatasetSchema.from_dict(parsed)
    if nre_dicts and not schema.non_routine_events:
        schema = DatasetSchema(
            columns=schema.columns,
            dataset_name=schema.dataset_name,
            dataset_sha256=schema.dataset_sha256,
            timezone=schema.timezone,
            storage_timezone=schema.storage_timezone,
            outlier_policy=schema.outlier_policy,
            non_routine_events=[NonRoutineEventSpec.from_dict(d) for d in nre_dicts],
            schema_version=schema.schema_version,
        )
    if verify_sha256 and schema.compute_sha256() != stored_sha:
        raise SchemaIntegrityError(
            f"Persisted DatasetSchema canonical SHA-256 mismatch: expected {stored_sha}, got {schema.compute_sha256()}."
        )
    return schema


def list_dataset_schemas(dataset_id: int | None = None) -> list[dict]:
    with session_scope() as s:
        q = select(M.DatasetSchemaRecord).order_by(M.DatasetSchemaRecord.id.desc())
        if dataset_id is not None:
            q = q.where(M.DatasetSchemaRecord.dataset_id == dataset_id)
        return [
            {
                "id": r.id,
                "dataset_id": r.dataset_id,
                "name": r.name,
                "schema_version": r.schema_version,
                "dataset_sha256": r.dataset_sha256,
                "schema_sha256": r.schema_sha256,
                "time_column": r.time_column,
                "targets": json.loads(r.targets_json or "[]"),
                "outlier_policy": r.outlier_policy,
                "created_at": str(r.created_at),
            }
            for r in s.execute(q).scalars()
        ]


def load_ingestion_summary(schema_id: int | None = None, *, dataset_id: int | None = None) -> dict:
    with session_scope() as s:
        if schema_id is not None:
            rec = s.get(M.DatasetSchemaRecord, schema_id)
        else:
            rec = (
                s.execute(
                    select(M.DatasetSchemaRecord)
                    .where(M.DatasetSchemaRecord.dataset_id == dataset_id)
                    .order_by(M.DatasetSchemaRecord.id.desc())
                    .limit(1)
                )
                .scalars()
                .first()
            )
        if rec is None:
            raise FileNotFoundError(
                f"No persisted DatasetSchema summary found (schema_id={schema_id}, dataset_id={dataset_id})."
            )
        return json.loads(rec.ingestion_summary_json or "{}")


def load_non_routine_events(
    *,
    dataset_id: int | None = None,
    schema_id: int | None = None,
) -> list[NonRoutineEventSpec]:
    with session_scope() as s:
        q = select(M.NonRoutineEvent).order_by(M.NonRoutineEvent.id)
        if schema_id is not None:
            q = q.where(M.NonRoutineEvent.schema_id == schema_id)
        elif dataset_id is not None:
            q = q.where(M.NonRoutineEvent.dataset_id == dataset_id)
        return [
            NonRoutineEventSpec(
                name=r.name,
                start=r.start_ts,
                end=r.end_ts,
                action=r.action,
                affected_targets=tuple(json.loads(r.affected_targets_json or "[]")),
                reason=r.reason,
            )
            for r in s.execute(q).scalars()
        ]


# ===================================================================== Phase 3 Persistence (VSG, TCP, Provenance, Cache)


def save_vsg_state(
    vsg_dict: dict,
    *,
    dataset_id: int | None = None,
) -> int:
    """Persist serialized VariableSelectionGate JSON with SHA-256 tamper protection (VSG-1)."""
    import hashlib

    canonical = json.dumps(vsg_dict, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str)
    payload_sha = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    vsg_sha = str(vsg_dict.get("vsg_sha256", "") or payload_sha)
    auto_sha = str(vsg_dict.get("auto_log_hash", "") or "")
    ds_sha = str(vsg_dict.get("dataset_sha256", "") or "")
    targets = list(vsg_dict.get("targets", []))
    accepted_map = vsg_dict.get("accepted_by_target") or {}
    accepted = bool(targets) and all(bool(accepted_map.get(t)) for t in targets)

    with session_scope() as s:
        rec = M.VSGStateRecord(
            dataset_id=dataset_id,
            dataset_sha256=ds_sha,
            vsg_sha256=vsg_sha,
            auto_log_hash=auto_sha,
            accepted=accepted,
            targets_json=json.dumps(targets),
            state_json=canonical,
            payload_sha256=payload_sha,
        )
        s.add(rec)
        s.flush()
        vid = rec.id
    audit(f"VSG state #{vid} saved (accepted={accepted}, vsg_sha256={vsg_sha[:12]})", "vsg", detail={"vsg_id": vid})
    return vid


def load_vsg_state(vsg_id: int | None = None, *, dataset_id: int | None = None, verify_sha256: bool = True) -> dict:
    """Load persisted VSG JSON and verify SHA-256 integrity."""
    import hashlib

    with session_scope() as s:
        if vsg_id is not None:
            rec = s.get(M.VSGStateRecord, vsg_id)
        else:
            rec = (
                s.execute(
                    select(M.VSGStateRecord)
                    .where(M.VSGStateRecord.dataset_id == dataset_id)
                    .order_by(M.VSGStateRecord.id.desc())
                    .limit(1)
                )
                .scalars()
                .first()
            )
        if rec is None:
            raise FileNotFoundError(f"No persisted VSG state found (vsg_id={vsg_id}, dataset_id={dataset_id}).")
        raw_json = rec.state_json or ""
        expected_sha = (rec.payload_sha256 or "").strip().lower()

    if verify_sha256:
        actual_sha = hashlib.sha256(raw_json.encode("utf-8")).hexdigest()
        if not expected_sha or actual_sha != expected_sha:
            raise SchemaIntegrityError(
                f"Persisted VSG state SHA-256 mismatch: expected {expected_sha}, computed {actual_sha}."
            )
    return json.loads(raw_json)


def save_tcp_config(
    tcp_dict: dict,
    *,
    dataset_id: int | None = None,
    vsg_id: int | None = None,
    characterization: dict | None = None,
) -> int:
    """Persist serialized TestConfigPanel JSON + optional 14-D characterization with SHA-256 guard (TCP-5, TCP-14)."""
    import hashlib

    canonical = json.dumps(tcp_dict, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str)
    payload_sha = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    char_json = json.dumps(characterization or {}, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str)
    tcp_sha = str(tcp_dict.get("tcp_sha256", "") or payload_sha)
    prereg_sha = str(tcp_dict.get("preregistration_sha256", "") or "")
    accepted = bool(tcp_dict.get("accepted", False))
    locked = bool(tcp_dict.get("locked", False))

    with session_scope() as s:
        rec = M.TCPConfigRecord(
            dataset_id=dataset_id,
            vsg_id=vsg_id,
            tcp_sha256=tcp_sha,
            preregistration_sha256=prereg_sha,
            accepted=accepted,
            locked=locked,
            config_json=canonical,
            characterization_json=char_json,
            payload_sha256=payload_sha,
        )
        s.add(rec)
        s.flush()
        tid = rec.id
    audit(
        f"TCP config #{tid} saved (accepted={accepted}, locked={locked}, prereg={prereg_sha[:12] if prereg_sha else 'none'})",
        "tcp",
        detail={"tcp_id": tid},
    )
    return tid


def load_tcp_config(tcp_id: int | None = None, *, dataset_id: int | None = None, verify_sha256: bool = True) -> dict:
    """Load persisted TCP configuration JSON and verify SHA-256 integrity."""
    import hashlib

    with session_scope() as s:
        if tcp_id is not None:
            rec = s.get(M.TCPConfigRecord, tcp_id)
        else:
            rec = (
                s.execute(
                    select(M.TCPConfigRecord)
                    .where(M.TCPConfigRecord.dataset_id == dataset_id)
                    .order_by(M.TCPConfigRecord.id.desc())
                    .limit(1)
                )
                .scalars()
                .first()
            )
        if rec is None:
            raise FileNotFoundError(f"No persisted TCP config found (tcp_id={tcp_id}, dataset_id={dataset_id}).")
        raw_json = rec.config_json or ""
        char_json = rec.characterization_json or "{}"
        expected_sha = (rec.payload_sha256 or "").strip().lower()

    if verify_sha256:
        actual_sha = hashlib.sha256(raw_json.encode("utf-8")).hexdigest()
        if not expected_sha or actual_sha != expected_sha:
            raise SchemaIntegrityError(
                f"Persisted TCP config SHA-256 mismatch: expected {expected_sha}, computed {actual_sha}."
            )
    out = json.loads(raw_json)
    out["characterization"] = json.loads(char_json)
    return out


def save_provenance_ledger(
    ledger_dict: dict,
    *,
    dataset_id: int | None = None,
    run_id: int | None = None,
) -> list[int]:
    """Persist ProvenanceLedger records, enforcing phase in {'pre_test', 'post_test'} (VSG-6, PIPE-2)."""
    from .provenance import ProvenanceLedger

    verified = ProvenanceLedger.from_dict(ledger_dict)
    ids: list[int] = []
    with session_scope() as s:
        for rec in verified.records:
            payload = json.dumps(rec.to_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str)
            row = M.ProvenanceEntryRecord(
                dataset_id=dataset_id,
                run_id=run_id,
                seq=rec.seq,
                entity_type=rec.entity_type,
                entity_id=rec.entity_id,
                target=rec.target,
                source=rec.source,
                phase=rec.phase,
                dataset_sha256=rec.dataset_sha256,
                previous_record_sha256=rec.previous_record_sha256,
                record_sha256=rec.record_sha256,
                payload_json=payload,
            )
            s.add(row)
            s.flush()
            ids.append(row.id)
    return ids


def load_provenance_records(*, dataset_id: int | None = None, run_id: int | None = None) -> list[dict]:
    """Load persisted provenance records in sequential hash-chain order."""
    with session_scope() as s:
        q = select(M.ProvenanceEntryRecord).order_by(M.ProvenanceEntryRecord.id)
        if dataset_id is not None:
            q = q.where(M.ProvenanceEntryRecord.dataset_id == dataset_id)
        if run_id is not None:
            q = q.where(M.ProvenanceEntryRecord.run_id == run_id)
        return [json.loads(r.payload_json) for r in s.execute(q).scalars()]


def save_cache_artifact(key_dict: dict, key_sha256: str, artifact_dict: dict) -> int:
    """Persist deterministic JSON test cache entry keyed by ``cache_key_sha256`` (CACHE-1)."""
    import hashlib

    art_json = json.dumps(artifact_dict, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str)
    art_sha = hashlib.sha256(art_json.encode("utf-8")).hexdigest()
    with session_scope() as s:
        existing = (
            s.execute(select(M.ResultCacheRecord).where(M.ResultCacheRecord.cache_key_sha256 == key_sha256))
            .scalars()
            .first()
        )
        if existing is not None:
            existing.artifact_json = art_json
            existing.artifact_sha256 = art_sha
            s.flush()
            return existing.id
        rec = M.ResultCacheRecord(
            cache_key_sha256=key_sha256,
            data_hash=str(key_dict.get("data_hash", "")),
            variable_set_json=json.dumps(list(key_dict.get("variable_set", []))),
            test_id=str(key_dict.get("test_id", "")),
            parameters_json=json.dumps(key_dict.get("parameters", {}), sort_keys=True),
            seed=int(key_dict.get("seed", 0)),
            library_versions_json=json.dumps(key_dict.get("library_versions", {}), sort_keys=True),
            artifact_json=art_json,
            artifact_sha256=art_sha,
        )
        s.add(rec)
        s.flush()
        return rec.id


def load_cache_artifact(key_sha256: str, *, verify_sha256: bool = True) -> dict | None:
    """Load persisted JSON test cache entry by ``key_sha256``, verifying SHA-256 integrity."""
    import hashlib

    with session_scope() as s:
        rec = (
            s.execute(select(M.ResultCacheRecord).where(M.ResultCacheRecord.cache_key_sha256 == key_sha256))
            .scalars()
            .first()
        )
        if rec is None:
            return None
        raw = rec.artifact_json or ""
        expected = (rec.artifact_sha256 or "").strip().lower()
    if verify_sha256:
        actual = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        if not expected or actual != expected:
            raise SchemaIntegrityError(f"Cache entry SHA-256 mismatch for key {key_sha256[:12]}.")
    return json.loads(raw)


