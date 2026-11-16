"""Isolated AutoML engines, a pretrained forecasting foundation model and pretrained vision.

TPOT, H2O AutoML, AutoGluon Tabular, auto-sklearn and Chronos-Bolt execute in separately
provisioned runtimes (see ``energybaseline.isolated``). GhostNet runs in this process on
PyTorch from separately provisioned weights (see ``energybaseline.assets``). Nothing here
downloads anything, trains on holdout targets, persists a model or changes a baseline.
"""

from __future__ import annotations

import re

import numpy as np

from .common import chronological, digest, fields, integer, number, numeric

AUTOML_ENGINES = ("tpot", "h2o", "autogluon", "autosklearn")
QUANTILE_LEVELS = (0.1, 0.5, 0.9)
# The vendored definition is upstream ghostnet.py plus a two-line change notice and one corrected
# name. Both hashes are asserted against the packaged source by the test suite.
GHOSTNET_UPSTREAM_SHA256 = "cfd6fe7e4705406b68111680dc5ab6904512f9f4a314c6f29052d94b0a74be5b"
GHOSTNET_SOURCE_SHA256 = "31568e1265af1fa92f40b4fd4a415afdab81b3c80f6186c3ef393b9394808cec"
VALIDATION_NOTES = {
    "tpot": "Negated scorer value of the selected pipeline on the inner validation rows.",
    "h2o": "Leader RMSE on the validation frame, which H2O also uses for early stopping, so it is optimistic.",
    "autogluon": "A weighted ensemble fits its weights on the validation rows, so its score is optimistic.",
    "autosklearn": "Recomputed from the selected model on the inner validation rows; equals the engine's holdout cost.",
}
_LABEL = re.compile(r"[A-Za-z0-9_ .,:()\[\]-]{1,120}")


def _text(value, name):
    if not isinstance(value, str) or _LABEL.fullmatch(value) is None:
        raise ValueError(f"Isolated engine returned an invalid {name}.")
    return value


def _predictions(values, count):
    if not isinstance(values, list) or len(values) != count or any(type(v) is not float for v in values):
        raise ValueError("Isolated engine returned predictions of the wrong shape or type.")
    result = np.asarray(values, dtype=float)
    if not np.isfinite(result).all():
        raise ValueError("Isolated engine returned nonfinite predictions.")
    return result


def external_automl(frame, spec):
    """Regression search by one real external AutoML engine on a chronological inner split.

    The engine process receives inner fitting rows, later inner validation rows and the
    outer holdout features. Purged gap rows and every outer holdout target stay in this
    process, where all holdout metrics are computed from the returned predictions.
    """
    from ..isolated import ENGINES, run_engine

    fields(
        spec,
        ("engine", "time", "target", "features", "train_rows", "validation_rows", "seed"),
        ("gap", "models"),
    )
    engine = spec["engine"]
    if not isinstance(engine, str) or engine not in AUTOML_ENGINES:
        raise ValueError("engine must be one of tpot, h2o, autogluon or autosklearn.")
    chronological(frame, spec["time"])
    target, features = spec["target"], spec["features"]
    if not isinstance(features, list) or not 1 <= len(features) <= 32 or target in features or spec["time"] in features:
        raise ValueError("Use 1..32 predictors disjoint from target and time.")
    if target == spec["time"] or len(frame) > 4000:
        raise ValueError("External AutoML requires a separate target and at most 4000 rows.")
    x, y = numeric(frame, features), numeric(frame, [target])[:, 0]
    split = integer(spec["train_rows"], "train_rows", 48, len(frame) - 1)
    validation = integer(spec["validation_rows"], "validation_rows", 12, split - 24)
    gap = integer(spec.get("gap", 0), "gap", 0, min(100, split - validation - 24))
    seed = integer(spec["seed"], "seed", 0, 2**31 - 1)
    models = integer(spec.get("models", 4), "models", 2, 6)
    boundary = split - validation
    fit_end = boundary - gap
    result = run_engine(
        engine,
        {"seed": seed, "models": models},
        {
            "x_fit": x[:fit_end],
            "y_fit": y[:fit_end],
            "x_val": x[boundary:split],
            "y_val": y[boundary:split],
            "x_test": x[split:],
        },
        timeout=180,
    )
    predicted = _predictions(result.get("predictions"), len(frame) - split)
    evaluated = integer(result.get("candidates_evaluated"), "candidates_evaluated", 1, 10_000)
    failed = integer(result.get("candidates_failed"), "candidates_failed", 0, evaluated)
    final_rows = result.get("final_fit_rows")
    if final_rows not in (fit_end, fit_end + validation):
        raise ValueError("Isolated engine reported an impossible final fitting scope.")
    refit = final_rows == fit_end + validation
    actual = y[split:]
    reference = float(np.mean(y[:fit_end]))
    return {
        "engine": engine,
        "engine_version": ENGINES[engine].version,
        "engine_license": ENGINES[engine].license,
        "runtime": "isolated virtual environment and process",
        "runtime_python": _text(result.get("runtime_python"), "runtime version"),
        "lock_sha256": result["lock_sha256"],
        "search_method": _text(result.get("search_method"), "search method"),
        "selected_model": _text(result.get("selected_model"), "model name"),
        "models_requested": models,
        "candidates_evaluated": evaluated,
        "candidates_failed": failed,
        "search_fit_rows": fit_end,
        "purge_gap": gap,
        "validation_rows": validation,
        "final_fit_rows": final_rows,
        "train_rows": split,
        "test_rows": len(frame) - split,
        "engine_reported_validation_rmse": number(result.get("validation_rmse"), "validation_rmse", 0),
        "engine_reported_validation_note": VALIDATION_NOTES[engine],
        "test_rmse": float(np.sqrt(np.mean((predicted - actual) ** 2))),
        "test_mae": float(np.mean(np.abs(predicted - actual))),
        "fit_mean_reference_test_rmse": float(np.sqrt(np.mean((reference - actual) ** 2))),
        "prediction_sha256": digest(predicted.tolist()),
        "fit_scope": (
            "The engine selects on one chronological inner split. "
            + (
                "Its selection is refit on fitting plus validation rows; "
                if refit
                else "The engine offers no refit, so the selected model stays trained on fitting rows; "
            )
            + "purged rows and outer holdout targets never enter the engine process."
        ),
        "holdout_targets_sent_to_engine": False,
        "model_persisted": False,
        "baseline_changed": False,
    }


def foundation_forecast(frame, spec):
    """Zero-shot univariate quantile forecast by pretrained Chronos-Bolt, scored on a holdout.

    Quantiles are model outputs without a coverage guarantee: no distribution-free claim is
    made for autocorrelated series and no interval is calibrated here.
    """
    from ..assets import ASSETS, verified_asset
    from ..isolated import ENGINES, run_engine

    fields(spec, ("time", "target", "train_rows"), ("horizon",))
    chronological(frame, spec["time"])
    if spec["target"] == spec["time"] or len(frame) > 20_000:
        raise ValueError("Foundation forecasting requires a separate target and at most 20000 rows.")
    y = numeric(frame, [spec["target"]])[:, 0]
    split = integer(spec["train_rows"], "train_rows", 32, len(frame) - 1)
    horizon = integer(spec.get("horizon", min(64, len(frame) - split)), "horizon", 1, min(64, len(frame) - split))
    model_dir = verified_asset("chronos-bolt-tiny")
    context = y[max(0, split - 2048) : split]
    result = run_engine(
        "chronos",
        {"horizon": horizon, "quantile_levels": list(QUANTILE_LEVELS)},
        {"context": context},
        timeout=120,
        extra_paths={"model": model_dir},
    )
    # The weights are hashed again after inference: the files that were read are the approved ones.
    verified_asset("chronos-bolt-tiny")
    rows = result.get("quantiles")
    if not isinstance(rows, list) or len(rows) != horizon:
        raise ValueError("Foundation model returned the wrong forecast horizon.")
    quantiles = np.vstack([_predictions(row, len(QUANTILE_LEVELS)) for row in rows])
    if _text(result.get("pipeline"), "pipeline name") != "ChronosBoltPipeline":
        raise ValueError("Foundation runtime loaded an unexpected pipeline.")
    actual = y[split : split + horizon]
    median = quantiles[:, 1]
    last = np.full(horizon, y[split - 1])
    pinball = [
        float(np.mean(np.maximum(level * (actual - quantiles[:, i]), (level - 1) * (actual - quantiles[:, i]))))
        for i, level in enumerate(QUANTILE_LEVELS)
    ]
    mae, last_mae = float(np.mean(np.abs(median - actual))), float(np.mean(np.abs(last - actual)))
    asset = ASSETS["chronos-bolt-tiny"]
    return {
        "engine": "chronos",
        "engine_version": ENGINES["chronos"].version,
        "runtime": "isolated virtual environment and process",
        "runtime_python": _text(result.get("runtime_python"), "runtime version"),
        "lock_sha256": result["lock_sha256"],
        "model": "amazon/chronos-bolt-tiny",
        "model_license": asset.license,
        "model_sha256": {item.name: item.sha256 for item in asset.files},
        "model_parameters": integer(result.get("parameters"), "parameters", 1_000_000, 100_000_000),
        "pretrained_weights_used": True,
        "training_performed": False,
        "runtime_downloads": False,
        "context_rows": len(context),
        "train_rows": split,
        "horizon": horizon,
        "quantile_levels": list(QUANTILE_LEVELS),
        "median_mae": mae,
        "median_rmse": float(np.sqrt(np.mean((median - actual) ** 2))),
        "last_value_mae": last_mae,
        "mae_ratio_to_last_value": mae / last_mae if last_mae > 0 else None,
        "mean_pinball_loss": float(np.mean(pinball)),
        "observations_inside_10_90": int(np.sum((actual >= quantiles[:, 0]) & (actual <= quantiles[:, 2]))),
        "quantile_crossings": int(np.sum(np.diff(quantiles, axis=1) < 0)),
        "forecast_sha256": digest(quantiles.tolist()),
        "scope": (
            "Zero-shot univariate forecast from fixed pretrained weights; holdout values never enter the "
            "model process. Quantiles are uncalibrated model outputs with no coverage guarantee."
        ),
        "model_persisted": False,
        "baseline_changed": False,
    }


def _ghostnet():
    """Build GhostNet 1.0x from the vendored upstream definition and verified weights."""
    import torch

    from ..assets import ASSETS, verified_asset
    from .vendor import ghostnet as upstream

    root = verified_asset("ghostnet-imagenet")
    weights, labels = ASSETS["ghostnet-imagenet"].files
    # weights_only refuses pickled code objects: only tensors and plain containers are loaded.
    state = torch.load(root / weights.name, map_location="cpu", weights_only=True)
    model = upstream.ghostnet(num_classes=1000, width=1.0, dropout=0.2)
    model.load_state_dict(state, strict=True)
    model.eval()
    names = (root / labels.name).read_text(encoding="utf-8").splitlines()
    if len(names) != 1000:
        raise ValueError("Class vocabulary must contain exactly 1000 labels.")
    return model, names, {item.name: item.sha256 for item in (weights, labels)}


def _imagenet_tensor(bgr):
    """Shorter side to 256 (bilinear), centre crop 224, ImageNet normalisation: upstream validation."""
    from PIL import Image

    image = Image.fromarray(np.ascontiguousarray(bgr[:, :, ::-1]))
    width, height = image.size
    if width <= height:
        size = (256, max(256, int(256 * height / width)))
    else:
        size = (max(256, int(256 * width / height)), 256)
    image = image.resize(size, Image.Resampling.BILINEAR)
    left, top = int(round((size[0] - 224) / 2.0)), int(round((size[1] - 224) / 2.0))
    pixels = np.asarray(image.crop((left, top, left + 224, top + 224)), dtype=np.float32) / 255.0
    normalized = (pixels - np.array([0.485, 0.456, 0.406], np.float32)) / np.array([0.229, 0.224, 0.225], np.float32)
    return normalized.transpose(2, 0, 1)


def pretrained_image_classification(spec):
    """ImageNet-1k classification by pretrained GhostNet; no training and no pixel export."""
    from ..isolated import ProvisioningRequired
    from .deep import torch_scope
    from .external import _ocr_images

    fields(spec, ("image_root", "image_files"), ("top_k",))
    top_k = integer(spec.get("top_k", 3), "top_k", 1, 5)
    images, sources = _ocr_images(spec)
    try:
        with torch_scope(0) as torch:
            model, names, hashes = _ghostnet()
            batch = torch.from_numpy(np.stack([_imagenet_tensor(image) for image in images]))
            with torch.no_grad():
                probabilities = torch.softmax(model(batch), dim=1).numpy().astype(float)
            parameters = int(sum(p.numel() for p in model.parameters()))
            if _ghostnet()[2] != hashes:
                raise ValueError("Model identity changed during execution.")
    except ProvisioningRequired:
        raise
    except Exception:
        # Library messages can contain local paths. This is the external boundary, not a fallback.
        raise ValueError(
            "Pretrained image classification failed model verification or bounded execution; raw details are private."
        ) from None
    if probabilities.shape != (len(images), 1000) or not np.isfinite(probabilities).all():
        raise ValueError("Pretrained classifier returned an invalid probability table.")
    outputs = []
    for source, row in zip(sources, probabilities):
        order = np.argsort(-row, kind="stable")[:top_k]
        outputs.append(
            {
                **source,
                "top": [{"class_index": int(i), "label": names[i], "probability": float(row[i])} for i in order],
            }
        )
    import importlib.metadata

    return {
        "engine": "torch",
        "engine_version": importlib.metadata.version("torch").split("+")[0],
        "model": "GhostNet 1.0x (Huawei Noah's Ark Lab), ImageNet-1k",
        "model_definition_sha256": GHOSTNET_SOURCE_SHA256,
        "model_definition_upstream_sha256": GHOSTNET_UPSTREAM_SHA256,
        "model_sha256": hashes,
        "model_parameters": parameters,
        "published_top1_accuracy": 0.7398,
        "pretrained_weights_used": True,
        "training_performed": False,
        "runtime_downloads": False,
        "preprocessing": "EXIF transpose, RGB, shorter side 256 bilinear, centre crop 224, ImageNet mean/std",
        "top_k": top_k,
        "images": outputs,
        "pixels_exported": False,
        "baseline_changed": False,
        "scope": (
            "Closed-vocabulary ImageNet-1k classification. Probabilities are softmax scores, not calibrated "
            "accuracy; objects outside the 1000 classes still receive one of those labels."
        ),
    }
