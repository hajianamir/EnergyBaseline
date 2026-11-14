"""Immutable Versioned Baseline Lock, SHA-256 Verified Joblib Store, ISO 50006 Model Card & Excel Formula Round-Trip (SC-10, AC-04, G-9, DOM-1).

Enforces strict Phase 6 Gate G6 invariants:
- Unsafe Python pickle deserialization is strictly forbidden: artifacts are persisted via ``joblib.dump``
  and cryptographically verified against their recorded ``artifact_sha256`` BEFORE ``joblib.load`` runs.
- Once locked, a ``LockedBaselineRecord`` and its on-disk ``v<N>`` manifest/artifact are strictly
  immutable: attribute assignment, nested dict mutation, or in-place version overwrite raises
  ``BaselineImmutabilityError``.
- Re-locking a new choice creates ``version = previous_max + 1`` while preserving every prior version
  intact in the append-only audit ledger.
- Parametric baselines export live Excel cell formulas (``=...``) verified via round-trip formula parsing.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from types import MappingProxyType
from typing import Any, Mapping, Sequence
import uuid

import joblib
import numpy as np
import openpyxl
import pandas as pd


class BaselineImmutabilityError(TypeError):
    """Raised on any attempt to mutate a locked baseline record or overwrite an existing locked version (SC-10, AC-04)."""


class ArtifactIntegrityError(ValueError):
    """Raised when a serialized model artifact fails SHA-256 verification prior to load (G-9)."""


def _freeze_value(val: Any) -> Any:
    """Recursively freeze dicts into ``MappingProxyType`` and lists into ``tuple`` so locked state cannot be mutated."""
    if isinstance(val, MappingProxyType):
        return val
    if isinstance(val, Mapping):
        return MappingProxyType({str(k): _freeze_value(v) for k, v in val.items()})
    if isinstance(val, (list, tuple)):
        return tuple(_freeze_value(x) for x in val)
    return val


def _thaw_value(val: Any) -> Any:
    """Convert frozen ``MappingProxyType`` / ``tuple`` trees back into JSON-serializable Python structures."""
    if isinstance(val, Mapping):
        return {str(k): _thaw_value(v) for k, v in val.items()}
    if isinstance(val, tuple):
        return [_thaw_value(x) for x in val]
    if isinstance(val, np.generic):
        return val.item()
    return copy.deepcopy(val)


def compute_file_sha256(path: str | Path) -> str:
    """Compute hex SHA-256 digest of a file on disk in 64 KiB chunks."""
    p = Path(path)
    h = hashlib.sha256()
    with p.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


@dataclass(frozen=True)
class ExcelFormulaRoundTripResult:
    """Audit result of verifying live Excel cell formulas against Python model predictions."""

    workbook_path: str
    n_rows_verified: int
    all_cells_are_formulas: bool
    sample_formula: str
    max_abs_prediction_diff: float
    python_rmse: float
    excel_formula_rmse: float
    rmse_abs_diff: float
    passed: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "all_cells_are_formulas": bool(self.all_cells_are_formulas),
            "excel_formula_rmse": float(self.excel_formula_rmse),
            "max_abs_prediction_diff": float(self.max_abs_prediction_diff),
            "n_rows_verified": int(self.n_rows_verified),
            "passed": bool(self.passed),
            "python_rmse": float(self.python_rmse),
            "rmse_abs_diff": float(self.rmse_abs_diff),
            "sample_formula": self.sample_formula,
            "workbook_path": self.workbook_path,
        }


class LockedBaselineRecord:
    """Strictly immutable, cryptographically hashed versioned baseline lock record (SC-10, AC-04, DOM-1)."""

    __slots__ = (
        "_locked_initialized",
        "lock_id",
        "run_id",
        "target",
        "version",
        "locked_at_utc",
        "locked_by",
        "model_name",
        "family",
        "is_parametric",
        "formula_or_architecture",
        "coefficients",
        "hyperparameters",
        "selected_features",
        "preprocessing_summary",
        "vsg_snapshot",
        "tcp_snapshot",
        "baseline_window",
        "thresholds",
        "random_seed",
        "calibration_metrics",
        "composite_score",
        "validity_envelope",
        "override_record",
        "iso50006_model_card",
        "artifact_path",
        "artifact_sha256",
        "manifest_path",
        "previous_lock_sha256",
        "record_sha256",
    )

    def __init__(
        self,
        *,
        lock_id: str,
        run_id: str,
        target: str,
        version: int,
        locked_at_utc: str,
        locked_by: str,
        model_name: str,
        family: str,
        is_parametric: bool,
        formula_or_architecture: str,
        coefficients: Mapping[str, float],
        hyperparameters: Mapping[str, Any],
        selected_features: Sequence[str],
        preprocessing_summary: Mapping[str, Any],
        vsg_snapshot: Mapping[str, Any],
        tcp_snapshot: Mapping[str, Any],
        baseline_window: Mapping[str, Any],
        thresholds: Mapping[str, float],
        random_seed: int,
        calibration_metrics: Mapping[str, Any],
        composite_score: float,
        validity_envelope: Mapping[str, Any],
        override_record: Mapping[str, Any] | None,
        iso50006_model_card: Mapping[str, Any],
        artifact_path: str,
        artifact_sha256: str,
        manifest_path: str,
        previous_lock_sha256: str | None = None,
        record_sha256: str | None = None,
    ) -> None:
        object.__setattr__(self, "_locked_initialized", False)
        object.__setattr__(self, "lock_id", str(lock_id))
        object.__setattr__(self, "run_id", str(run_id))
        object.__setattr__(self, "target", str(target))
        object.__setattr__(self, "version", int(version))
        object.__setattr__(self, "locked_at_utc", str(locked_at_utc))
        object.__setattr__(self, "locked_by", str(locked_by))
        object.__setattr__(self, "model_name", str(model_name))
        object.__setattr__(self, "family", str(family))
        object.__setattr__(self, "is_parametric", bool(is_parametric))
        object.__setattr__(self, "formula_or_architecture", str(formula_or_architecture))
        object.__setattr__(self, "coefficients", _freeze_value(dict(coefficients)))
        object.__setattr__(self, "hyperparameters", _freeze_value(dict(hyperparameters)))
        object.__setattr__(self, "selected_features", tuple(str(f) for f in selected_features))
        object.__setattr__(self, "preprocessing_summary", _freeze_value(dict(preprocessing_summary)))
        object.__setattr__(self, "vsg_snapshot", _freeze_value(dict(vsg_snapshot)))
        object.__setattr__(self, "tcp_snapshot", _freeze_value(dict(tcp_snapshot)))
        object.__setattr__(self, "baseline_window", _freeze_value(dict(baseline_window)))
        object.__setattr__(self, "thresholds", _freeze_value(dict(thresholds)))
        object.__setattr__(self, "random_seed", int(random_seed))
        object.__setattr__(self, "calibration_metrics", _freeze_value(dict(calibration_metrics)))
        object.__setattr__(self, "composite_score", float(composite_score))
        object.__setattr__(self, "validity_envelope", _freeze_value(dict(validity_envelope)))
        object.__setattr__(
            self,
            "override_record",
            _freeze_value(dict(override_record)) if override_record is not None else None,
        )
        object.__setattr__(self, "iso50006_model_card", _freeze_value(dict(iso50006_model_card)))
        object.__setattr__(self, "artifact_path", str(artifact_path))
        object.__setattr__(self, "artifact_sha256", str(artifact_sha256))
        object.__setattr__(self, "manifest_path", str(manifest_path))
        object.__setattr__(
            self,
            "previous_lock_sha256",
            str(previous_lock_sha256) if previous_lock_sha256 else None,
        )

        computed = record_sha256 or self._compute_canonical_sha256()
        object.__setattr__(self, "record_sha256", str(computed))
        object.__setattr__(self, "_locked_initialized", True)

    def __setattr__(self, name: str, value: Any) -> None:
        raise BaselineImmutabilityError(
            f"SC-10/AC-04 immutability violation: cannot modify attribute '{name}' on locked baseline "
            f"'{self.lock_id}' (version v{self.version}). Create a new version instead."
        )

    def __delattr__(self, name: str) -> None:
        raise BaselineImmutabilityError(
            f"SC-10/AC-04 immutability violation: cannot delete attribute '{name}' on locked baseline '{self.lock_id}'."
        )

    def _canonical_payload_without_record_hash(self) -> dict[str, Any]:
        return {
            "artifact_path": self.artifact_path,
            "artifact_sha256": self.artifact_sha256,
            "baseline_window": _thaw_value(self.baseline_window),
            "calibration_metrics": _thaw_value(self.calibration_metrics),
            "coefficients": _thaw_value(self.coefficients),
            "composite_score": round(float(self.composite_score), 8),
            "family": self.family,
            "formula_or_architecture": self.formula_or_architecture,
            "hyperparameters": _thaw_value(self.hyperparameters),
            "is_parametric": bool(self.is_parametric),
            "iso50006_model_card": _thaw_value(self.iso50006_model_card),
            "lock_id": self.lock_id,
            "locked_at_utc": self.locked_at_utc,
            "locked_by": self.locked_by,
            "manifest_path": self.manifest_path,
            "model_name": self.model_name,
            "override_record": _thaw_value(self.override_record) if self.override_record is not None else None,
            "preprocessing_summary": _thaw_value(self.preprocessing_summary),
            "previous_lock_sha256": self.previous_lock_sha256,
            "random_seed": int(self.random_seed),
            "run_id": self.run_id,
            "selected_features": list(self.selected_features),
            "target": self.target,
            "tcp_snapshot": _thaw_value(self.tcp_snapshot),
            "thresholds": _thaw_value(self.thresholds),
            "validity_envelope": _thaw_value(self.validity_envelope),
            "version": int(self.version),
            "vsg_snapshot": _thaw_value(self.vsg_snapshot),
        }

    def _compute_canonical_sha256(self) -> str:
        raw = json.dumps(self._canonical_payload_without_record_hash(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def verify_integrity(self) -> bool:
        """Verify both the manifest canonical SHA-256 and the on-disk joblib artifact SHA-256."""
        expected_rec = self._compute_canonical_sha256()
        if expected_rec != self.record_sha256:
            raise ArtifactIntegrityError(
                f"LockedBaselineRecord tamper detected: record_sha256 mismatch ({expected_rec} != {self.record_sha256})."
            )
        art = Path(self.artifact_path)
        if not art.is_file():
            raise ArtifactIntegrityError(f"Locked baseline artifact missing on disk: {art}")
        actual_art = compute_file_sha256(art)
        if actual_art != self.artifact_sha256:
            raise ArtifactIntegrityError(
                f"G-9 SHA-256 verification FAILED for '{art}': expected {self.artifact_sha256}, got {actual_art}."
            )
        return True

    def to_dict(self) -> dict[str, Any]:
        payload = self._canonical_payload_without_record_hash()
        payload["record_sha256"] = self.record_sha256
        return payload


def build_iso50006_model_card(
    *,
    target: str,
    model_name: str,
    family: str,
    is_parametric: bool,
    formula_or_architecture: str,
    selected_features: Sequence[str],
    baseline_window: Mapping[str, Any],
    thresholds: Mapping[str, float],
    calibration_metrics: Mapping[str, Any],
    validity_envelope: Mapping[str, Any],
    notes: str = "",
) -> dict[str, Any]:
    """Construct an ISO 50006:2014 / IPMVP Option C compliant Energy Baseline (EnB) Model Card (DOM-1)."""
    return {
        "adjustment_method": (
            "Closed-form parametric routine adjustment equation"
            if is_parametric
            else "Locked deterministic non-parametric estimator (verified joblib SHA-256)"
        ),
        "baseline_period": dict(baseline_window),
        "calibration_summary": dict(calibration_metrics),
        "compliance_thresholds": dict(thresholds),
        "energy_performance_indicator_enpi": f"Model-normalized {target} consumption & CUSUM savings residual",
        "estimation_method_and_family": f"{model_name} ({family})",
        "formula_or_architecture": str(formula_or_architecture),
        "governing_standards": [
            "ISO 50006:2014 (Energy baseline and EnPIs)",
            "ISO 50015:2014 (M&V of energy performance)",
            "ASHRAE Guideline 14-2014",
            "EVO IPMVP Core Concepts (Option C)",
        ],
        "notes": str(notes),
        "relevant_variables": list(selected_features),
        "static_factors_guidance": (
            "Facility floor area, primary HVAC/process equipment topology, and core operating shifts assumed "
            "invariant; non-routine adjustments required upon static-factor change."
        ),
        "validity_domain_summary": {
            "drivers_monitored": list(validity_envelope.get("drivers", [])),
            "n_training_obs": int(validity_envelope.get("n_training_obs", 0)),
        },
    }


class BaselineLockManager:
    """Append-only versioned baseline lock manager enforcing immutability and SHA-256 verified joblib loads."""

    def __init__(self, lock_dir: str | Path) -> None:
        self.lock_dir = Path(lock_dir)
        self.lock_dir.mkdir(parents=True, exist_ok=True)
        self._ledger_path = self.lock_dir / "audit_ledger.jsonl"
        self._records_by_key: dict[tuple[str, str], list[LockedBaselineRecord]] = {}
        self._reload_existing_manifests()

    def _reload_existing_manifests(self) -> None:
        for mf in sorted(self.lock_dir.glob("*.lock.json")):
            try:
                data = json.loads(mf.read_text(encoding="utf-8"))
                rec = LockedBaselineRecord(**data)
                key = (rec.run_id, rec.target)
                self._records_by_key.setdefault(key, []).append(rec)
            except Exception:
                continue
        for key in self._records_by_key:
            self._records_by_key[key].sort(key=lambda r: r.version)

    @staticmethod
    def _safe_slug(text: str) -> str:
        return re.sub(r"[^A-Za-z0-9_.-]+", "_", str(text)).strip("_") or "target"

    def lock_baseline(
        self,
        *,
        run_id: str,
        target: str,
        model_name: str,
        family: str,
        is_parametric: bool,
        fitted_artifact: Any,
        formula_or_architecture: str,
        coefficients: Mapping[str, float] | None = None,
        hyperparameters: Mapping[str, Any] | None = None,
        selected_features: Sequence[str] = (),
        preprocessing_summary: Mapping[str, Any] | None = None,
        vsg_snapshot: Mapping[str, Any] | None = None,
        tcp_snapshot: Mapping[str, Any] | None = None,
        baseline_window: Mapping[str, Any] | None = None,
        thresholds: Mapping[str, float] | None = None,
        random_seed: int = 42,
        calibration_metrics: Mapping[str, Any] | None = None,
        composite_score: float = 0.0,
        validity_envelope: Mapping[str, Any] | None = None,
        override_record: Mapping[str, Any] | None = None,
        locked_by: str = "engineer",
        explicit_version: int | None = None,
        notes: str = "",
    ) -> LockedBaselineRecord:
        """Create a new immutable baseline version ``v<N>`` with joblib SHA-256 verification and audit log."""
        key = (str(run_id), str(target))
        history = self._records_by_key.get(key, [])
        if explicit_version is not None:
            if any(r.version == int(explicit_version) for r in history):
                raise BaselineImmutabilityError(
                    f"SC-10/AC-04 immutability violation: locked baseline version v{explicit_version} for "
                    f"({run_id}, {target}) already exists and CANNOT be overwritten in-place."
                )
            ver = int(explicit_version)
        else:
            ver = (history[-1].version + 1) if history else 1

        prev_sha = history[-1].record_sha256 if history else None
        slug = f"{self._safe_slug(run_id)}__{self._safe_slug(target)}__v{ver:03d}"
        art_path = self.lock_dir / f"{slug}.joblib"
        mf_path = self.lock_dir / f"{slug}.lock.json"
        if art_path.exists() or mf_path.exists():
            raise BaselineImmutabilityError(
                f"SC-10/AC-04 immutability violation: lock files for '{slug}' already exist on disk."
            )

        # Persist strictly via joblib.dump (zero pickle module usage, G-9)
        joblib.dump(fitted_artifact, art_path, compress=3)
        art_sha = compute_file_sha256(art_path)
        (self.lock_dir / f"{slug}.joblib.sha256").write_text(f"{art_sha}  {art_path.name}\n", encoding="utf-8")

        win = dict(baseline_window or {})
        thr = {str(k): float(v) for k, v in (thresholds or {"max_cvrmse": 15.0, "max_nmbe": 5.0, "min_r2": 0.75}).items()}
        cal = dict(calibration_metrics or {})
        env = dict(validity_envelope or {})
        card = build_iso50006_model_card(
            target=str(target),
            model_name=str(model_name),
            family=str(family),
            is_parametric=bool(is_parametric),
            formula_or_architecture=str(formula_or_architecture),
            selected_features=selected_features,
            baseline_window=win,
            thresholds=thr,
            calibration_metrics=cal,
            validity_envelope=env,
            notes=notes,
        )

        rec = LockedBaselineRecord(
            lock_id=f"lock-{uuid.uuid4().hex[:12]}",
            run_id=str(run_id),
            target=str(target),
            version=ver,
            locked_at_utc=datetime.now(timezone.utc).isoformat(),
            locked_by=str(locked_by),
            model_name=str(model_name),
            family=str(family),
            is_parametric=bool(is_parametric),
            formula_or_architecture=str(formula_or_architecture),
            coefficients=dict(coefficients or {}),
            hyperparameters=dict(hyperparameters or {}),
            selected_features=selected_features,
            preprocessing_summary=dict(preprocessing_summary or {}),
            vsg_snapshot=dict(vsg_snapshot or {}),
            tcp_snapshot=dict(tcp_snapshot or {}),
            baseline_window=win,
            thresholds=thr,
            random_seed=int(random_seed),
            calibration_metrics=cal,
            composite_score=float(composite_score),
            validity_envelope=env,
            override_record=override_record,
            iso50006_model_card=card,
            artifact_path=str(art_path),
            artifact_sha256=art_sha,
            manifest_path=str(mf_path),
            previous_lock_sha256=prev_sha,
        )
        mf_path.write_text(json.dumps(rec.to_dict(), indent=2, sort_keys=True), encoding="utf-8")

        audit_event = {
            "action": "BASELINE_LOCKED",
            "artifact_sha256": rec.artifact_sha256,
            "lock_id": rec.lock_id,
            "locked_at_utc": rec.locked_at_utc,
            "locked_by": rec.locked_by,
            "model_name": rec.model_name,
            "override_applied": rec.override_record is not None,
            "previous_lock_sha256": rec.previous_lock_sha256,
            "record_sha256": rec.record_sha256,
            "run_id": rec.run_id,
            "target": rec.target,
            "version": rec.version,
        }
        with self._ledger_path.open("a", encoding="utf-8") as lf:
            lf.write(json.dumps(audit_event, sort_keys=True) + "\n")

        self._records_by_key.setdefault(key, []).append(rec)
        return rec

    def load_verified_artifact(self, record: LockedBaselineRecord) -> Any:
        """Verify SHA-256 of both manifest and joblib file BEFORE deserializing with ``joblib.load`` (G-9)."""
        record.verify_integrity()
        return joblib.load(record.artifact_path)

    def update_locked_baseline(self, *args: Any, **kwargs: Any) -> None:
        """Explicitly forbidden in-place mutation entry point — always raises ``BaselineImmutabilityError``."""
        raise BaselineImmutabilityError(
            "SC-10/AC-04 immutability violation: locked baselines cannot be updated in-place. "
            "Call lock_baseline() to append a new immutable version."
        )

    def get_history(self, run_id: str, target: str) -> tuple[LockedBaselineRecord, ...]:
        """Return immutable version history ``(v1, v2, ...)`` for ``(run_id, target)``."""
        return tuple(self._records_by_key.get((str(run_id), str(target)), ()))

    def get_active_lock(self, run_id: str, target: str) -> LockedBaselineRecord | None:
        """Return latest immutable version for ``(run_id, target)``, or ``None`` if unlocked."""
        hist = self.get_history(run_id, target)
        return hist[-1] if hist else None

    def read_audit_ledger(self) -> list[dict[str, Any]]:
        """Read append-only baseline lock audit ledger events."""
        if not self._ledger_path.is_file():
            return []
        return [
            json.loads(line)
            for line in self._ledger_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]


def _excel_col_letter(col_idx_1based: int) -> str:
    """Convert 1-based column index (1 -> 'A', 27 -> 'AA') to Excel column letters."""
    idx = int(col_idx_1based)
    letters: list[str] = []
    while idx > 0:
        idx, rem = divmod(idx - 1, 26)
        letters.append(chr(ord("A") + rem))
    return "".join(reversed(letters))


def export_locked_baseline_excel(
    record: LockedBaselineRecord,
    X_baseline: pd.DataFrame,
    y_actual: Any,
    output_path: str | Path,
) -> Path:
    """Export locked parametric baseline to an ``.xlsx`` workbook driven by live Excel formulas (``=...``).

    Sheets created:
    - ``ISO50006_Model_Card``: immutable metadata, hashes, window, thresholds
    - ``Coefficients``: intercept + per-driver coefficients referenced via absolute ``$B$<row>`` coordinates
    - ``Baseline_Formula_Calc``: timestamps, actual ``y``, driver columns, live Excel prediction formula
      ``=Coefficients!$B$2 + ...``, live residual formula ``=<Actual>-<Pred>``, and summary ``=SQRT(SUMSQ(...)/COUNT(...))``
    """
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    df = X_baseline.copy() if isinstance(X_baseline, pd.DataFrame) else pd.DataFrame(X_baseline)
    yv = np.asarray(y_actual, dtype=float).reshape(-1)
    if len(df) != yv.size:
        raise ValueError("X_baseline and y_actual must have matching row counts.")

    coefs = dict(record.coefficients)
    intercept = float(coefs.get("intercept", coefs.get("const", 0.0)))
    driver_cols = [c for c in record.selected_features if c in df.columns and c in coefs]
    if not driver_cols:
        driver_cols = [c for c in coefs if c not in ("intercept", "const") and c in df.columns]

    wb = openpyxl.Workbook()
    ws_card = wb.active
    assert ws_card is not None
    ws_card.title = "ISO50006_Model_Card"
    ws_card.append(["Field", "Value"])
    for k, v in [
        ("lock_id", record.lock_id),
        ("run_id", record.run_id),
        ("target", record.target),
        ("version", record.version),
        ("model_name", record.model_name),
        ("family", record.family),
        ("formula", record.formula_or_architecture),
        ("artifact_sha256", record.artifact_sha256),
        ("record_sha256", record.record_sha256),
        ("locked_at_utc", record.locked_at_utc),
    ]:
        ws_card.append([k, str(v)])

    ws_coef = wb.create_sheet("Coefficients")
    ws_coef.append(["Parameter", "Estimate"])
    ws_coef.append(["intercept", intercept])
    coef_row_map: dict[str, int] = {}
    for idx_d, col_name in enumerate(driver_cols, start=3):
        ws_coef.append([col_name, float(coefs[col_name])])
        coef_row_map[col_name] = idx_d

    ws_calc = wb.create_sheet("Baseline_Formula_Calc")
    headers = ["RowIndex", "Actual_Energy", *driver_cols, "Predicted_Baseline", "Residual"]
    ws_calc.append(headers)

    pred_col_idx = 3 + len(driver_cols)
    res_col_idx = pred_col_idx + 1
    pred_col_let = _excel_col_letter(pred_col_idx)
    res_col_let = _excel_col_letter(res_col_idx)

    X_clean = df[driver_cols].apply(pd.to_numeric, errors="coerce").fillna(0.0) if driver_cols else pd.DataFrame(index=df.index)
    for i in range(len(df)):
        excel_row = i + 2
        row_vals: list[Any] = [i + 1, float(yv[i])]
        terms = ["Coefficients!$B$2"]
        for d_pos, col_name in enumerate(driver_cols):
            drv_col_let = _excel_col_letter(3 + d_pos)
            row_vals.append(float(X_clean.iloc[i, d_pos]))
            c_row = coef_row_map[col_name]
            terms.append(f"({drv_col_let}{excel_row}*Coefficients!$B${c_row})")
        pred_formula = "=" + "+".join(terms)
        res_formula = f"=B{excel_row}-{pred_col_let}{excel_row}"
        row_vals.extend([pred_formula, res_formula])
        ws_calc.append(row_vals)

    last_data_row = len(df) + 1
    summary_row = last_data_row + 2
    ws_calc.cell(row=summary_row, column=1, value="Summary_RMSE")
    ws_calc.cell(
        row=summary_row,
        column=res_col_idx,
        value=f"=SQRT(SUMSQ({res_col_let}2:{res_col_let}{last_data_row})/COUNT({res_col_let}2:{res_col_let}{last_data_row}))",
    )

    wb.save(out)
    wb.close()
    return out


_CELL_REF_RE = re.compile(r"(?:(?P<sheet>[A-Za-z0-9_]+)!)?\$?(?P<col>[A-Z]+)\$?(?P<row>\d+)")
_FUNC_SUMSQ_RE = re.compile(
    r"^=SQRT\(SUMSQ\((?P<c1>[A-Z]+)(?P<r1>\d+):(?P<c2>[A-Z]+)(?P<r2>\d+)\)/COUNT\((?P=c1)(?P=r1):(?P=c2)(?P=r2)\)\)$"
)


def _col_letter_to_idx(letters: str) -> int:
    val = 0
    for ch in letters:
        val = val * 26 + (ord(ch) - ord("A") + 1)
    return val


def verify_excel_formula_roundtrip(
    workbook_path: str | Path,
    expected_predictions: Any,
    expected_actual: Any,
    *,
    tol: float = 1e-9,
) -> ExcelFormulaRoundTripResult:
    """Parse and evaluate the live Excel formula strings in ``Baseline_Formula_Calc`` and verify round-trip numerics."""
    wb_path = Path(workbook_path)
    wb = openpyxl.load_workbook(wb_path, data_only=False)
    try:
        ws_coef = wb["Coefficients"]
        ws_calc = wb["Baseline_Formula_Calc"]

        exp_pred = np.asarray(expected_predictions, dtype=float).reshape(-1)
        exp_act = np.asarray(expected_actual, dtype=float).reshape(-1)
        n = int(exp_pred.size)

        # Find Predicted_Baseline and Residual column indices from header row
        headers = [ws_calc.cell(row=1, column=c).value for c in range(1, ws_calc.max_column + 1)]
        pred_col = headers.index("Predicted_Baseline") + 1
        res_col = headers.index("Residual") + 1

        sheets_map = {"Coefficients": ws_coef, "Baseline_Formula_Calc": ws_calc}
        evaluated_cache: dict[tuple[str, int, int], float] = {}

        def _eval_cell(sheet_name: str, r: int, c: int) -> float:
            key = (sheet_name, r, c)
            if key in evaluated_cache:
                return evaluated_cache[key]
            raw = sheets_map[sheet_name].cell(row=r, column=c).value
            if isinstance(raw, (int, float)):
                val = float(raw)
                evaluated_cache[key] = val
                return val
            s = str(raw).strip()
            if not s.startswith("="):
                raise ValueError(f"Expected numeric or formula in {sheet_name}!R{r}C{c}, got {raw!r}.")
            expr = s[1:]

            def _repl(m: re.Match[str]) -> str:
                sh = m.group("sheet") or sheet_name
                cc = _col_letter_to_idx(m.group("col"))
                rr = int(m.group("row"))
                return repr(_eval_cell(sh, rr, cc))

            arith = _CELL_REF_RE.sub(_repl, expr)
            if not re.fullmatch(r"[0-9eE.+*\-/() ]+", arith):
                raise ValueError(f"Unsafe or unsupported formula token in {arith!r}")
            # Safe constant arithmetic parse via Python ast (strictly numeric literals + +-*/ unary ops)
            import ast

            tree = ast.parse(arith, mode="eval")

            def _eval_ast(node: ast.AST) -> float:
                if isinstance(node, ast.Expression):
                    return _eval_ast(node.body)
                if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
                    return float(node.value)
                if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
                    v = _eval_ast(node.operand)
                    return v if isinstance(node.op, ast.UAdd) else -v
                if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div)):
                    lhs = _eval_ast(node.left)
                    rhs = _eval_ast(node.right)
                    if isinstance(node.op, ast.Add):
                        return lhs + rhs
                    if isinstance(node.op, ast.Sub):
                        return lhs - rhs
                    if isinstance(node.op, ast.Mult):
                        return lhs * rhs
                    return lhs / rhs
                raise ValueError(f"Unsupported AST node {type(node).__name__}")

            out_v = _eval_ast(tree)
            evaluated_cache[key] = out_v
            return out_v

        all_formulas = True
        sample_formula = ""
        eval_preds = np.zeros(n, dtype=float)
        eval_resids = np.zeros(n, dtype=float)

        for i in range(n):
            r = i + 2
            raw_pred = ws_calc.cell(row=r, column=pred_col).value
            raw_res = ws_calc.cell(row=r, column=res_col).value
            if not (isinstance(raw_pred, str) and raw_pred.startswith("=")) or not (
                isinstance(raw_res, str) and raw_res.startswith("=")
            ):
                all_formulas = False
            if i == 0:
                sample_formula = str(raw_pred)
            eval_preds[i] = _eval_cell("Baseline_Formula_Calc", r, pred_col)
            eval_resids[i] = _eval_cell("Baseline_Formula_Calc", r, res_col)

        summary_row = n + 3
        rmse_formula_str = str(ws_calc.cell(row=summary_row, column=res_col).value or "").strip()
        m_rmse = _FUNC_SUMSQ_RE.match(rmse_formula_str)
        if not m_rmse:
            all_formulas = False
            excel_rmse = float("nan")
        else:
            c_idx = _col_letter_to_idx(m_rmse.group("c1"))
            r1, r2 = int(m_rmse.group("r1")), int(m_rmse.group("r2"))
            vals = np.array([_eval_cell("Baseline_Formula_Calc", rr, c_idx) for rr in range(r1, r2 + 1)], dtype=float)
            excel_rmse = float(np.sqrt(np.sum(vals * vals) / len(vals)))

        py_rmse = float(np.sqrt(np.mean((exp_act - exp_pred) ** 2)))
        max_pred_diff = float(np.max(np.abs(eval_preds - exp_pred))) if n else 0.0
        rmse_diff = abs(excel_rmse - py_rmse) if np.isfinite(excel_rmse) else float("inf")
        ok = bool(all_formulas and max_pred_diff <= float(tol) and rmse_diff <= float(tol))
        return ExcelFormulaRoundTripResult(
            workbook_path=str(wb_path),
            n_rows_verified=n,
            all_cells_are_formulas=all_formulas,
            sample_formula=sample_formula,
            max_abs_prediction_diff=max_pred_diff,
            python_rmse=py_rmse,
            excel_formula_rmse=excel_rmse,
            rmse_abs_diff=rmse_diff,
            passed=ok,
        )
    finally:
        wb.close()


def monitoring_snapshot(baseline: LockedBaselineRecord, input_sha256: str,
                        result: Mapping[str, Any]) -> dict[str, Any]:
    """Bind monitoring output to a verified immutable model version and input digest."""
    from ..monitoring.safety import assert_safe_payload
    if not isinstance(baseline, LockedBaselineRecord):
        raise ValueError("A locked baseline record is required")
    baseline.verify_integrity()
    if not isinstance(input_sha256, str) or re.fullmatch(r"[0-9a-f]{64}", input_sha256) is None:
        raise ValueError("A canonical SHA-256 input digest is required")
    payload = {"baseline_version": baseline.version, "baseline_lock_id": baseline.lock_id,
               "baseline_sha256": baseline.record_sha256, "input_sha256": input_sha256,
               "result": copy.deepcopy(dict(result))}
    assert_safe_payload(payload)
    # Round trip rejects nonfinite numbers and detaches mutable caller state.
    return json.loads(json.dumps(payload, sort_keys=True, allow_nan=False))
