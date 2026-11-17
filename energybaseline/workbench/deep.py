"""Small CPU reference forecasting architectures; scratch training, fixed-origin holdout and real NAS.

These are explicitly reduced reference variants, not pretrained foundation models or
claims of feature parity with the named research implementations.
"""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
import threading

import numpy as np

from .common import chronological, digest, fields, integer, number, numeric

ARCHITECTURES = ("patchtst", "informer", "tft", "nhits", "tide", "deepar")
_TORCH_LOCK = threading.RLock()


@contextmanager
def torch_scope(seed):
    import torch

    # CPU RNG/thread settings are process-global. Serialize library calls and restore them.
    with _TORCH_LOCK:
        threads = torch.get_num_threads()
        with torch.random.fork_rng(devices=[]):
            try:
                torch.set_num_threads(1)
                torch.manual_seed(seed)
                yield torch
            finally:
                torch.set_num_threads(threads)


def make_network(architecture, features, context, horizon, width):
    import torch
    from torch import nn
    from torch.nn import functional as F

    if architecture not in ARCHITECTURES or width % 2:
        raise ValueError("Unknown reference architecture or invalid attention width.")

    class Network(nn.Module):
        def __init__(self):
            super().__init__()
            self.architecture = architecture
            self.horizon = horizon
            if architecture in ("patchtst", "informer"):
                self.position = nn.Parameter(torch.randn(1, context + horizon, width) * 0.02)
            if architecture == "informer":
                self.projection = nn.Linear(features, width)
            if architecture == "patchtst":
                self.patch = min(4, context)
                self.patch_projection = nn.Linear(self.patch, width)
                self.encoder = nn.TransformerEncoder(
                    nn.TransformerEncoderLayer(width, 2, width * 2, dropout=0, batch_first=True),
                    1,
                    enable_nested_tensor=False,
                )
                self.head = nn.Linear(width, horizon)
            elif architecture == "informer":
                self.query = nn.Linear(width, width)
                self.key = nn.Linear(width, width)
                self.value = nn.Linear(width, width)
                self.distill = nn.Conv1d(width, width, 3, padding=1)
                self.decoder = nn.TransformerDecoder(
                    nn.TransformerDecoderLayer(width, 2, width * 2, dropout=0, batch_first=True), 1
                )
                self.head = nn.Linear(width, 1)
            elif architecture == "tft":
                self.variables = nn.ModuleList([nn.Linear(1, width) for _ in range(features)])
                self.selection = nn.Sequential(nn.Linear(features, width), nn.ELU(), nn.Linear(width, features))
                self.rnn = nn.LSTM(width, width, batch_first=True)
                self.gate = nn.Linear(width, width * 2)
                self.norm = nn.LayerNorm(width)
                self.attention = nn.MultiheadAttention(width, 2, dropout=0, batch_first=True)
                self.head = nn.Linear(width, horizon)
            elif architecture == "nhits":
                self.blocks = nn.ModuleList()
                for rate in (1, 2, 4):
                    pooled = (context + rate - 1) // rate
                    knots = max(1, horizon // rate)
                    self.blocks.append(
                        nn.Sequential(
                            nn.Linear(pooled, width),
                            nn.ReLU(),
                            nn.Linear(width, width),
                            nn.ReLU(),
                            nn.Linear(width, context + knots),
                        )
                    )
            elif architecture == "tide":
                self.encoder_input = nn.Linear(context * features, width)
                self.encoder = nn.Sequential(nn.Linear(width, width), nn.ReLU(), nn.Linear(width, width))
                self.encoder_norm = nn.LayerNorm(width)
                self.decoder = nn.Linear(width, horizon * width)
                self.temporal = nn.Sequential(nn.Linear(width, width), nn.ReLU(), nn.Linear(width, 1))
                self.temporal_skip = nn.Linear(width, 1)
                self.skip = nn.Linear(context, horizon)
            else:
                self.rnn = nn.LSTM(1, width, batch_first=True)
                self.distribution = nn.Linear(width, 2)

        def forward(self, x, future=None):
            if architecture == "patchtst":
                # Channel-independent patches with shared attention parameters; target channel readout.
                patches = x.transpose(1, 2)[:, :, -(context // self.patch) * self.patch :].unfold(
                    -1, self.patch, self.patch
                )
                b, c, count, size = patches.shape
                z = self.patch_projection(patches.reshape(b * c, count, size)) + self.position[:, :count]
                encoded = self.encoder(z).mean(dim=1)
                return self.head(encoded).reshape(b, c, horizon)[:, 0], None
            if architecture == "nhits":
                residual = x[:, :, 0]
                forecast = torch.zeros(len(x), horizon, device=x.device)
                for rate, block in zip((1, 2, 4), self.blocks):
                    pooled = F.max_pool1d(residual[:, None], rate, stride=rate, ceil_mode=True).squeeze(1)
                    theta = block(pooled)
                    residual = residual - theta[:, :context]
                    knots = theta[:, context:]
                    forecast = forecast + F.interpolate(
                        knots[:, None], size=horizon, mode="linear", align_corners=False
                    ).squeeze(1)
                return forecast, None
            if architecture == "tide":
                projected = self.encoder_input(x.flatten(1))
                code = self.encoder_norm(projected + self.encoder(projected))
                decoded = self.decoder(code).reshape(len(x), horizon, width)
                return (self.temporal(decoded) + self.temporal_skip(decoded)).squeeze(-1) + self.skip(x[:, :, 0]), None
            if architecture == "deepar":
                _, state = self.rnn(x[:, :, :1])
                means, scales = [], []
                for step in range(horizon):
                    distribution = self.distribution(state[0][-1])
                    mean, scale = distribution[:, 0], F.softplus(distribution[:, 1]) + 1e-4
                    means.append(mean)
                    scales.append(scale)
                    feedback = future[:, step] if future is not None else mean
                    _, state = self.rnn(feedback[:, None, None], state)
                return torch.stack(means, 1), torch.stack(scales, 1)
            if architecture == "tft":
                transformed = torch.stack([layer(x[:, :, i : i + 1]) for i, layer in enumerate(self.variables)], dim=2)
                weights = self.selection(x).softmax(dim=-1)
                selected = (transformed * weights[:, :, :, None]).sum(dim=2)
                recurrent, _ = self.rnn(selected)
                z = self.norm(selected + F.glu(self.gate(recurrent), dim=-1))
                attended, _ = self.attention(z, z, z, need_weights=False)
                return self.head(self.norm(z + attended)[:, -1]), None
            # ProbSparse-style top-query attention, encoder distillation and one-shot masked decoder.
            z = self.projection(x) + self.position[:, :context]
            scores = self.query(z) @ self.key(z).transpose(1, 2) / width**0.5
            sparsity = scores.max(dim=-1).values - scores.mean(dim=-1)
            count = min(context, max(1, int(np.ceil(np.log(context + 1)))))
            selected = sparsity.topk(count, dim=-1).indices
            values = self.value(z)
            updates = torch.softmax(scores.gather(1, selected[:, :, None].expand(-1, -1, context)), dim=-1) @ values
            attended = values.mean(dim=1, keepdim=True).expand_as(values).clone()
            attended = attended.scatter(1, selected[:, :, None].expand(-1, -1, width), updates)
            memory = F.max_pool1d(F.elu(self.distill((z + attended).transpose(1, 2))), 2, ceil_mode=True).transpose(
                1, 2
            )
            label = max(1, context // 2)
            tokens = torch.cat([z[:, -label:], torch.zeros(len(x), horizon, width, device=x.device)], dim=1)
            tokens = tokens + self.position[:, : label + horizon]
            mask = torch.full((label + horizon, label + horizon), float("-inf"), device=x.device).triu(1)
            return self.head(self.decoder(tokens, memory, tgt_mask=mask)[:, -horizon:]).squeeze(-1), None

    return Network()


def state_digest(net):
    result = hashlib.sha256()
    for name, tensor in sorted(net.state_dict().items()):
        result.update(name.encode())
        result.update(tensor.detach().cpu().numpy().tobytes())
    return result.hexdigest()


def fit_forecast(values, horizon, *, architecture, context, width, epochs, seed, learning_rate):
    import torch

    if architecture in ("nhits", "deepar") and values.shape[1] != 1:
        raise ValueError("N-HiTS/DeepAR reference variants are univariate, without exogenous inputs.")
    center, scale = values.mean(axis=0), values.std(axis=0)
    scale = np.where(scale > 1e-12, scale, 1)
    z = (values - center) / scale
    ends = np.arange(context, len(values) - horizon + 1)
    if len(ends) < 8 or len(ends) * context * values.shape[1] > 1_000_000:
        raise ValueError("Insufficient or excessive complete training windows.")
    X = np.stack([z[end - context : end] for end in ends]).astype("float32")
    Y = np.stack([z[end : end + horizon, 0] for end in ends]).astype("float32")
    with torch_scope(seed):
        net = make_network(architecture, values.shape[1], context, horizon, width)
        optimizer = torch.optim.AdamW(net.parameters(), lr=learning_rate)
        x, y = torch.from_numpy(X), torch.from_numpy(Y)
        history = []
        for _ in range(epochs):
            net.train()
            total = 0.0
            for batch in torch.randperm(len(x)).split(64):
                optimizer.zero_grad()
                prediction, sd = net(x[batch], y[batch] if architecture == "deepar" else None)
                loss = (
                    torch.mean(((prediction - y[batch]) / sd) ** 2 / 2 + sd.log())
                    if sd is not None
                    else torch.mean((prediction - y[batch]) ** 2)
                )
                if not torch.isfinite(loss):
                    raise ValueError("Nonfinite neural training objective.")
                loss.backward()
                torch.nn.utils.clip_grad_norm_(net.parameters(), 1.0)
                optimizer.step()
                total += float(loss.detach()) * len(batch)
            history.append(total / len(x))
        net.eval()
        with torch.no_grad():
            forecast, _ = net(torch.from_numpy(z[-context:].astype("float32"))[None])
        forecast = forecast.numpy()[0] * scale[0] + center[0]
        weights = state_digest(net)
    if not np.isfinite(forecast).all():
        raise ValueError("Nonfinite fixed-origin forecast.")
    return forecast, {
        "weight_sha256": weights,
        "parameter_count": sum(p.numel() for p in net.parameters()),
        "training_windows": len(ends),
        "first_training_loss": history[0],
        "last_training_loss": history[-1],
    }


def deep_forecast(frame, spec):
    fields(
        spec,
        ("time", "target", "train_rows", "architecture", "seed"),
        ("features", "context", "width", "epochs", "learning_rate", "search"),
    )
    times = chronological(frame, spec["time"])
    if times.diff().dropna().nunique() != 1:
        raise ValueError("Deep forecasting requires regular timestamps.")
    features = spec.get("features", [])
    if not isinstance(features, list) or spec["target"] in features or spec["time"] in features:
        raise ValueError("Historical-only covariates must exclude target and timestamps.")
    values = numeric(frame, [spec["target"]] + features)
    split = integer(spec["train_rows"], "train_rows", 32, len(frame) - 1)
    horizon = integer(len(frame) - split, "horizon", 1, 48)
    if split > 2000 or values.shape[1] > 8:
        raise ValueError("Deep training accepts at most 2000 rows and eight historical channels.")
    context = integer(spec.get("context", 12), "context", 4, 96)
    width = integer(spec.get("width", 16), "width", 4, 64)
    if width % 2:
        raise ValueError("Attention width must be even.")
    epochs = integer(spec.get("epochs", 10), "epochs", 1, 100)
    seed = integer(spec["seed"], "seed", 0, 2**32 - 1)
    lr = number(spec.get("learning_rate", 0.003), "learning_rate", 0.00001, 0.1)
    if epochs * split * context * values.shape[1] * width > 100_000_000:
        raise ValueError("Neural training work bound exceeded.")
    train, test = values[:split], values[split:, 0]
    architecture = spec["architecture"]
    leaderboard = []
    if architecture == "search":
        configurations = spec.get("search")
        if not isinstance(configurations, list) or not 2 <= len(configurations) <= 8:
            raise ValueError("NAS needs two to eight explicit architecture/width configurations.")
        if len(configurations) * epochs * split * context * values.shape[1] * 64 > 200_000_000:
            raise ValueError("Combined architecture search work bound exceeded.")
        inner = split - max(horizon, 8)
        for i, configuration in enumerate(configurations):
            fields(configuration, ("architecture", "width"))
            candidate = configuration["architecture"]
            candidate_width = integer(configuration["width"], "width", 4, 64)
            prediction, evidence = fit_forecast(
                train[:inner],
                split - inner,
                architecture=candidate,
                context=context,
                width=candidate_width,
                epochs=epochs,
                seed=seed,
                learning_rate=lr,
            )
            leaderboard.append(
                {
                    "position": i,
                    "architecture": candidate,
                    "width": candidate_width,
                    "inner_rmse": float(np.sqrt(np.mean((prediction - train[inner:, 0]) ** 2))),
                    "weight_sha256": evidence["weight_sha256"],
                }
            )
        selected = min(leaderboard, key=lambda row: (row["inner_rmse"], row["position"]))
        architecture, width = selected["architecture"], selected["width"]
    elif "search" in spec:
        raise ValueError("Search configurations apply only to explicit architecture search.")
    prediction, evidence = fit_forecast(
        train,
        horizon,
        architecture=architecture,
        context=context,
        width=width,
        epochs=epochs,
        seed=seed,
        learning_rate=lr,
    )
    import importlib.metadata

    return {
        "architecture": architecture,
        "variant": "Small scratch-trained reference variant; not pretrained or full research-package feature parity",
        "backend": "torch",
        "backend_version": importlib.metadata.version("torch"),
        "train_rows": split,
        "test_rows": horizon,
        "rmse": float(np.sqrt(np.mean((prediction - test) ** 2))),
        "mae": float(np.mean(np.abs(prediction - test))),
        "training_values_sha256": digest(train.tolist()),
        "fit": evidence,
        "search": leaderboard,
        "selection_scope": "Inner chronological training suffix only; outer holdout never selects models",
        "forecast_origin": "Last training row, no observed holdout feedback or future covariates",
        "uncertainty_intervals_computed": False,
        "baseline_changed": False,
    }
