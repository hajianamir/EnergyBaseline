"""Neural-network regressors (PyTorch, CPU-first / GPU when present) + a sklearn MLP fallback.

All regressors expose ``fit(A, y) / predict(A)`` on chronologically ordered 2-D arrays, use
deterministic seeding, a time-ordered validation tail (last 15 %), early stopping, LR
scheduling, dropout and weight decay, and keep ``history_`` (training curves).
"""
from __future__ import annotations

import copy
import logging
import random

import numpy as np

log = logging.getLogger("energybaseline.nn")
try:  # PyTorch is optional at import time (the app degrades gracefully without it)
    import torch
    import torch.nn as nn

    TORCH_OK = True
except Exception:  # pragma: no cover
    torch = None  # type: ignore
    nn = None  # type: ignore
    TORCH_OK = False

TORCH_ARCHS = ("lstm", "gru", "tcn", "cnn1d", "cnn_lstm", "ae_regressor", "nbeats")
ARCH_LABEL = {"lstm": "LSTM", "gru": "GRU", "tcn": "TCN", "cnn1d": "1D-CNN", "cnn_lstm": "CNN-LSTM",
              "ae_regressor": "Autoencoder regressor", "nbeats": "N-BEATS"}


def torch_available() -> bool:
    return TORCH_OK


def device_name() -> str:
    if not TORCH_OK:
        return "unavailable"
    return "cuda" if torch.cuda.is_available() else "cpu"


def seed_everything(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    if TORCH_OK:
        torch.manual_seed(seed)
        if torch.cuda.is_available():
            torch.cuda.manual_seed_all(seed)


def _windows(Z: np.ndarray, L: int) -> np.ndarray:
    n = len(Z)
    idx = np.arange(n)[:, None] + np.arange(-L + 1, 1)[None, :]
    idx = np.clip(idx, 0, n - 1)  # edge padding for the first L-1 rows
    return Z[idx]


# ============================================================ sklearn MLP (always available)
class SkMLP:
    def __init__(self, hidden=(64, 32), alpha=1e-4, max_epochs=150, patience=12, seed=42, lr=1e-3, batch=128):
        self.hidden, self.alpha, self.max_epochs, self.patience = tuple(hidden), alpha, max_epochs, patience
        self.seed, self.lr, self.batch = seed, lr, batch
        self.history_: dict = {"train_loss": [], "val_loss": []}

    def fit(self, A, y):
        from sklearn.neural_network import MLPRegressor
        A = np.asarray(A, float)
        y = np.asarray(y, float)
        self.mu_, self.sd_ = A.mean(0), np.where(A.std(0) > 0, A.std(0), 1.0)
        self.ymu_, self.ysd_ = y.mean(), (y.std() or 1.0)
        Z, t = (A - self.mu_) / self.sd_, (y - self.ymu_) / self.ysd_
        nval = int(0.15 * len(Z)) if len(Z) >= 60 else 0
        tr, va = slice(0, len(Z) - nval), slice(len(Z) - nval, len(Z))
        net = MLPRegressor(hidden_layer_sizes=self.hidden, alpha=self.alpha, learning_rate_init=self.lr,
                           batch_size=min(self.batch, max(8, len(Z[tr]))), random_state=self.seed, shuffle=True)
        best, best_state, bad = np.inf, None, 0
        for ep in range(self.max_epochs):
            net.partial_fit(Z[tr], t[tr])
            tl = float(np.mean((net.predict(Z[tr]) - t[tr]) ** 2))
            vl = float(np.mean((net.predict(Z[va]) - t[va]) ** 2)) if nval else tl
            self.history_["train_loss"].append(tl)
            self.history_["val_loss"].append(vl)
            if vl < best - 1e-6:
                best, bad = vl, 0
                best_state = (copy.deepcopy(net.coefs_), copy.deepcopy(net.intercepts_))
            else:
                bad += 1
                if bad >= self.patience:
                    break
        if best_state:
            net.coefs_, net.intercepts_ = best_state
        self.net_ = net
        self.epochs_ = len(self.history_["train_loss"])
        return self

    def predict(self, A):
        Z = (np.asarray(A, float) - self.mu_) / self.sd_
        return self.net_.predict(Z) * self.ysd_ + self.ymu_

    @property
    def n_params(self) -> int:
        return int(sum(c.size for c in self.net_.coefs_) + sum(b.size for b in self.net_.intercepts_))

    def describe(self, names: list[str]) -> str:
        sizes = [len(names)] + list(self.hidden) + [1]
        txt = [f"Feed-forward MLP, layers {' -> '.join(map(str, sizes))}, ReLU hidden activations, Adam, L2 alpha={self.alpha}, "
               f"early stopping on a time-ordered validation tail (stopped after {self.epochs_} epochs), {self.n_params} parameters.",
               "ŷ = ysd · ( w_out · ReLU(W2 · ReLU(W1 · z + b1) + b2) + b_out ) + ymu,  z = (x − μ)/σ  (standardised inputs)"]
        if self.n_params <= 400:
            for i, (W, b) in enumerate(zip(self.net_.coefs_, self.net_.intercepts_), 1):
                txt.append(f"W{i} (shape {W.shape}) = {np.round(W, 4).tolist()}  b{i} = {np.round(b, 4).tolist()}")
        else:
            txt.append("Full weights are stored in the exported model artifact (see artifact path).")
        return "\n".join(txt)


# ============================================================ torch modules
if TORCH_OK:

    class _MLP(nn.Module):
        def __init__(s, f, L, h, d):
            super().__init__()
            s.net = nn.Sequential(nn.Flatten(), nn.Linear(f * L, h), nn.ReLU(), nn.Dropout(d), nn.Linear(h, max(2, h // 2)), nn.ReLU(), nn.Linear(max(2, h // 2), 1))

        def forward(s, x):
            return s.net(x).squeeze(-1)

    class _RNN(nn.Module):
        def __init__(s, f, L, h, d, cell):
            super().__init__()
            s.rnn = (nn.LSTM if cell == "lstm" else nn.GRU)(f, h, batch_first=True)
            s.drop, s.out = nn.Dropout(d), nn.Linear(h, 1)

        def forward(s, x):
            o, _ = s.rnn(x)
            return s.out(s.drop(o[:, -1])).squeeze(-1)

    class _CNN(nn.Module):
        def __init__(s, f, L, h, d):
            super().__init__()
            s.net = nn.Sequential(nn.Conv1d(f, h, 3, padding=1), nn.ReLU(), nn.Conv1d(h, h, 3, padding=1), nn.ReLU(), nn.AdaptiveAvgPool1d(1), nn.Flatten(), nn.Dropout(d), nn.Linear(h, 1))

        def forward(s, x):
            return s.net(x.transpose(1, 2)).squeeze(-1)

    class _CausalBlock(nn.Module):
        def __init__(s, cin, cout, dil, d):
            super().__init__()
            s.pad = 2 * dil
            s.c1, s.c2 = nn.Conv1d(cin, cout, 3, padding=s.pad, dilation=dil), nn.Conv1d(cout, cout, 3, padding=s.pad, dilation=dil)
            s.drop = nn.Dropout(d)
            s.res = nn.Conv1d(cin, cout, 1) if cin != cout else nn.Identity()

        def forward(s, x):
            o = torch.relu(s.c1(x)[:, :, :-s.pad])
            o = s.drop(torch.relu(s.c2(o)[:, :, :-s.pad]))
            return torch.relu(o + s.res(x))

    class _TCN(nn.Module):
        def __init__(s, f, L, h, d):
            super().__init__()
            s.blocks = nn.Sequential(_CausalBlock(f, h, 1, d), _CausalBlock(h, h, 2, d), _CausalBlock(h, h, 4, d))
            s.out = nn.Linear(h, 1)

        def forward(s, x):
            return s.out(s.blocks(x.transpose(1, 2))[:, :, -1]).squeeze(-1)

    class _CNNLSTM(nn.Module):
        def __init__(s, f, L, h, d):
            super().__init__()
            s.conv = nn.Sequential(nn.Conv1d(f, h, 3, padding=1), nn.ReLU())
            s.rnn = nn.LSTM(h, h, batch_first=True)
            s.drop, s.out = nn.Dropout(d), nn.Linear(h, 1)

        def forward(s, x):
            o, _ = s.rnn(s.conv(x.transpose(1, 2)).transpose(1, 2))
            return s.out(s.drop(o[:, -1])).squeeze(-1)

    class _AE(nn.Module):
        def __init__(s, f, L, h, d):
            super().__init__()
            b = max(2, h // 2)
            s.enc = nn.Sequential(nn.Flatten(), nn.Linear(f * L, h), nn.ReLU(), nn.Linear(h, b), nn.ReLU())
            s.dec = nn.Sequential(nn.Linear(b, h), nn.ReLU(), nn.Linear(h, f * L))
            s.head = nn.Sequential(nn.Dropout(d), nn.Linear(b, 1))

        def forward(s, x):
            return s.head(s.enc(x)).squeeze(-1)

        def aux_loss(s, x):
            return 0.3 * torch.mean((s.dec(s.enc(x)) - x.flatten(1)) ** 2)

    class _NBeatsBlock(nn.Module):
        def __init__(s, dim, h):
            super().__init__()
            s.fc = nn.Sequential(nn.Linear(dim, h), nn.ReLU(), nn.Linear(h, h), nn.ReLU())
            s.back, s.fore = nn.Linear(h, dim), nn.Linear(h, 1)

        def forward(s, x):
            z = s.fc(x)
            return s.back(z), s.fore(z)

    class _NBeats(nn.Module):
        def __init__(s, f, L, h, d):
            super().__init__()
            s.blocks = nn.ModuleList([_NBeatsBlock(f * L, h) for _ in range(3)])

        def forward(s, x):
            r, out = x.flatten(1), 0
            for b in s.blocks:
                back, fore = b(r)
                r = r - back
                out = out + fore
            return out.squeeze(-1)

    def _make(arch, f, L, h, d):
        if arch == "mlp":
            return _MLP(f, L, h, d)
        if arch in ("lstm", "gru"):
            return _RNN(f, L, h, d, arch)
        return {"cnn1d": _CNN, "tcn": _TCN, "cnn_lstm": _CNNLSTM, "ae_regressor": _AE, "nbeats": _NBeats}[arch](f, L, h, d)


ARCH_MATH = {
    "lstm": "i,f,o = σ(W·[h_{t-1}, x_t] + b); g = tanh(W_g·[h_{t-1}, x_t] + b_g); c_t = f⊙c_{t-1} + i⊙g; h_t = o⊙tanh(c_t); ŷ = w·Dropout(h_T) + b over a window of T steps",
    "gru": "z_t = σ(W_z[h_{t-1},x_t]); r_t = σ(W_r[h_{t-1},x_t]); h̃_t = tanh(W[r_t⊙h_{t-1}, x_t]); h_t = (1−z_t)⊙h_{t-1} + z_t⊙h̃_t; ŷ = w·h_T + b",
    "tcn": "3 residual blocks of causal dilated Conv1d (kernel 3, dilation 1,2,4, ReLU, dropout): H = ReLU(F(x) + x); ŷ = w·H[:, :, T] + b",
    "cnn1d": "H = ReLU(Conv1d_3(ReLU(Conv1d_3(x)))); ŷ = w·GlobalAvgPool(H) + b",
    "cnn_lstm": "H = ReLU(Conv1d_3(x)); (h_1..h_T) = LSTM(H); ŷ = w·h_T + b",
    "ae_regressor": "e = ReLU(W2·ReLU(W1·x_flat)) (bottleneck); x̂ = W4·ReLU(W3·e); ŷ = w·Dropout(e) + b; loss = MSE(y,ŷ) + 0.3·MSE(x̂, x_flat)",
    "nbeats": "3 N-BEATS blocks: z = FC(r_k); r_{k+1} = r_k − Backcast(z); ŷ = Σ_k Forecast(z_k), r_1 = x_flat",
    "mlp": "ŷ = w3·ReLU(W2·ReLU(W1·x_flat + b1) + b2) + b3",
}


class TorchSeqRegressor:
    def __init__(self, arch="lstm", seq_len=12, hidden=32, dropout=0.1, weight_decay=1e-4, lr=2e-3,
                 max_epochs=60, patience=8, batch=128, seed=42):
        if not TORCH_OK:
            raise RuntimeError("PyTorch is not installed")
        self.arch, self.seq_len, self.hidden, self.dropout = arch, seq_len, hidden, dropout
        self.weight_decay, self.lr, self.max_epochs, self.patience, self.batch, self.seed = weight_decay, lr, max_epochs, patience, batch, seed
        self.history_: dict = {"train_loss": [], "val_loss": [], "lr": []}
        self.net_ = None
        self._state = None

    # ---------------------------------------------------------------- fit / predict
    def fit(self, A, y):
        seed_everything(self.seed)
        torch.set_num_threads(max(1, min(4, torch.get_num_threads())))
        dev = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        A = np.asarray(A, float)
        y = np.asarray(y, float)
        self.f_ = A.shape[1]
        self.mu_, self.sd_ = A.mean(0), np.where(A.std(0) > 0, A.std(0), 1.0)
        self.ymu_, self.ysd_ = float(y.mean()), float(y.std() or 1.0)
        W = _windows((A - self.mu_) / self.sd_, self.seq_len).astype(np.float32)
        t = ((y - self.ymu_) / self.ysd_).astype(np.float32)
        n = len(W)
        nval = int(0.15 * n) if n >= 60 else 0
        Xtr, ttr = torch.from_numpy(W[: n - nval]).to(dev), torch.from_numpy(t[: n - nval]).to(dev)
        Xva, tva = torch.from_numpy(W[n - nval:]).to(dev), torch.from_numpy(t[n - nval:]).to(dev)
        net = _make(self.arch, self.f_, self.seq_len, self.hidden, self.dropout).to(dev)
        opt = torch.optim.Adam(net.parameters(), lr=self.lr, weight_decay=self.weight_decay)
        sched = torch.optim.lr_scheduler.ReduceLROnPlateau(opt, factor=0.5, patience=3)
        g = torch.Generator().manual_seed(self.seed)
        best, best_state, bad = float("inf"), None, 0
        for ep in range(self.max_epochs):
            net.train()
            perm = torch.randperm(len(Xtr), generator=g)
            tot = 0.0
            for i in range(0, len(perm), self.batch):
                b = perm[i:i + self.batch].to(dev)
                opt.zero_grad()
                loss = torch.mean((net(Xtr[b]) - ttr[b]) ** 2)
                if hasattr(net, "aux_loss"):
                    loss = loss + net.aux_loss(Xtr[b])
                loss.backward()
                torch.nn.utils.clip_grad_norm_(net.parameters(), 5.0)
                opt.step()
                tot += float(loss.detach()) * len(b)
            net.eval()
            with torch.no_grad():
                vl = float(torch.mean((net(Xva) - tva) ** 2)) if nval else tot / len(Xtr)
            self.history_["train_loss"].append(tot / len(Xtr))
            self.history_["val_loss"].append(vl)
            self.history_["lr"].append(opt.param_groups[0]["lr"])
            sched.step(vl)
            if vl < best - 1e-6:
                best, bad = vl, 0
                best_state = {k: v.detach().cpu().clone() for k, v in net.state_dict().items()}
            else:
                bad += 1
                if bad >= self.patience:
                    break
        if best_state:
            net.load_state_dict(best_state)
        self.net_ = net.cpu().eval()
        self.epochs_ = len(self.history_["train_loss"])
        self.best_val_ = best
        return self

    def _ensure(self):
        if self.net_ is None and self._state is not None:
            net = _make(self.arch, self.f_, self.seq_len, self.hidden, self.dropout)
            net.load_state_dict({k: torch.from_numpy(v) for k, v in self._state.items()})
            self.net_ = net.eval()

    def predict(self, A):
        self._ensure()
        W = _windows((np.asarray(A, float) - self.mu_) / self.sd_, self.seq_len).astype(np.float32)
        out = []
        with torch.no_grad():
            for i in range(0, len(W), 4096):
                out.append(self.net_(torch.from_numpy(W[i:i + 4096])).numpy())
        return np.concatenate(out) * self.ysd_ + self.ymu_

    # ---------------------------------------------------------------- pickling
    def __getstate__(self):
        d = self.__dict__.copy()
        if self.net_ is not None:
            d["_state"] = {k: v.detach().cpu().numpy() for k, v in self.net_.state_dict().items()}
        d["net_"] = None
        return d

    @property
    def n_params(self) -> int:
        self._ensure()
        return int(sum(p.numel() for p in self.net_.parameters()))

    def describe(self, names: list[str]) -> str:
        self._ensure()
        return (f"{ARCH_LABEL.get(self.arch, self.arch)} network, input window T={self.seq_len} steps × {len(names)} standardised features "
                f"({', '.join(names[:12])}{' …' if len(names) > 12 else ''}); hidden width {self.hidden}, dropout {self.dropout}, weight decay {self.weight_decay}, "
                f"Adam + ReduceLROnPlateau, early stopping on a time-ordered validation tail (stopped after {self.epochs_} epochs, best val MSE={self.best_val_:.4f} in standardised units), "
                f"{self.n_params} trained parameters, device={device_name()}.\n"
                f"Mathematical form: {ARCH_MATH.get(self.arch, '')}\n"
                f"Targets are standardised: ŷ_final = ysd·ŷ + ymu (ymu={self.ymu_:.6g}, ysd={self.ysd_:.6g}). "
                f"Trained weights are exported in the model artifact (see artifact path).")
