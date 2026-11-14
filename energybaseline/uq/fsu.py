"""Fractional Savings Uncertainty; undefined near-zero savings are explicit."""

from .savings_uncertainty import savings_uncertainty


def fractional_savings_uncertainty(savings, calibration_residuals, *, level=0.95, **kwargs):
    result = savings_uncertainty(savings, calibration_residuals, level=level, **kwargs)
    total = abs(result["savings"])
    result["fsu"] = result["half_width"] / total if total > 1e-12 else None
    result["defined"] = total > 1e-12
    return result
