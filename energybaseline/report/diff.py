"""Supplementary artifact-level report diff and spreadsheet formula adapter."""

from .provenance import canonical


def report_diff(before, after):
    a, b = before.data["artifacts"], after.data["artifacts"]
    return {
        key: {"before": a.get(key), "after": b.get(key)}
        for key in sorted(set(a) | set(b))
        if canonical(a.get(key)) != canonical(b.get(key))
    }


def export_formula_workbook(baseline, drivers, actual, path):
    from ..lock.baseline_lock import export_locked_baseline_excel

    baseline.verify_integrity()
    return export_locked_baseline_excel(baseline, drivers, actual, path)
