"""Shared contracts, MT-5 input guard, OOF index alignment, and Model Evaluation Register types for F1..F10."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd

from ..mining.clustering import assert_no_cluster_labels_in_model_inputs


class SampleSizeGateSkippedError(ValueError):
    """Raised when a model family's sample-size minimum is not met (recorded as 'skipped' in register)."""


class OptionalBackendSkippedError(RuntimeError):
    """Raised when an optional model backend (e.g., Lite PyTorch or Prophet) is unavailable."""


def prepare_design_frame(X: Any, feature_names: Sequence[str] | None = None) -> tuple[pd.DataFrame, list[str]]:
    """Validate model inputs against MT-5 cluster-label leakage and coerce to a clean numeric DataFrame."""
    assert_no_cluster_labels_in_model_inputs(X)
    if isinstance(X, pd.DataFrame):
        cols = list(feature_names) if feature_names is not None else [str(c) for c in X.columns]
        out = pd.DataFrame(index=X.index)
        for c in cols:
            if c not in X.columns:
                raise KeyError(f"Required feature column '{c}' missing from input DataFrame.")
            out[c] = pd.to_numeric(X[c], errors="coerce")
        return out, cols
    if isinstance(X, pd.Series):
        name = str(X.name or (feature_names[0] if feature_names else "x0"))
        df = pd.DataFrame({name: pd.to_numeric(X, errors="coerce")}, index=X.index)
        return df, [name]
    arr = np.asarray(X, dtype=float)
    if arr.ndim == 1:
        arr = arr.reshape(-1, 1)
    if arr.ndim != 2:
        raise ValueError(f"Expected 1-D or 2-D design matrix, got shape {arr.shape}.")
    cols = (
        list(feature_names)
        if feature_names is not None
        else [f"x{j}" for j in range(arr.shape[1])]
    )
    return pd.DataFrame(arr, columns=cols), cols


def compute_baseline_metrics(y_true: Any, y_pred: Any) -> dict[str, float]:
    """Compute ASHRAE Guideline 14 / ISO 50006 baseline accuracy metrics (R2, RMSE, CVRMSE, NMBE, MAE, MAPE, sMAPE, WAPE)."""
    yt = np.asarray(y_true, dtype=float).reshape(-1)
    yp = np.asarray(y_pred, dtype=float).reshape(-1)
    if yt.size != yp.size:
        raise ValueError(f"y_true ({yt.size}) and y_pred ({yp.size}) must have identical length.")
    valid = np.isfinite(yt) & np.isfinite(yp)
    yt, yp = yt[valid], yp[valid]
    n = int(yt.size)
    if n == 0:
        return {
            "cvrmse": float("nan"),
            "mae": float("nan"),
            "mape": float("nan"),
            "n": 0.0,
            "nmbe": float("nan"),
            "r2": float("nan"),
            "rmse": float("nan"),
            "smape": float("nan"),
            "wape": float("nan"),
        }
    res = yt - yp
    ybar = float(np.mean(yt))
    denom = abs(ybar) if abs(ybar) > 1e-12 else (float(np.mean(np.abs(yt))) or 1.0)
    sst = float(np.sum((yt - ybar) ** 2))
    rmse = float(np.sqrt(np.mean(res * res)))
    nz = np.abs(yt) > 1e-9
    sum_abs_y = float(np.sum(np.abs(yt)))
    return {
        "cvrmse": float(100.0 * rmse / denom),
        "mae": float(np.mean(np.abs(res))),
        "mape": float(100.0 * np.mean(np.abs(res[nz] / yt[nz]))) if np.any(nz) else float("nan"),
        "n": float(n),
        "nmbe": float(100.0 * np.sum(res) / (float(n) * denom)),
        "r2": float(1.0 - float(np.sum(res * res)) / sst) if sst > 1e-15 else 0.0,
        "rmse": rmse,
        "smape": float(100.0 * np.mean(2.0 * np.abs(res) / np.maximum(np.abs(yt) + np.abs(yp), 1e-12))),
        "wape": float(100.0 * np.sum(np.abs(res)) / max(sum_abs_y, 1e-12)),
    }


def align_oof_predictions(
    predictions_by_model: Mapping[str, pd.Series | np.ndarray],
    y_true: pd.Series | np.ndarray,
) -> tuple[pd.Index, np.ndarray, dict[str, np.ndarray]]:
    """Align heterogeneous OOF model predictions on their exact common non-NaN timestamp index (Phase 4 §9 Risk 1)."""
    if isinstance(y_true, pd.Series):
        y_ser = y_true.astype(float)
    else:
        y_arr = np.asarray(y_true, dtype=float).reshape(-1)
        y_ser = pd.Series(y_arr, index=pd.RangeIndex(y_arr.size))

    series_map: dict[str, pd.Series] = {}
    common_idx = y_ser.dropna().index
    for name, pred in predictions_by_model.items():
        if isinstance(pred, pd.Series):
            s = pred.astype(float)
        else:
            arr = np.asarray(pred, dtype=float).reshape(-1)
            # Align trailing end when leading warm-up rows were trimmed (e.g., LSTM / AR lags)
            if arr.size <= len(y_ser):
                s = pd.Series(arr, index=y_ser.index[len(y_ser) - arr.size :])
            else:
                raise ValueError(f"Prediction series '{name}' longer than y_true ({arr.size} > {len(y_ser)}).")
        s_valid = s.dropna()
        common_idx = common_idx.intersection(s_valid.index)
        series_map[str(name)] = s_valid

    if len(common_idx) == 0:
        raise ValueError("No overlapping finite OOF timestamps across candidate models.")
    aligned_preds = {name: ser.loc[common_idx].to_numpy(dtype=float) for name, ser in series_map.items()}
    return common_idx, y_ser.loc[common_idx].to_numpy(dtype=float), aligned_preds


def recommend_autocorr_embargo_gap(
    y: Any,
    *,
    steps_per_day: float = 24.0,
    max_lag: int = 0,
    max_rolling_window: int = 0,
) -> int:
    """Compute autocorrelation-aware CV embargo/purge gap (Phase 4 §9 Risk 2 & VAL-2)."""
    yv = np.asarray(y, dtype=float).reshape(-1)
    yv = yv[np.isfinite(yv)]
    n = int(yv.size)
    base_gap = max(1, int(round(steps_per_day)), int(max_lag), int(max_rolling_window))
    if n < 24 or float(np.std(yv)) <= 1e-12:
        return min(base_gap, max(1, n // 10))
    yc = yv - float(np.mean(yv))
    denom = float(np.dot(yc, yc))
    thresh = max(1.96 / np.sqrt(float(n)), 1.0 / np.e)
    ac_decay_lag = 1
    max_probe = min(60, n // 6)
    for lag in range(1, max_probe + 1):
        r_k = abs(float(np.dot(yc[lag:], yc[:-lag])) / denom)
        if r_k >= thresh:
            ac_decay_lag = lag
        else:
            break
    return min(max(base_gap, ac_decay_lag), max(1, n // 8))


@dataclass(frozen=True)
class NNGateDecision:
    """Bidirectional neural-network eligibility & acceptance gate decision (Phase 6 Gate G6)."""

    model_name: str
    accepted: bool
    status: str
    n_obs: int
    min_samples: int
    n_features: int
    nn_cvrmse: float | None
    reference_cvrmse: float | None
    relative_gain_pct: float | None
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "accepted": bool(self.accepted),
            "min_samples": int(self.min_samples),
            "model_name": self.model_name,
            "n_features": int(self.n_features),
            "n_obs": int(self.n_obs),
            "nn_cvrmse": round(float(self.nn_cvrmse), 4) if self.nn_cvrmse is not None and np.isfinite(self.nn_cvrmse) else None,
            "reason": self.reason,
            "reference_cvrmse": (
                round(float(self.reference_cvrmse), 4)
                if self.reference_cvrmse is not None and np.isfinite(self.reference_cvrmse)
                else None
            ),
            "relative_gain_pct": (
                round(float(self.relative_gain_pct), 4)
                if self.relative_gain_pct is not None and np.isfinite(self.relative_gain_pct)
                else None
            ),
            "status": self.status,
        }


def evaluate_nn_gate(
    model_name: str,
    *,
    n_obs: int,
    n_features: int = 1,
    min_samples: int = 50,
    min_obs_per_feature: int = 10,
    nn_cvrmse: float | None = None,
    reference_cvrmse: float | None = None,
    max_cvrmse: float | None = None,
    min_relative_gain_pct: float = 0.0,
) -> NNGateDecision:
    """Evaluate bidirectional neural-network eligibility & OOS acceptance gate with logged reason."""
    n_i = int(n_obs)
    p_i = max(1, int(n_features))
    req_n = max(int(min_samples), int(min_obs_per_feature) * p_i)
    if n_i < req_n:
        return NNGateDecision(
            model_name=str(model_name),
            accepted=False,
            status="skipped_sample_size_gate",
            n_obs=n_i,
            min_samples=req_n,
            n_features=p_i,
            nn_cvrmse=nn_cvrmse,
            reference_cvrmse=reference_cvrmse,
            relative_gain_pct=None,
            reason=(
                f"NN gate SKIPPED {model_name}: sample size n={n_i} < required {req_n} "
                f"(floor={min_samples}, {min_obs_per_feature}*p={min_obs_per_feature * p_i})."
            ),
        )

    if nn_cvrmse is not None:
        if not np.isfinite(nn_cvrmse):
            return NNGateDecision(
                model_name=str(model_name),
                accepted=False,
                status="rejected_nn_gate",
                n_obs=n_i,
                min_samples=req_n,
                n_features=p_i,
                nn_cvrmse=None,
                reference_cvrmse=reference_cvrmse,
                relative_gain_pct=None,
                reason=f"NN gate REJECTED {model_name}: non-finite OOS CV(RMSE) on n={n_i}.",
            )
        if max_cvrmse is not None and float(nn_cvrmse) > float(max_cvrmse):
            return NNGateDecision(
                model_name=str(model_name),
                accepted=False,
                status="rejected_nn_gate",
                n_obs=n_i,
                min_samples=req_n,
                n_features=p_i,
                nn_cvrmse=float(nn_cvrmse),
                reference_cvrmse=reference_cvrmse,
                relative_gain_pct=None,
                reason=(
                    f"NN gate REJECTED {model_name}: OOS CV(RMSE)={nn_cvrmse:.3f}% exceeds "
                    f"ASHRAE threshold {max_cvrmse:.2f}% (n={n_i})."
                ),
            )
        if reference_cvrmse is not None and np.isfinite(reference_cvrmse) and float(reference_cvrmse) > 1e-9:
            gain_pct = 100.0 * (float(reference_cvrmse) - float(nn_cvrmse)) / float(reference_cvrmse)
            if gain_pct < float(min_relative_gain_pct):
                return NNGateDecision(
                    model_name=str(model_name),
                    accepted=False,
                    status="rejected_nn_gate",
                    n_obs=n_i,
                    min_samples=req_n,
                    n_features=p_i,
                    nn_cvrmse=float(nn_cvrmse),
                    reference_cvrmse=float(reference_cvrmse),
                    relative_gain_pct=gain_pct,
                    reason=(
                        f"NN gate REJECTED {model_name}: relative CV(RMSE) gain {gain_pct:.2f}% < "
                        f"required {min_relative_gain_pct:.2f}% vs tabular reference ({nn_cvrmse:.3f}% vs {reference_cvrmse:.3f}%)."
                    ),
                )
            return NNGateDecision(
                model_name=str(model_name),
                accepted=True,
                status="accepted_nn_gate",
                n_obs=n_i,
                min_samples=req_n,
                n_features=p_i,
                nn_cvrmse=float(nn_cvrmse),
                reference_cvrmse=float(reference_cvrmse),
                relative_gain_pct=gain_pct,
                reason=(
                    f"NN gate ACCEPTED {model_name}: n={n_i} >= {req_n} and OOS CV(RMSE)={nn_cvrmse:.3f}% "
                    f"(gain {gain_pct:+.2f}% vs reference {reference_cvrmse:.3f}%)."
                ),
            )

    return NNGateDecision(
        model_name=str(model_name),
        accepted=True,
        status="accepted_nn_gate",
        n_obs=n_i,
        min_samples=req_n,
        n_features=p_i,
        nn_cvrmse=float(nn_cvrmse) if nn_cvrmse is not None else None,
        reference_cvrmse=reference_cvrmse,
        relative_gain_pct=None,
        reason=f"NN gate ACCEPTED {model_name}: sample size n={n_i} >= required {req_n} (p={p_i}).",
    )


@dataclass(frozen=True)
class ModelEvaluationEntry:
    """Single row of the UX-5 / VAL-7 Model Evaluation Register."""

    family_id: str
    family_name: str
    model_name: str
    status: str
    status_badge: str
    r2: float | None
    cvrmse: float | None
    nmbe: float | None
    mae: float | None
    rmse: float | None
    wape: float | None
    composite_score: float | None
    in_mcs: bool
    n_obs: int
    n_params: int
    fit_seconds: float
    formula_summary: str
    reason: str
    requirement_tier: str = "M"

    def to_dict(self) -> dict[str, Any]:
        return {
            "composite_score": round(self.composite_score, 3) if self.composite_score is not None and np.isfinite(self.composite_score) else None,
            "cvrmse": round(self.cvrmse, 4) if self.cvrmse is not None and np.isfinite(self.cvrmse) else None,
            "family_id": self.family_id,
            "family_name": self.family_name,
            "fit_seconds": round(self.fit_seconds, 4),
            "formula_summary": self.formula_summary,
            "in_mcs": bool(self.in_mcs),
            "mae": round(self.mae, 4) if self.mae is not None and np.isfinite(self.mae) else None,
            "model_name": self.model_name,
            "n_obs": self.n_obs,
            "n_params": self.n_params,
            "nmbe": round(self.nmbe, 4) if self.nmbe is not None and np.isfinite(self.nmbe) else None,
            "r2": round(self.r2, 4) if self.r2 is not None and np.isfinite(self.r2) else None,
            "reason": self.reason,
            "requirement_tier": self.requirement_tier,
            "rmse": round(self.rmse, 4) if self.rmse is not None and np.isfinite(self.rmse) else None,
            "status": self.status,
            "status_badge": self.status_badge,
            "wape": round(self.wape, 4) if self.wape is not None and np.isfinite(self.wape) else None,
        }
