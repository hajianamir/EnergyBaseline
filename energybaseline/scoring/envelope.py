"""Baseline Validity Domain Envelope: min/max, quantiles & multivariate convex/ellipsoidal hull (SC-9).

Records the empirical validity envelope of baseline training drivers so post-baseline or holdout
predictions outside the calibrated operating domain emit explicit extrapolation warnings.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd
from scipy.spatial import ConvexHull, Delaunay, QhullError

from ..mining.clustering import assert_no_cluster_labels_in_model_inputs


@dataclass(frozen=True)
class DriverBounds:
    """Univariate training range and quantile bounds for a single independent variable."""

    driver: str
    min_val: float
    p01: float
    p05: float
    median: float
    p95: float
    p99: float
    max_val: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "driver": self.driver,
            "max": float(self.max_val),
            "median": float(self.median),
            "min": float(self.min_val),
            "p01": float(self.p01),
            "p05": float(self.p05),
            "p95": float(self.p95),
            "p99": float(self.p99),
        }


@dataclass(frozen=True)
class ExtrapolationReport:
    """Row- and driver-level extrapolation check against a fitted ``ValidityDomainEnvelope``."""

    n_rows_checked: int
    n_rows_outside_minmax: int
    n_rows_outside_p01_p99: int
    n_rows_outside_convex_hull: int
    extrapolation_detected: bool
    per_driver_minmax_violations: dict[str, int]
    row_extrapolation_mask: tuple[bool, ...]
    warnings: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "extrapolation_detected": bool(self.extrapolation_detected),
            "n_rows_checked": int(self.n_rows_checked),
            "n_rows_outside_convex_hull": int(self.n_rows_outside_convex_hull),
            "n_rows_outside_minmax": int(self.n_rows_outside_minmax),
            "n_rows_outside_p01_p99": int(self.n_rows_outside_p01_p99),
            "per_driver_minmax_violations": dict(self.per_driver_minmax_violations),
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class ValidityDomainEnvelope:
    """Immutable baseline validity envelope storing univariate bounds + 2D-5D convex hull vertices (SC-9)."""

    drivers: tuple[str, ...]
    n_training_obs: int
    bounds: dict[str, DriverBounds]
    hull_drivers: tuple[str, ...]
    hull_vertices: tuple[tuple[float, ...], ...]
    mahalanobis_center: tuple[float, ...]
    mahalanobis_inv_cov: tuple[tuple[float, ...], ...]
    mahalanobis_threshold_99: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "bounds": {k: v.to_dict() for k, v in self.bounds.items()},
            "drivers": list(self.drivers),
            "hull_drivers": list(self.hull_drivers),
            "hull_vertices": [list(pt) for pt in self.hull_vertices],
            "mahalanobis_center": list(self.mahalanobis_center),
            "mahalanobis_inv_cov": [list(row) for row in self.mahalanobis_inv_cov],
            "mahalanobis_threshold_99": float(self.mahalanobis_threshold_99),
            "n_training_obs": int(self.n_training_obs),
        }

    def check_extrapolation(self, X_new: pd.DataFrame, *, tol: float = 1e-9) -> ExtrapolationReport:
        """Evaluate whether ``X_new`` falls outside training min/max, [p01, p99], or convex hull."""
        assert_no_cluster_labels_in_model_inputs(X_new)
        df = X_new if isinstance(X_new, pd.DataFrame) else pd.DataFrame(X_new, columns=list(self.drivers))
        m = len(df)
        if m == 0 or not self.drivers:
            return ExtrapolationReport(0, 0, 0, 0, False, {}, (), ())

        mm_mask = np.zeros(m, dtype=bool)
        q_mask = np.zeros(m, dtype=bool)
        per_drv: dict[str, int] = {}
        warns: list[str] = []

        for d in self.drivers:
            b = self.bounds[d]
            if d not in df.columns:
                per_drv[d] = 0
                continue
            vals = pd.to_numeric(df[d], errors="coerce").to_numpy(dtype=float)
            out_mm = np.isfinite(vals) & ((vals < b.min_val - tol) | (vals > b.max_val + tol))
            out_q = np.isfinite(vals) & ((vals < b.p01 - tol) | (vals > b.p99 + tol))
            cnt = int(np.sum(out_mm))
            per_drv[d] = cnt
            mm_mask |= out_mm
            q_mask |= out_q
            if cnt > 0:
                warns.append(
                    f"SC-9 extrapolation warning: driver '{d}' has {cnt}/{m} row(s) outside baseline training "
                    f"range [{b.min_val:.4g}, {b.max_val:.4g}]."
                )

        hull_mask = np.zeros(m, dtype=bool)
        if len(self.hull_drivers) >= 2 and len(self.hull_vertices) >= len(self.hull_drivers) + 1:
            if all(hd in df.columns for hd in self.hull_drivers):
                pts = df[list(self.hull_drivers)].apply(pd.to_numeric, errors="coerce").to_numpy(dtype=float)
                finite_rows = np.all(np.isfinite(pts), axis=1)
                if np.any(finite_rows):
                    try:
                        tri = Delaunay(np.asarray(self.hull_vertices, dtype=float))
                        simplex = tri.find_simplex(pts[finite_rows], tol=1e-7)
                        outside = simplex < 0
                        hull_mask[finite_rows] = outside
                        n_hull = int(np.sum(outside))
                        if n_hull > 0:
                            warns.append(
                                f"SC-9 joint convex-hull warning: {n_hull}/{m} row(s) fall outside the "
                                f"multivariate ({', '.join(self.hull_drivers)}) baseline hull."
                            )
                    except QhullError:
                        pass

        row_flag = mm_mask | hull_mask
        return ExtrapolationReport(
            n_rows_checked=m,
            n_rows_outside_minmax=int(np.sum(mm_mask)),
            n_rows_outside_p01_p99=int(np.sum(q_mask)),
            n_rows_outside_convex_hull=int(np.sum(hull_mask)),
            extrapolation_detected=bool(np.any(row_flag)),
            per_driver_minmax_violations=per_drv,
            row_extrapolation_mask=tuple(bool(v) for v in row_flag),
            warnings=tuple(warns),
        )


def fit_validity_domain_envelope(
    X_train: pd.DataFrame,
    *,
    max_hull_dims: int = 4,
) -> ValidityDomainEnvelope:
    """Fit a univariate + multivariate convex-hull & Mahalanobis validity envelope on baseline drivers (SC-9)."""
    assert_no_cluster_labels_in_model_inputs(X_train)
    df = X_train.copy() if isinstance(X_train, pd.DataFrame) else pd.DataFrame(X_train)
    num_cols = [str(c) for c in df.columns if pd.api.types.is_numeric_dtype(df[c]) and df[c].notna().any()]

    bounds: dict[str, DriverBounds] = {}
    for c in num_cols:
        arr = pd.to_numeric(df[c], errors="coerce").dropna().to_numpy(dtype=float)
        q01, q05, q50, q95, q99 = np.quantile(arr, [0.01, 0.05, 0.50, 0.95, 0.99])
        bounds[c] = DriverBounds(
            driver=c,
            min_val=float(np.min(arr)),
            p01=float(q01),
            p05=float(q05),
            median=float(q50),
            p95=float(q95),
            p99=float(q99),
            max_val=float(np.max(arr)),
        )

    # Select up to max_hull_dims non-constant drivers for joint ConvexHull
    non_const = [c for c in num_cols if (bounds[c].max_val - bounds[c].min_val) > 1e-9]
    hull_cols = tuple(non_const[: max(0, min(int(max_hull_dims), 5))])
    hull_verts: tuple[tuple[float, ...], ...] = ()
    if len(hull_cols) >= 2 and len(df) >= len(hull_cols) + 2:
        mat = df[list(hull_cols)].apply(pd.to_numeric, errors="coerce").dropna().to_numpy(dtype=float)
        if len(mat) >= len(hull_cols) + 2:
            try:
                ch = ConvexHull(mat)
                hull_verts = tuple(tuple(float(v) for v in mat[idx]) for idx in ch.vertices)
            except QhullError:
                hull_verts = ()

    if non_const and len(df) >= 3:
        m_mat = df[non_const].apply(pd.to_numeric, errors="coerce")
        m_mat = m_mat.fillna(m_mat.median(numeric_only=True)).fillna(0.0).to_numpy(dtype=float)
        center = np.mean(m_mat, axis=0)
        cov = np.cov(m_mat, rowvar=False)
        cov_2d = np.atleast_2d(cov) + np.eye(len(non_const)) * 1e-6
        inv_cov = np.linalg.pinv(cov_2d)
        diff = m_mat - center
        d2 = np.einsum("ij,jk,ik->i", diff, inv_cov, diff)
        thr99 = float(np.quantile(d2, 0.99))
        c_tup = tuple(float(v) for v in center)
        inv_tup = tuple(tuple(float(x) for x in row) for row in inv_cov)
    else:
        c_tup = ()
        inv_tup = ()
        thr99 = 0.0

    return ValidityDomainEnvelope(
        drivers=tuple(num_cols),
        n_training_obs=len(df),
        bounds=bounds,
        hull_drivers=hull_cols,
        hull_vertices=hull_verts,
        mahalanobis_center=c_tup,
        mahalanobis_inv_cov=inv_tup,
        mahalanobis_threshold_99=thr99,
    )
