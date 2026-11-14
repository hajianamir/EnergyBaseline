"""AC-16 uncertainty gateway: predict first, then optionally observe outcomes."""

from .conformal import ConformalIntervals
from .calibration import interval_diagnostics
from .fsu import fractional_savings_uncertainty
from .savings_uncertainty import savings_uncertainty


def run_uq_catalog(adapter, X_holdout, y_holdout, *, tcp, sequential=False, gamma=0.01):
    tcp.assert_accepted()
    import numpy as np
    from .calibration import vector

    y = vector(y_holdout, minimum=3)
    X = np.asarray(X_holdout)
    if len(X) != len(y):
        raise ValueError("Holdout alignment mismatch")
    if sequential:
        if adapter.method not in ("enbpi", "aci"):
            raise ValueError("Sequential catalog requires time-series backend")
        points, intervals = [], []
        for i in range(len(y)):
            pred, bounds = adapter.predict(X[i : i + 1])
            points.extend(pred.tolist())
            intervals.extend(bounds.tolist())
            adapter.observe(X[i : i + 1], y[i : i + 1], gamma=gamma)
        points, intervals = np.asarray(points), np.asarray(intervals)
    else:
        points, intervals = adapter.predict(X)
    diagnostics = [
        interval_diagnostics(y, intervals[:, 0, j], intervals[:, 1, j], lv) for j, lv in enumerate(adapter.levels)
    ]
    return {
        "method": adapter.method,
        "levels": list(adapter.levels),
        "sequential": sequential,
        "coverage_contract": "Empirical diagnostics only for dependent or shifted series",
        "prediction": points.tolist(),
        "intervals": intervals.tolist(),
        "calibration": diagnostics,
        "ok": all(d["within_tolerance"] for d in diagnostics),
    }


__all__ = [
    "ConformalIntervals",
    "run_uq_catalog",
    "interval_diagnostics",
    "fractional_savings_uncertainty",
    "savings_uncertainty",
]
