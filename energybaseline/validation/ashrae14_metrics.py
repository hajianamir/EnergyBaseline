"""Out-of-sample ASHRAE Guideline 14-2014 / IPMVP Option C compliance metrics (VAL-6, DOM-2)."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any

import numpy as np


@dataclass(frozen=True)
class ASHRAE14Metrics:
    """Complete out-of-sample metric bundle with ASHRAE Guideline 14 / IPMVP threshold evaluation."""

    n: int
    n_params: int
    cvrmse: float
    cvrmse_ashrae14_dof: float
    nmbe: float
    nmbe_ashrae14_dof: float
    r2: float
    rmse: float
    mae: float
    mape: float
    smape: float
    wape: float
    granularity: str
    max_cvrmse_limit: float
    max_abs_nmbe_limit: float
    min_r2_limit: float
    meets_ashrae14: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "cvrmse": float(self.cvrmse),
            "cvrmse_ashrae14_dof": float(self.cvrmse_ashrae14_dof),
            "granularity": self.granularity,
            "mae": float(self.mae),
            "mape": float(self.mape),
            "max_abs_nmbe_limit": float(self.max_abs_nmbe_limit),
            "max_cvrmse_limit": float(self.max_cvrmse_limit),
            "meets_ashrae14": bool(self.meets_ashrae14),
            "min_r2_limit": float(self.min_r2_limit),
            "n": int(self.n),
            "n_params": int(self.n_params),
            "nmbe": float(self.nmbe),
            "nmbe_ashrae14_dof": float(self.nmbe_ashrae14_dof),
            "r2": float(self.r2),
            "rmse": float(self.rmse),
            "smape": float(self.smape),
            "wape": float(self.wape),
        }


def ashrae14_limits_for_granularity(granularity: str) -> tuple[float, float, float]:
    """Return ``(max_cvrmse_pct, max_abs_nmbe_pct, min_r2)`` per ASHRAE Guideline 14-2014 / IPMVP (DOM-2)."""
    g = str(granularity or "monthly").strip().lower()
    if g in ("monthly", "month", "1m", "me", "ms"):
        return 15.0, 5.0, 0.75
    if g in ("weekly", "week", "1w", "w"):
        return 20.0, 7.0, 0.60
    # Hourly, sub-hourly, or daily calibration limits per ASHRAE Guideline 14 Table 4-2
    return 30.0, 10.0, 0.50


def compute_ashrae14_oos_metrics(
    y_true: Any,
    y_pred: Any,
    *,
    n_params: int = 1,
    granularity: str = "monthly",
) -> ASHRAE14Metrics:
    """Compute out-of-sample CV(RMSE), NMBE, R2, MAE, MAPE, sMAPE, WAPE and ASHRAE 14 compliance."""
    yt = np.asarray(y_true, dtype=float).reshape(-1)
    yp = np.asarray(y_pred, dtype=float).reshape(-1)
    if yt.size != yp.size:
        raise ValueError(f"y_true ({yt.size}) and y_pred ({yp.size}) must have identical length.")
    mask = np.isfinite(yt) & np.isfinite(yp)
    yt, yp = yt[mask], yp[mask]
    n = int(yt.size)
    if n == 0:
        raise ValueError("Cannot compute ASHRAE 14 metrics on zero finite out-of-sample pairs.")

    p = max(1, int(n_params))
    res = yt - yp
    ybar = float(np.mean(yt))
    denom = abs(ybar) if abs(ybar) > 1e-12 else (float(np.mean(np.abs(yt))) or 1.0)
    sst = float(np.sum((yt - ybar) ** 2))
    sse = float(np.sum(res * res))

    rmse = math.sqrt(sse / float(n))
    cvrmse = 100.0 * rmse / denom
    nmbe = 100.0 * float(np.sum(res)) / (float(n) * denom)

    # ASHRAE Guideline 14 Section 5.3.2.1 degrees-of-freedom-adjusted OOS / baseline formulas (n - p)
    dof_rmse = max(1, n - p)
    dof_nmbe = max(1, n - p)
    cvrmse_dof = 100.0 * math.sqrt(sse / float(dof_rmse)) / denom
    nmbe_dof = 100.0 * float(np.sum(res)) / (float(dof_nmbe) * denom)

    r2 = float(1.0 - sse / sst) if sst > 1e-15 else 0.0
    mae = float(np.mean(np.abs(res)))
    nz = np.abs(yt) > 1e-9
    mape = float(100.0 * np.mean(np.abs(res[nz] / yt[nz]))) if np.any(nz) else float("nan")
    smape = float(100.0 * np.mean(2.0 * np.abs(res) / np.maximum(np.abs(yt) + np.abs(yp), 1e-12)))
    wape = float(100.0 * np.sum(np.abs(res)) / max(float(np.sum(np.abs(yt))), 1e-12))

    max_cv, max_nm, min_r2 = ashrae14_limits_for_granularity(granularity)
    meets = bool(cvrmse <= max_cv and abs(nmbe) <= max_nm and (not math.isfinite(r2) or r2 >= min_r2))
    return ASHRAE14Metrics(
        n=n,
        n_params=p,
        cvrmse=cvrmse,
        cvrmse_ashrae14_dof=cvrmse_dof,
        nmbe=nmbe,
        nmbe_ashrae14_dof=nmbe_dof,
        r2=r2,
        rmse=rmse,
        mae=mae,
        mape=mape,
        smape=smape,
        wape=wape,
        granularity=str(granularity),
        max_cvrmse_limit=max_cv,
        max_abs_nmbe_limit=max_nm,
        min_r2_limit=min_r2,
        meets_ashrae14=meets,
    )
