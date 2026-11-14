"""Strict reporting ingestion, adjusted EnPI, control charts and append-only audit history."""

from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import hashlib
import json
import numpy as np
import pandas as pd
from ..uq.calibration import vector, confidence
from ..uq.savings_uncertainty import savings_uncertainty
from .adjustments import apply_adjustments


class ReportingInputError(ValueError):
    """Deterministic rejection of malformed reporting input; no silent resampling."""


@dataclass(frozen=True)
class ReportingSchema:
    drivers: tuple[str, ...]
    target: str
    units: tuple[tuple[str, str], ...]
    step_seconds: float
    time_column: str = "timestamp"

    def __post_init__(self):
        if (
            not self.drivers
            or len(set(self.drivers)) != len(self.drivers)
            or self.target in self.drivers
            or self.time_column in (*self.drivers, self.target)
            or not np.isfinite(self.step_seconds)
            or self.step_seconds <= 0
            or set(dict(self.units)) != set((*self.drivers, self.target))
        ):
            raise ReportingInputError("Invalid baseline schema")


def validate_reporting(frame, schema, *, units):
    try:
        if not isinstance(frame, pd.DataFrame) or frame.empty or not frame.columns.is_unique:
            raise ReportingInputError("Reporting data must be a nonempty frame with unique columns")
        expected = [schema.time_column, *schema.drivers, schema.target]
        if set(frame.columns) != set(expected) or dict(units) != dict(schema.units):
            raise ReportingInputError("Reporting columns or units do not match the baseline")
        out = frame.loc[:, expected].copy()
        ts = pd.to_datetime(out[schema.time_column], utc=True, errors="raise", format="mixed")
        if ts.isna().any() or not ts.is_monotonic_increasing or ts.duplicated().any():
            raise ReportingInputError("Timestamps must be finite, unique and chronological")
        gaps = ts.diff().dt.total_seconds().iloc[1:].to_numpy()
        if len(gaps) and not np.allclose(gaps, schema.step_seconds, rtol=0, atol=1e-6):
            raise ReportingInputError("Reporting granularity differs from baseline")
        for col in (*schema.drivers, schema.target):
            if pd.api.types.is_bool_dtype(out[col]) or not pd.api.types.is_numeric_dtype(out[col]):
                raise ReportingInputError("Reporting values must have numeric dtypes")
            if not np.isfinite(out[col].to_numpy(dtype=float)).all():
                raise ReportingInputError("Missing or infinite reporting value")
        out[schema.time_column] = ts
        return out
    except ReportingInputError:
        raise
    except (ValueError, TypeError, KeyError, OverflowError) as exc:
        raise ReportingInputError("Malformed reporting input") from exc


def cusum_chart(standardized_residuals, *, k=0.5, h=5.0):
    z = vector(standardized_residuals)
    if k <= 0 or h <= 0:
        raise ValueError("Positive CUSUM parameters required")
    pos, neg = [], []
    p = m = 0.0
    for v in z:
        p = max(0.0, p + v - k)
        m = min(0.0, m + v + k)
        pos.append(p)
        neg.append(m)
    return {
        "positive": pos,
        "negative": neg,
        "alarm": ((np.asarray(pos) > h) | (np.asarray(neg) < -h)).tolist(),
        "reference_k": k,
        "decision_interval_h": h,
    }


def ewma_chart(standardized_residuals, *, smoothing=0.2, width=3.0):
    z = vector(standardized_residuals)
    if not 0 < smoothing <= 1 or width <= 0:
        raise ValueError("Invalid EWMA parameters")
    state = 0.0
    values = []
    for v in z:
        state = smoothing * v + (1 - smoothing) * state
        values.append(state)
    asymptotic = width * np.sqrt(smoothing / (2 - smoothing))
    limits = asymptotic * np.sqrt(1 - (1 - smoothing) ** (2 * np.arange(1, len(z) + 1)))
    return {
        "value": values,
        "upper": limits.tolist(),
        "lower": (-limits).tolist(),
        "asymptotic_limit": float(asymptotic),
        "alarm": (np.abs(values) > limits).tolist(),
    }


def predict_reporting(frame, *, schema, units, model, envelope, calibration_residuals, events=(), level=0.95):
    """Calls predict only. Residual reference distribution is frozen at calibration."""
    data = validate_reporting(frame, schema, units=units)
    confidence(level)
    X = data.loc[:, list(schema.drivers)]
    routine = vector(model.predict(X))
    if len(routine) != len(data):
        raise ValueError("Prediction alignment mismatch")
    adjusted, adjustment_audit = apply_adjustments(routine, events)
    actual = data[schema.target].to_numpy(dtype=float)
    resid = actual - adjusted
    savings = -resid
    reference = vector(calibration_residuals, minimum=5)
    sigma = float(np.std(reference, ddof=1))
    if sigma <= 0:
        raise ValueError("A positive calibrated residual scale is required")
    z = (resid - reference.mean()) / sigma
    check = envelope.check_extrapolation(X)
    flags = list(check.row_extrapolation_mask)
    out = pd.DataFrame(
        {
            "timestamp": data[schema.time_column],
            "routine_baseline": routine,
            "adjusted_baseline": adjusted,
            "actual": actual,
            "residual": resid,
            "savings": savings,
            "cumulative_savings": np.cumsum(savings),
            "enpi_ratio": np.divide(actual, adjusted, out=np.full(len(actual), np.nan), where=abs(adjusted) > 1e-12),
            "cumulative_enpi_ratio": np.divide(
                np.cumsum(actual),
                np.cumsum(adjusted),
                out=np.full(len(actual), np.nan),
                where=abs(np.cumsum(adjusted)) > 1e-12,
            ),
            "extrapolation": flags,
        }
    )
    for prefix in range(1, len(out) + 1):
        band = savings_uncertainty(
            savings[:prefix], reference, level=level, static_factor_sd=adjustment_audit["static_factor_sd"]
        )
        out.loc[out.index[prefix - 1], "cumulative_lower"] = band["lower"]
        out.loc[out.index[prefix - 1], "cumulative_upper"] = band["upper"]
    # Period uncertainty includes static factors; cumulative bands include serial covariance.
    period = savings_uncertainty([0.0], reference, level=level, static_factor_sd=adjustment_audit["static_factor_sd"])
    out["savings_lower"], out["savings_upper"] = savings - period["half_width"], savings + period["half_width"]
    return {
        "rows": json.loads(out.to_json(orient="records", date_format="iso")),
        "adjustments": adjustment_audit,
        "domain": check.to_dict(),
        "cusum": cusum_chart(z),
        "ewma": ewma_chart(z),
        "control_reference": {
            "mean": float(reference.mean()),
            "sd": sigma,
            "sha256": hashlib.sha256(reference.tobytes()).hexdigest(),
        },
        "uncertainty": savings_uncertainty(
            savings, reference, level=level, static_factor_sd=adjustment_audit["static_factor_sd"]
        ),
    }


def frame_input_hash(frame):
    """Hash exact validated columns, units-independent values, dtypes and timestamps."""

    def scalar(value):
        # Float repr preserves every binary64 bit; ISO timestamps retain nanoseconds.
        # Ordinary DataFrame JSON rounds both and is unsuitable for audit binding.
        if isinstance(value, (pd.Timestamp, np.datetime64)):
            return ["timestamp", pd.Timestamp(value).isoformat()]
        if isinstance(value, (bool, np.bool_)):
            return ["bool", bool(value)]
        if isinstance(value, (float, np.floating)):
            return [str(type(value)), repr(value)]
        if isinstance(value, (int, np.integer)):
            return ["integer", str(value)]
        if value is None or value is pd.NA or value is pd.NaT:
            return ["missing", str(value)]
        if isinstance(value, str):
            return ["text", value]
        raise ValueError("Unsupported reporting scalar for audit hashing")

    payload = {
        "columns": [scalar(v) for v in frame.columns],
        "dtypes": [str(d) for d in frame.dtypes],
        "index": [scalar(v) for v in frame.index],
        "data": [[scalar(v) for v in row] for row in frame.itertuples(index=False, name=None)],
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


class MonitoringHistory:
    """Single-user append-only JSONL history, exclusively created and hash chained.

    A batch ID and chronological window can occur once per baseline. Full chain
    verification happens before append; a corrupt history is never extended.
    """

    def __init__(self, path):
        self.path = Path(path)

    def read(self):
        records = []
        previous = None
        if not self.path.exists():
            return records
        for line in self.path.read_text(encoding="utf-8").splitlines():
            record = json.loads(line)
            digest = record.pop("sha256")
            calculated = hashlib.sha256(
                json.dumps(record, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
            ).hexdigest()
            if digest != calculated or record["previous_sha256"] != previous:
                raise ValueError("Monitoring history integrity failure")
            record["sha256"] = digest
            records.append(record)
            previous = digest
        return records

    def append(self, *, baseline, frame, result, batch_id, schema, units, calibration_residuals=None):
        from ..lock.baseline_lock import monitoring_snapshot

        validated = validate_reporting(frame, schema, units=units)
        history = self.read()
        if not isinstance(batch_id, str) or not batch_id.strip():
            raise ValueError("Batch ID required")
        same = [r for r in history if r["baseline_sha256"] == baseline.record_sha256]
        start, end = validated[schema.time_column].iloc[[0, -1]].astype(str)
        if any(r["batch_id"] == batch_id for r in same) or (
            same and pd.Timestamp(start) <= pd.Timestamp(same[-1]["period_end"])
        ):
            raise ValueError("Duplicate or overlapping reporting batch")
        # Extend cumulative savings and control-chart state across nonoverlapping batches.
        # Preserve batch rows, while chart arrays cover the entire monitored history.
        import copy

        result = copy.deepcopy(result)
        if "rows" in result:
            rows = result["rows"]
            if len(rows) != len(validated):
                raise ValueError("Monitoring row count does not match reporting input")
            prior = [row for item in same for row in item["result"].get("rows", [])]
            all_rows = prior + rows
            if calibration_residuals is None:
                raise ValueError("Calibration residuals required for incremental monitoring")
            reference = vector(calibration_residuals, minimum=5)
            ref_hash = hashlib.sha256(reference.tobytes()).hexdigest()
            if result["control_reference"]["sha256"] != ref_hash or any(
                item["result"].get("control_reference", {}).get("sha256") != ref_hash for item in same
            ):
                raise ValueError("Calibration reference changed across monitoring batches")
            if any(item["result"].get("adjustments") != result.get("adjustments") for item in same):
                raise ValueError("Adjustment changes require a separate monitoring history")
            saving = np.array([r["savings"] for r in all_rows])
            actual = np.array([r["actual"] for r in all_rows])
            adjusted = np.array([r["adjusted_baseline"] for r in all_rows])
            for j, row in enumerate(rows, start=len(prior)):
                band = savings_uncertainty(
                    saving[: j + 1],
                    reference,
                    level=result["uncertainty"]["confidence"],
                    static_factor_sd=result["adjustments"]["static_factor_sd"],
                )
                row["cumulative_savings"] = float(saving[: j + 1].sum())
                denom = float(adjusted[: j + 1].sum())
                row["cumulative_enpi_ratio"] = float(actual[: j + 1].sum() / denom) if abs(denom) > 1e-12 else None
                row["cumulative_lower"], row["cumulative_upper"] = band["lower"], band["upper"]
            z = (actual - adjusted - reference.mean()) / np.std(reference, ddof=1)
            result["cusum"], result["ewma"] = cusum_chart(z), ewma_chart(z)
            result["uncertainty"] = band
            result["monitored_rows"] = len(all_rows)
        record = monitoring_snapshot(baseline, frame_input_hash(validated), result)
        record.update(
            batch_id=batch_id,
            period_start=start,
            period_end=end,
            previous_sha256=history[-1]["sha256"] if history else None,
        )
        record["sha256"] = hashlib.sha256(
            json.dumps(record, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        ).hexdigest()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, sort_keys=True, allow_nan=False) + "\n")
        return record
