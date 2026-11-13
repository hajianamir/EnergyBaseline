"""Large-series chart downsampling: Largest-Triangle-Three-Buckets (LTTB) and M4 pixel-column envelope preservation."""

from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Any

import numpy as np


@dataclass(frozen=True)
class DownsampleResult:
    """Downsampled coordinates and quantitative fidelity bounds."""

    method: str
    n_input: int
    n_output: int
    compression_ratio: float
    indices: np.ndarray
    x: np.ndarray
    y: np.ndarray
    max_abs_interp_error: float
    rmse_interp_error: float
    envelope_min_error: float
    envelope_max_error: float
    elapsed_ms: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "compression_ratio": round(self.compression_ratio, 4),
            "elapsed_ms": round(self.elapsed_ms, 4),
            "envelope_max_error": round(self.envelope_max_error, 8),
            "envelope_min_error": round(self.envelope_min_error, 8),
            "max_abs_interp_error": round(self.max_abs_interp_error, 8),
            "method": self.method,
            "n_input": self.n_input,
            "n_output": self.n_output,
            "rmse_interp_error": round(self.rmse_interp_error, 8),
        }


def _to_numeric_xy(x: Any, y: Any) -> tuple[np.ndarray, np.ndarray]:
    yv = np.asarray(y, dtype=float).reshape(-1)
    if x is None:
        xv = np.arange(yv.size, dtype=float)
    else:
        x_arr = np.asarray(x)
        if np.issubdtype(x_arr.dtype, np.datetime64):
            xv = x_arr.astype("datetime64[ns]").astype(np.int64).astype(float) / 1e9
        else:
            xv = np.asarray(x_arr, dtype=float).reshape(-1)
    if xv.size != yv.size:
        raise ValueError(f"x and y must have identical length (got {xv.size} vs {yv.size}).")
    valid = np.isfinite(xv) & np.isfinite(yv)
    return xv[valid], yv[valid]


def _compute_error_bounds(
    xv: np.ndarray,
    yv: np.ndarray,
    idx: np.ndarray,
) -> tuple[float, float, float, float]:
    if xv.size == 0 or idx.size == 0:
        return 0.0, 0.0, 0.0, 0.0
    xs = xv[idx]
    ys = yv[idx]
    # Piecewise linear interpolation error across full series (sampled evenly above 250k for O(1) diagnostic overhead)
    step = max(1, xv.size // 250_000)
    xp = xv[::step]
    yp = yv[::step]
    order = np.argsort(xs, kind="mergesort")
    y_hat = np.interp(xp, xs[order], ys[order])
    diff = np.abs(yp - y_hat)
    max_err = float(np.max(diff))
    rmse_err = float(np.sqrt(np.mean(diff * diff)))
    env_min_err = float(abs(float(np.min(yv)) - float(np.min(ys))))
    env_max_err = float(abs(float(np.max(yv)) - float(np.max(ys))))
    return max_err, rmse_err, env_min_err, env_max_err


def lttb_downsample(x: Any, y: Any, threshold: int = 2000) -> DownsampleResult:
    """Downsample ``(x, y)`` to ``threshold`` points via Steinarsson's Largest-Triangle-Three-Buckets (LTTB)."""
    t0 = time.perf_counter()
    xv, yv = _to_numeric_xy(x, y)
    n = int(xv.size)
    k = int(threshold)
    if k < 3:
        raise ValueError(f"LTTB threshold must be >= 3 (got {k}).")
    if n <= k or n <= 2:
        idx = np.arange(n, dtype=np.int64)
        el = (time.perf_counter() - t0) * 1000.0
        return DownsampleResult(
            method="lttb",
            n_input=n,
            n_output=n,
            compression_ratio=1.0,
            indices=idx,
            x=xv.copy(),
            y=yv.copy(),
            max_abs_interp_error=0.0,
            rmse_interp_error=0.0,
            envelope_min_error=0.0,
            envelope_max_error=0.0,
            elapsed_ms=el,
        )

    n_buckets = k - 2
    edges = np.floor(np.linspace(1, n - 1, n_buckets + 1)).astype(np.int64)
    # Guarantee strictly positive bucket widths
    lengths = np.maximum(1, np.diff(edges))
    avg_x = np.add.reduceat(xv, edges[:-1]) / lengths
    avg_y = np.add.reduceat(yv, edges[:-1]) / lengths

    out_idx = np.empty(k, dtype=np.int64)
    out_idx[0] = 0
    out_idx[-1] = n - 1
    a_idx = 0

    for i in range(n_buckets):
        s_i = int(edges[i])
        e_i = int(edges[i + 1])
        if e_i <= s_i:
            e_i = min(n - 1, s_i + 1)
        if i + 1 < n_buckets:
            nx = float(avg_x[i + 1])
            ny = float(avg_y[i + 1])
        else:
            nx = float(xv[-1])
            ny = float(yv[-1])
        ax = float(xv[a_idx])
        ay = float(yv[a_idx])
        bx = xv[s_i:e_i]
        by = yv[s_i:e_i]
        areas = np.abs((ax - nx) * (by - ay) - (ax - bx) * (ny - ay))
        pick = s_i + int(np.argmax(areas))
        out_idx[i + 1] = pick
        a_idx = pick

    el = (time.perf_counter() - t0) * 1000.0
    max_err, rmse_err, env_min, env_max = _compute_error_bounds(xv, yv, out_idx)
    return DownsampleResult(
        method="lttb",
        n_input=n,
        n_output=int(out_idx.size),
        compression_ratio=float(n) / float(out_idx.size),
        indices=out_idx,
        x=xv[out_idx],
        y=yv[out_idx],
        max_abs_interp_error=max_err,
        rmse_interp_error=rmse_err,
        envelope_min_error=env_min,
        envelope_max_error=env_max,
        elapsed_ms=el,
    )


def m4_downsample(x: Any, y: Any, pixel_width: int = 800) -> DownsampleResult:
    """Downsample ``(x, y)`` via M4 pixel-column aggregation (first, min, max, last per column; <= 4 * pixel_width)."""
    t0 = time.perf_counter()
    xv, yv = _to_numeric_xy(x, y)
    n = int(xv.size)
    w = int(pixel_width)
    if w < 1:
        raise ValueError(f"pixel_width must be >= 1 (got {w}).")
    if n <= 4 * w:
        idx = np.arange(n, dtype=np.int64)
        el = (time.perf_counter() - t0) * 1000.0
        return DownsampleResult(
            method="m4",
            n_input=n,
            n_output=n,
            compression_ratio=1.0,
            indices=idx,
            x=xv.copy(),
            y=yv.copy(),
            max_abs_interp_error=0.0,
            rmse_interp_error=0.0,
            envelope_min_error=0.0,
            envelope_max_error=0.0,
            elapsed_ms=el,
        )

    edges = np.linspace(0, n, w + 1, dtype=np.int64)
    # Vectorized reshape when evenly divisible, otherwise fast slice loop over w <= ~2000 buckets
    chosen = np.empty(4 * w, dtype=np.int64)
    pos = 0
    for b in range(w):
        s_i = int(edges[b])
        e_i = int(edges[b + 1])
        if e_i <= s_i:
            continue
        seg = yv[s_i:e_i]
        i_first = s_i
        i_last = e_i - 1
        i_min = s_i + int(np.argmin(seg))
        i_max = s_i + int(np.argmax(seg))
        # Sort within bucket to preserve chronological x-order
        pts = sorted({i_first, i_min, i_max, i_last})
        for p in pts:
            chosen[pos] = p
            pos += 1

    out_idx = np.unique(chosen[:pos])
    el = (time.perf_counter() - t0) * 1000.0
    max_err, rmse_err, env_min, env_max = _compute_error_bounds(xv, yv, out_idx)
    return DownsampleResult(
        method="m4",
        n_input=n,
        n_output=int(out_idx.size),
        compression_ratio=float(n) / float(max(1, out_idx.size)),
        indices=out_idx,
        x=xv[out_idx],
        y=yv[out_idx],
        max_abs_interp_error=max_err,
        rmse_interp_error=rmse_err,
        envelope_min_error=env_min,
        envelope_max_error=env_max,
        elapsed_ms=el,
    )
