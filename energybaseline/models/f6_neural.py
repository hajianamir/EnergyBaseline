"""F6 — Neural baselines: scikit-learn MLPRegressor (always available) + PyTorch CPU LSTM (Full profile; clean Lite skip)."""

from __future__ import annotations

import copy
import random
from typing import Any

import numpy as np
import pandas as pd
from sklearn.neural_network import MLPRegressor

from ..core.nn import TORCH_OK, torch
from .base import (
    OptionalBackendSkippedError,
    SampleSizeGateSkippedError,
    compute_baseline_metrics,
    prepare_design_frame,
)

SUPPORTED_F6_VARIANTS = ("mlp", "lstm")


def _sliding_windows(Z: np.ndarray, seq_len: int) -> np.ndarray:
    """Construct causal sliding windows [t - seq_len + 1 .. t] with leading edge hold."""
    n = Z.shape[0]
    idx = np.clip(np.arange(n)[:, None] + np.arange(-seq_len + 1, 1)[None, :], 0, n - 1)
    return Z[idx]


class F6NeuralBaseline:
    """Sample-size-gated neural energy baseline supporting deterministic MLP and PyTorch CPU LSTM."""

    family_id = "F6"
    family_name = "Neural Baselines (MLP / PyTorch LSTM)"

    def __init__(
        self,
        variant: str = "mlp",
        *,
        min_samples: int = 60,
        hidden_sizes: tuple[int, ...] = (48, 24),
        seq_len: int = 6,
        max_epochs: int = 120,
        lr: float = 4e-3,
        alpha: float = 1e-3,
        force_lite_profile: bool = False,
        seed: int = 42,
    ) -> None:
        v = str(variant).strip().lower()
        if v not in SUPPORTED_F6_VARIANTS:
            raise ValueError(f"Unsupported F6 variant '{variant}'; expected one of {SUPPORTED_F6_VARIANTS}.")
        self.variant = v
        self.min_samples = max(20, int(min_samples))
        self.hidden_sizes = tuple(int(h) for h in hidden_sizes)
        self.seq_len = max(2, int(seq_len))
        self.max_epochs = max(15, int(max_epochs))
        self.lr = float(np.clip(lr, 1e-4, 5e-2))
        self.alpha = float(max(0.0, alpha))
        self.force_lite_profile = bool(force_lite_profile)
        self.seed = int(seed)

        self.feature_names_: list[str] = []
        self.history_: dict[str, list[float]] = {"train_loss": [], "val_loss": []}
        self.n_params_: int = 0
        self.train_metrics_: dict[str, float] = {}

    def fit(self, X: Any, y: Any) -> "F6NeuralBaseline":
        X_df, cols = prepare_design_frame(X)
        yv = np.asarray(y, dtype=float).reshape(-1)
        n = int(yv.size)
        if len(X_df) != n:
            raise ValueError("X and y must have identical row counts.")
        if n < self.min_samples:
            raise SampleSizeGateSkippedError(
                f"Sample size n={n} is below neural baseline minimum min_samples={self.min_samples}."
            )
        if self.variant == "lstm" and (self.force_lite_profile or not TORCH_OK):
            raise OptionalBackendSkippedError(
                "PyTorch not installed or Lite profile active — LSTM skipped cleanly."
            )

        self.feature_names_ = cols
        medians = X_df.median(numeric_only=True).fillna(0.0)
        self._impute_medians_ = medians.to_dict()
        X_mat = X_df.fillna(medians).fillna(0.0).to_numpy(dtype=float)

        self._mu_ = X_mat.mean(axis=0)
        self._sd_ = np.where(X_mat.std(axis=0, ddof=0) > 1e-12, X_mat.std(axis=0, ddof=0), 1.0)
        self._ymu_ = float(yv.mean())
        self._ysd_ = float(yv.std(ddof=0)) if float(yv.std(ddof=0)) > 1e-12 else 1.0

        Z = (X_mat - self._mu_) / self._sd_
        t_norm = (yv - self._ymu_) / self._ysd_
        n_val = max(6, int(round(0.15 * n)))
        n_tr = n - n_val
        self.history_ = {"train_loss": [], "val_loss": []}

        if self.variant == "mlp":
            net = MLPRegressor(
                hidden_layer_sizes=self.hidden_sizes,
                activation="relu",
                solver="adam",
                alpha=self.alpha,
                learning_rate_init=self.lr,
                batch_size=min(64, max(8, n_tr // 2)),
                random_state=self.seed,
                shuffle=True,
            )
            best_val = float("inf")
            best_weights: tuple[Any, Any] | None = None
            stale = 0
            for _ in range(self.max_epochs):
                net.partial_fit(Z[:n_tr], t_norm[:n_tr])
                tr_l = float(np.mean((net.predict(Z[:n_tr]) - t_norm[:n_tr]) ** 2))
                va_l = float(np.mean((net.predict(Z[n_tr:]) - t_norm[n_tr:]) ** 2))
                self.history_["train_loss"].append(tr_l)
                self.history_["val_loss"].append(va_l)
                if va_l < best_val - 1e-6:
                    best_val = va_l
                    best_weights = (copy.deepcopy(net.coefs_), copy.deepcopy(net.intercepts_))
                    stale = 0
                else:
                    stale += 1
                    if stale >= 14:
                        break
            if best_weights is not None:
                net.coefs_, net.intercepts_ = best_weights
            self._mlp_ = net
            self.n_params_ = int(sum(c.size for c in net.coefs_) + sum(b.size for b in net.intercepts_))
            yhat = net.predict(Z) * self._ysd_ + self._ymu_
        else:
            assert torch is not None
            import torch.nn as tnn

            random.seed(self.seed)
            np.random.seed(self.seed)
            torch.manual_seed(self.seed)

            W = _sliding_windows(Z, self.seq_len)
            X_tr_t = torch.tensor(W[:n_tr], dtype=torch.float32)
            y_tr_t = torch.tensor(t_norm[:n_tr], dtype=torch.float32)
            X_va_t = torch.tensor(W[n_tr:], dtype=torch.float32)
            y_va_t = torch.tensor(t_norm[n_tr:], dtype=torch.float32)

            h_dim = int(self.hidden_sizes[0])

            class _SeqLSTM(tnn.Module):
                def __init__(self, in_dim: int, hid: int) -> None:
                    super().__init__()
                    self.lstm = tnn.LSTM(input_size=in_dim, hidden_size=hid, batch_first=True)
                    self.head = tnn.Linear(hid, 1)

                def forward(self, x_seq: Any) -> Any:
                    out, _ = self.lstm(x_seq)
                    return self.head(out[:, -1, :]).squeeze(-1)

            model = _SeqLSTM(Z.shape[1], h_dim)
            opt = torch.optim.Adam(model.parameters(), lr=self.lr, weight_decay=self.alpha)
            loss_fn = tnn.MSELoss()

            best_val = float("inf")
            best_state: dict[str, Any] | None = None
            stale = 0
            for _ in range(self.max_epochs):
                model.train()
                opt.zero_grad()
                pred_tr = model(X_tr_t)
                loss = loss_fn(pred_tr, y_tr_t)
                loss.backward()
                opt.step()

                model.eval()
                with torch.no_grad():
                    tr_l = float(loss.item())
                    va_l = float(loss_fn(model(X_va_t), y_va_t).item())
                self.history_["train_loss"].append(tr_l)
                self.history_["val_loss"].append(va_l)
                if va_l < best_val - 1e-6:
                    best_val = va_l
                    best_state = copy.deepcopy(model.state_dict())
                    stale = 0
                else:
                    stale += 1
                    if stale >= 14:
                        break

            if best_state is not None:
                model.load_state_dict(best_state)
            model.eval()
            self._lstm_ = model
            self.n_params_ = int(sum(p.numel() for p in model.parameters()))
            with torch.no_grad():
                yhat_norm = model(torch.tensor(W, dtype=torch.float32)).cpu().numpy()
            yhat = yhat_norm * self._ysd_ + self._ymu_

        self.train_metrics_ = compute_baseline_metrics(yv, yhat)
        return self

    def predict(self, X: Any) -> np.ndarray:
        X_df, _ = prepare_design_frame(X, self.feature_names_)
        X_mat = X_df.fillna(self._impute_medians_).fillna(0.0).to_numpy(dtype=float)
        Z = (X_mat - self._mu_) / self._sd_
        if self.variant == "mlp":
            return np.asarray(self._mlp_.predict(Z) * self._ysd_ + self._ymu_, dtype=float).reshape(-1)
        assert torch is not None
        W = _sliding_windows(Z, self.seq_len)
        self._lstm_.eval()
        with torch.no_grad():
            pred_norm = self._lstm_(torch.tensor(W, dtype=torch.float32)).cpu().numpy()
        return np.asarray(pred_norm * self._ysd_ + self._ymu_, dtype=float).reshape(-1)

    def predict_series(self, X: pd.DataFrame, *, drop_warmup: bool = False) -> pd.Series:
        """Return timestamp-aligned prediction Series (optionally dropping seq_len-1 warm-up steps for LSTM)."""
        preds = self.predict(X)
        ser = pd.Series(preds, index=X.index, name=f"F6_{self.variant}")
        if drop_warmup and self.variant == "lstm" and len(ser) > self.seq_len:
            return ser.iloc[self.seq_len - 1 :]
        return ser

    @property
    def n_params(self) -> int:
        return self.n_params_

    def formula_text(self) -> str:
        if self.variant == "mlp":
            return f"MLPRegressor(layers={self.hidden_sizes}, alpha={self.alpha}, params={self.n_params_})"
        return f"PyTorchLSTM(hidden={self.hidden_sizes[0]}, seq_len={self.seq_len}, params={self.n_params_}, cpu)"
