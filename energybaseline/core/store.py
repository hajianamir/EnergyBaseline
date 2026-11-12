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
from sqlalchemy import select, update

from ..config import ensure_dirs
from ..db import models as M
from ..db.session import session_scope
from .pipeline import RunResult

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
