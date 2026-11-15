"""Raw-data fold preparation shared by candidate evaluation and nested tuning.

The evaluation population is fixed before learned quality filtering. Validation
labels never enter a fitted quality policy, feature specification or selector.
Only the final deployment fit sees all available calibration labels.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from collections import OrderedDict
import hashlib
from typing import Any

import numpy as np
import pandas as pd

from .auto import AutoLog, is_auto
from .features import build_spec, select_features, validate_memory
from .quality import apply_non_routine_events, preprocess_target
from .schema import coerce_dataset_schema
from ..validation.purge import compute_purge_gap_spec


@dataclass
class PreparedFold:
    train_X: pd.DataFrame
    train_y: np.ndarray
    valid_X: pd.DataFrame
    train_positions: np.ndarray
    valid_positions: np.ndarray
    raw_names: list[str]
    temp_col: str | None
    audit: dict[str, Any]


class RawEvaluationPlan:
    """Immutable input snapshot and cached training-only fold transformations.

    Fold positions address finite target observations, while lag/rolling features
    use the original temporal grid, including rows with missing target labels.
    Validation predictors may supply observed causal history; validation targets
    are removed before every transformation.
    """

    def __init__(self, frame, target, all_targets, granularity, config, seed, *, schema=None, cancel=None):
        self.config = dict(config)
        self.cancel = cancel or (lambda: False)
        budget = self.config.get("evaluation_cache_mb", 128)
        budget = 128 if is_auto(budget) else float(budget)
        if not np.isfinite(budget) or budget < 0:
            raise ValueError("evaluation_cache_mb must be finite and nonnegative.")
        self.cache_budget_bytes = int(budget * 1024 * 1024)
        self.cache_bytes = 0
        self.cache_hits = 0
        self.cache_evictions = 0
        self.target, self.all_targets = target, list(all_targets)
        self.g, self.seed = granularity, seed
        self.schema = coerce_dataset_schema(schema)
        if (
            not isinstance(frame.index, pd.DatetimeIndex)
            or not frame.index.is_unique
            or not frame.index.is_monotonic_increasing
        ):
            raise ValueError("Evaluation input requires a unique, increasing DatetimeIndex.")
        source = frame.copy(deep=True)
        nres = (
            self.schema.non_routine_events
            if self.schema is not None and self.schema.non_routine_events
            else self.config.get("non_routine_events", ())
        )
        if nres:
            source, _, _ = apply_non_routine_events(source, nres, target=target)
        self.frame = source
        numeric_y = pd.to_numeric(source[target], errors="coerce")
        if np.isinf(numeric_y.to_numpy(dtype=float)).any():
            raise ValueError("Infinite target observations are not valid evaluation labels.")
        self.positions = np.flatnonzero(numeric_y.notna().to_numpy())
        self.index = source.index[self.positions]
        self.y = numeric_y.iloc[self.positions].to_numpy(dtype=float)
        self.cache: OrderedDict[tuple, PreparedFold] = OrderedDict()
        self.folds: list[tuple[np.ndarray, np.ndarray]] = []

    def purge_spec(self):
        """Use upper bounds on AUTO memory, never a full-data selected lag."""

        def memory(key, automatic):
            raw = self.config.get(key)
            if is_auto(raw):
                return automatic if len(self.y) >= 60 else 0
            return max([0, *validate_memory(raw, setting=key)])

        spd = self.g.steps_per_day
        lag = memory("lags", max(3, int(spd)))
        roll = memory("rolling_windows", max(3, int(spd * 3)))
        return compute_purge_gap_spec(steps_per_day=spd, max_lag=lag, max_rolling_window=roll)

    @staticmethod
    def _fold_bytes(fold):
        return int(
            fold.train_X.memory_usage(index=True, deep=True).sum()
            + fold.valid_X.memory_usage(index=True, deep=True).sum()
            + fold.train_y.nbytes
            + fold.train_positions.nbytes
            + fold.valid_positions.nbytes
        )

    def check_cancelled(self):
        if self.cancel():
            raise InterruptedError("Evaluation cancelled before the next bounded stage.")

    def prepare(self, train, valid) -> PreparedFold:
        self.check_cancelled()

        def checked(values):
            values = np.asarray(values)
            if values.ndim != 1 or values.dtype.kind not in "iu" or not len(values):
                raise ValueError("Fold positions must be nonempty one-dimensional integer arrays.")
            if (values < 0).any() or (values >= len(self.y)).any() or len(np.unique(values)) != len(values):
                raise ValueError("Fold positions must be unique and within the evaluation population.")
            return np.sort(values.astype(int))

        train, valid = checked(train), checked(valid)
        if np.intersect1d(train, valid).size:
            raise ValueError("Training and validation positions must be disjoint.")
        key = (tuple(train), tuple(valid))
        if key in self.cache:
            self.cache_hits += 1
            self.cache.move_to_end(key)
            return self.cache[key]
        if not len(train) or not len(valid):
            raise ValueError("Both training and validation observations are required.")
        train_raw = self.frame.iloc[self.positions[train]].copy()
        log = AutoLog(self.config, self.target)
        prep = preprocess_target(train_raw, self.target, self.all_targets, self.g, log, self.seed, schema=self.schema)
        from ..mining.clustering import strip_discovery_cluster_labels

        clean, _ = strip_discovery_cluster_labels(prep.df)
        clean = clean.loc[clean[self.target].notna()].copy()
        if len(clean) < 8:
            raise ValueError("Fewer than eight training observations remain inside a fold.")
        self.check_cancelled()
        spec, _, _ = build_spec(clean, self.target, prep.predictors, prep.cat_cols, self.g, log)
        # Preserve actual temporal spacing instead of compressing gaps between
        # training segments. No target column is passed to FeatureSpec.build.
        end = int(max(self.positions[train].max(), self.positions[valid].max())) + 1
        history = self.frame.iloc[:end].drop(columns=self.all_targets, errors="ignore").copy()
        for column in prep.predictors:
            # pandas 3 disallows silently assigning imputed/clipped fractional
            # values into integer source columns. This is a numeric model matrix.
            history[column] = (
                pd.to_numeric(history[column], errors="coerce").astype(float) if column in history else np.nan
            )
            history.loc[clean.index, column] = pd.to_numeric(clean[column], errors="coerce").astype(float)
        for column in prep.cat_cols:
            history[column] = history[column].astype(object)
            history.loc[clean.index, column] = clean[column].astype(object)
        self.check_cancelled()
        expanded = spec.build(history)
        train_X = expanded.loc[clean.index]
        train_y = clean[self.target].to_numpy(dtype=float)
        selected = select_features(train_X, pd.Series(train_y, index=train_X.index), log, self.seed, [])
        if spec.temp_col and spec.temp_col in train_X and spec.temp_col not in selected:
            selected.append(spec.temp_col)
        selected = [column for column in train_X if column in set(selected)]
        train_X = train_X[selected]
        # The deployment transform's full-calibration medians are not used here.
        medians = train_X.median(numeric_only=True).fillna(0.0)
        valid_X = expanded.loc[self.index[valid], selected].fillna(medians).fillna(0.0)
        train_X = train_X.fillna(medians).fillna(0.0)
        fingerprint = hashlib.sha256(pd.util.hash_pandas_object(train_raw, index=True).values.tobytes()).hexdigest()
        audit = {
            "train_input_sha256": fingerprint,
            "n_train_input": len(train),
            "n_train_fit": len(train_y),
            "n_valid": len(valid),
            "selected_features": selected,
            "medians": {str(c): float(v) for c, v in medians.items()},
            "heating_balance": spec.balance_heat,
            "cooling_balance": spec.balance_cool,
            "category_levels": spec.cat_levels,
            "auto": log.as_dicts(),
        }
        fold = PreparedFold(train_X, train_y, valid_X, train, valid, list(spec.raw_cols), spec.temp_col, audit)
        size = self._fold_bytes(fold)
        # Bound retained matrix memory; zero disables caching. Transient fit
        # allocations and audit dictionaries are outside this matrix budget.
        if size <= self.cache_budget_bytes:
            while self.cache and self.cache_bytes + size > self.cache_budget_bytes:
                _, removed = self.cache.popitem(last=False)
                self.cache_bytes -= self._fold_bytes(removed)
                self.cache_evictions += 1
            self.cache[key] = fold
            self.cache_bytes += size
        return fold

    def aligned(self, predictions, index):
        return pd.Series(predictions, index=self.index).reindex(index).to_numpy(dtype=float)

    def inner_folds(self, train, ctx):
        from .models import make_folds

        train = np.asarray(train, dtype=int)
        # No stride-based gap conversion and no small-sample gap clipping.
        for itr, ite in make_folds(len(train), 3, ctx.cv_scheme, ctx.gap):
            yield self.prepare(train[itr], train[ite])

    def fit(self, candidate, X, y, ctx, train_positions):
        """Tune with independently prepared inner folds, then fit supplied training data."""
        params, note = {}, "HPO skipped (n<80, trials=0, or parameter-free candidate)"
        if candidate.space is not None and ctx.hpo_trials > 0 and len(train_positions) >= 80:
            prepared = list(self.inner_folds(train_positions, ctx))
            if prepared:
                import optuna

                optuna.logging.set_verbosity(optuna.logging.WARNING)
                study = optuna.create_study(direction="minimize", sampler=optuna.samplers.TPESampler(seed=ctx.seed))

                def objective(trial):
                    if ctx.cancel():
                        raise InterruptedError("cancelled")
                    values = candidate.space(trial)
                    errors = []
                    for fold in prepared:
                        self.check_cancelled()
                        if ctx.cancel():
                            raise InterruptedError("cancelled")
                        local = replace(ctx, raw_names=fold.raw_names, temp_col=fold.temp_col)
                        model = candidate.fit(fold.train_X, fold.train_y, local, tune=False, params_override=values)
                        errors.append(float(np.mean((model.predict(fold.valid_X) - self.y[fold.valid_positions]) ** 2)))
                    return float(np.mean(errors))

                study.optimize(objective, n_trials=ctx.hpo_trials, show_progress_bar=False)
                params, note = (
                    study.best_params,
                    f"Optuna TPE, {ctx.hpo_trials} trials, {len(prepared)} independently prepared inner folds",
                )
        model = candidate.fit(X, y, ctx, tune=False, params_override=params)
        model.hpo_note = note
        return model

    def ensemble_predictions(self, first, second, ctx):
        """Fit each outer weight vector from independently prepared inner OOF only."""
        from ..models.ensemble import fit_nnls_weights_on_inner_oof

        out = np.full(len(self.y), np.nan)
        weights = []
        for train, valid in self.folds:
            self.check_cancelled()
            if ctx.cancel():
                raise InterruptedError("cancelled")
            inner = list(self.inner_folds(train, ctx))
            if not inner:
                return np.full(len(self.y), np.nan), []
            predictions = np.full((len(self.y), 2), np.nan)
            for fold in inner:
                self.check_cancelled()
                if ctx.cancel():
                    raise InterruptedError("cancelled")
                local = replace(ctx, raw_names=fold.raw_names, temp_col=fold.temp_col)
                for j, candidate in enumerate((first, second)):
                    model = candidate.fit(fold.train_X, fold.train_y, local, tune=False)
                    predictions[fold.valid_positions, j] = model.predict(fold.valid_X)
            w, _ = fit_nnls_weights_on_inner_oof(
                predictions[train], self.y[train], normalize_simplex=True, enforce_k_max=False
            )
            weights.append(w)
            outer = self.prepare(train, valid)
            local = replace(ctx, raw_names=outer.raw_names, temp_col=outer.temp_col)
            pair = [
                candidate.fit(outer.train_X, outer.train_y, local, tune=False).predict(outer.valid_X)
                for candidate in (first, second)
            ]
            out[valid] = float(w[0]) * pair[0] + float(w[1]) * pair[1]
        return out, weights
