"""Optional automated EDA attachment explicitly labelled 'descriptive, not inferential' (Phase 5 F10 EDA attach)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd
from scipy import stats as sp_stats

from .downsample import lttb_downsample

DESCRIPTIVE_LABEL = "descriptive, not inferential"

_FORBIDDEN_INFERENTIAL_KEYS = frozenset(
    {
        "p_value",
        "pvalue",
        "p_raw",
        "p_adj",
        "rejected_null",
        "reject_null",
        "hypothesis_decision",
        "causal_effect",
        "observed_power",
    }
)

_FORBIDDEN_INFERENTIAL_PHRASES: tuple[str, ...] = (
    "statistically significant",
    "reject the null",
    "null hypothesis rejected",
    "proves causality",
    "causes an increase",
    "causes a decrease",
    "p < 0.05",
    "p <= 0.05",
)


class DescriptiveOnlyViolationError(ValueError):
    """Raised if an optional EDA attachment omits the 'descriptive, not inferential' label or makes inferential claims."""


def assert_descriptive_only(payload: Mapping[str, Any]) -> None:
    """Verify ``payload`` carries the mandatory 'descriptive, not inferential' stamp and zero inferential claims."""
    if payload.get("label") != DESCRIPTIVE_LABEL or payload.get("classification") != DESCRIPTIVE_LABEL:
        raise DescriptiveOnlyViolationError(
            f"Optional EDA attachment must carry label == '{DESCRIPTIVE_LABEL}'."
        )
    if payload.get("inferential_claims") is not False:
        raise DescriptiveOnlyViolationError("Optional EDA attachment must declare inferential_claims=False.")

    def _walk(node: Any) -> None:
        if isinstance(node, Mapping):
            for k, v in node.items():
                ks = str(k).strip().lower()
                if ks in _FORBIDDEN_INFERENTIAL_KEYS:
                    raise DescriptiveOnlyViolationError(
                        f"Forbidden inferential key '{k}' found inside descriptive-only EDA attachment."
                    )
                _walk(v)
        elif isinstance(node, (list, tuple)):
            for item in node:
                _walk(item)
        elif isinstance(node, str):
            low = node.lower()
            for phrase in _FORBIDDEN_INFERENTIAL_PHRASES:
                if phrase in low:
                    raise DescriptiveOnlyViolationError(
                        f"Forbidden inferential claim '{phrase}' found inside descriptive-only EDA attachment."
                    )

    _walk(payload)


@dataclass(frozen=True)
class EDAAttachment:
    """Self-contained automated EDA summary stamped 'descriptive, not inferential'."""

    target_column: str
    predictor_columns: tuple[str, ...]
    n_rows: int
    summary_table: tuple[dict[str, Any], ...]
    missingness_table: tuple[dict[str, Any], ...]
    association_table: tuple[dict[str, Any], ...]
    downsampled_preview: dict[str, Any]
    notes: tuple[str, ...]
    label: str = DESCRIPTIVE_LABEL
    classification: str = DESCRIPTIVE_LABEL
    inferential_claims: bool = False
    disclaimer: str = (
        "Automated exploratory profile — descriptive, not inferential. "
        "Values summarize sample distributions and co-variation only without hypothesis tests or causal claims."
    )

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "association_table": list(self.association_table),
            "classification": DESCRIPTIVE_LABEL,
            "disclaimer": self.disclaimer,
            "downsampled_preview": dict(self.downsampled_preview),
            "inferential_claims": False,
            "label": DESCRIPTIVE_LABEL,
            "missingness_table": list(self.missingness_table),
            "n_rows": self.n_rows,
            "notes": list(self.notes),
            "predictor_columns": list(self.predictor_columns),
            "sections": {
                "associations": {"label": DESCRIPTIVE_LABEL, "rows": list(self.association_table)},
                "missingness": {"label": DESCRIPTIVE_LABEL, "rows": list(self.missingness_table)},
                "univariate_summary": {"label": DESCRIPTIVE_LABEL, "rows": list(self.summary_table)},
            },
            "summary_table": list(self.summary_table),
            "target_column": self.target_column,
        }
        assert_descriptive_only(out)
        return out


def build_optional_eda_attachment(
    df: pd.DataFrame,
    *,
    target_col: str,
    predictor_cols: Sequence[str] | None = None,
    max_preview_points: int = 800,
) -> EDAAttachment:
    """Build an optional automated EDA attachment strictly labelled 'descriptive, not inferential'."""
    if target_col not in df.columns:
        raise ValueError(f"Target column '{target_col}' not found in DataFrame.")
    if predictor_cols is None:
        preds = [
            str(c)
            for c in df.columns
            if c != target_col and pd.api.types.is_numeric_dtype(df[c])
        ]
    else:
        preds = [str(c) for c in predictor_cols if str(c) in df.columns and str(c) != target_col]

    all_cols = [target_col, *preds]
    n_rows = int(len(df))
    summary_rows: list[dict[str, Any]] = []
    missing_rows: list[dict[str, Any]] = []

    for col in all_cols:
        s_num = pd.to_numeric(df[col], errors="coerce")
        n_miss = int(s_num.isna().sum())
        miss_pct = round(100.0 * float(n_miss) / float(max(1, n_rows)), 4)
        missing_rows.append(
            {
                "column": col,
                "label": DESCRIPTIVE_LABEL,
                "missing_count": n_miss,
                "missing_pct": miss_pct,
                "present_count": int(n_rows - n_miss),
            }
        )
        valid = s_num.dropna().to_numpy(dtype=float)
        if valid.size == 0:
            summary_rows.append({"column": col, "count": 0, "label": DESCRIPTIVE_LABEL})
            continue
        q05, q25, q50, q75, q95 = np.quantile(valid, [0.05, 0.25, 0.50, 0.75, 0.95])
        sk = float(sp_stats.skew(valid)) if valid.size >= 3 and np.std(valid) > 0 else 0.0
        ku = float(sp_stats.kurtosis(valid)) if valid.size >= 4 and np.std(valid) > 0 else 0.0
        summary_rows.append(
            {
                "column": col,
                "count": int(valid.size),
                "iqr": round(float(q75 - q25), 6),
                "kurtosis_excess": round(ku, 6),
                "label": DESCRIPTIVE_LABEL,
                "max": round(float(np.max(valid)), 6),
                "mean": round(float(np.mean(valid)), 6),
                "median": round(float(q50), 6),
                "min": round(float(np.min(valid)), 6),
                "p05": round(float(q05), 6),
                "p95": round(float(q95), 6),
                "skewness": round(sk, 6),
                "std": round(float(np.std(valid, ddof=1)) if valid.size > 1 else 0.0, 6),
            }
        )

    assoc_rows: list[dict[str, Any]] = []
    y_ser = pd.to_numeric(df[target_col], errors="coerce")
    for col in preds:
        x_ser = pd.to_numeric(df[col], errors="coerce")
        mask = x_ser.notna() & y_ser.notna()
        n_pair = int(mask.sum())
        if n_pair < 4:
            continue
        xv = x_ser[mask].to_numpy(dtype=float)
        yv = y_ser[mask].to_numpy(dtype=float)
        if np.std(xv) <= 1e-12 or np.std(yv) <= 1e-12:
            pr, sr = 0.0, 0.0
        else:
            pr = float(np.corrcoef(xv, yv)[0, 1])
            sr = float(pd.Series(xv).corr(pd.Series(yv), method="spearman"))
        assoc_rows.append(
            {
                "abs_max_association": round(max(abs(pr), abs(sr)), 6),
                "label": DESCRIPTIVE_LABEL,
                "n_paired": n_pair,
                "sample_pearson_r": round(pr, 6),
                "sample_spearman_rho": round(sr, 6),
                "variable": col,
            }
        )
    assoc_rows.sort(key=lambda r: -float(r["abs_max_association"]))

    yv_clean = y_ser.dropna().to_numpy(dtype=float)
    if yv_clean.size >= 3:
        ds = lttb_downsample(np.arange(yv_clean.size, dtype=float), yv_clean, threshold=max(3, int(max_preview_points)))
        preview = {
            "compression_ratio": round(ds.compression_ratio, 4),
            "label": DESCRIPTIVE_LABEL,
            "method": ds.method,
            "n_input": ds.n_input,
            "n_output": ds.n_output,
        }
    else:
        preview = {"compression_ratio": 1.0, "label": DESCRIPTIVE_LABEL, "method": "raw", "n_input": int(yv_clean.size), "n_output": int(yv_clean.size)}

    notes = (
        f"Classification: {DESCRIPTIVE_LABEL}.",
        f"Profiled {len(all_cols)} numeric column(s) across {n_rows} row(s) without inferential hypothesis testing.",
    )
    att = EDAAttachment(
        target_column=target_col,
        predictor_columns=tuple(preds),
        n_rows=n_rows,
        summary_table=tuple(summary_rows),
        missingness_table=tuple(missing_rows),
        association_table=tuple(assoc_rows),
        downsampled_preview=preview,
        notes=notes,
    )
    assert_descriptive_only(att.to_dict())
    return att
