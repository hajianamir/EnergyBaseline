"""Actual external engines with fixed schemas, private failures and offline pretrained OCR.

No user import paths, executable callbacks, URLs, pickles or model overrides are accepted.
These operations do not authorize inference or modify the accepted baseline lifecycle.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
from io import BytesIO
from pathlib import Path
from types import MappingProxyType

import numpy as np

from .common import chronological, digest, fields, integer, number, numeric


OCR_MODELS = MappingProxyType(
    {
        "ch_PP-OCRv4_det_infer.onnx": (4745517, "d2a7720d45a54257208b1e13e36a8479894cb74155a5efe29462512d42f49da9"),
        "ch_PP-OCRv4_rec_infer.onnx": (10857958, "48fc40f24f6d2a207a2b1091d3437eb3cc3eb6b676dc3ef9c37384005483683b"),
        "ch_ppocr_mobile_v2.0_cls_infer.onnx": (
            585532,
            "e47acedf663230f8863ff1ab0e64dd2d82b838fceb5957146dab185a89d6215c",
        ),
    }
)


def flaml_holdout(frame, spec):
    """Real FLAML CFO search on a prefix/suffix split inside the training partition."""
    fields(spec, ("time", "target", "features", "train_rows", "validation_rows", "seed"), ("iterations", "gap"))
    chronological(frame, spec["time"])
    target, features = spec["target"], spec["features"]
    if not isinstance(features, list) or not 1 <= len(features) <= 32 or target in features or spec["time"] in features:
        raise ValueError("Use 1..32 predictors disjoint from target and time.")
    if target == spec["time"] or len(frame) > 4000:
        raise ValueError("FLAML requires a separate target and at most 4000 rows.")
    x, y = numeric(frame, features), numeric(frame, [target])[:, 0]
    split = integer(spec["train_rows"], "train_rows", 24, len(frame) - 1)
    validation = integer(spec["validation_rows"], "validation_rows", 6, split - 12)
    gap = integer(spec.get("gap", 0), "gap", 0, min(100, split - validation - 12))
    seed = integer(spec["seed"], "seed", 0, 2**31 - 1)
    iterations = integer(spec.get("iterations", 6), "iterations", 2, 16)
    boundary = split - validation
    fit_end = boundary - gap
    from flaml import AutoML, tune
    from sklearn.base import clone
    from threadpoolctl import threadpool_limits

    search = AutoML()
    space = {
        family: {
            "n_estimators": {"domain": tune.randint(8, 33), "init_value": 8, "low_cost_init_value": 8},
            "max_leaves": {"domain": tune.randint(4, 33), "init_value": 4, "low_cost_init_value": 4},
            "max_features": {"domain": 1.0, "init_value": 1.0},
        }
        for family in ("rf", "extra_tree")
    }
    # Finite iterations/model sizes are used instead of a timing-dependent search cutoff.
    # All input arrays are copied: third-party search cannot mutate the caller frame.
    with threadpool_limits(limits=1):
        search.fit(
            X_train=x[:fit_end].copy(),
            y_train=y[:fit_end].copy(),
            X_val=x[boundary:split].copy(),
            y_val=y[boundary:split].copy(),
            task="regression",
            metric="rmse",
            estimator_list=["rf", "extra_tree"],
            eval_method="holdout",
            retrain_full=False,
            ensemble=False,
            sample=False,
            max_iter=iterations,
            time_budget=-1,
            n_jobs=1,
            verbose=0,
            log_file_name="",
            seed=seed,
            learner_selector="roundrobin",
            hpo_method="cfo",
            custom_hp=space,
            keep_search_state=True,
        )
        selected = clone(search.model.estimator)
        params = selected.get_params()
        if not 8 <= params["n_estimators"] <= 32 or not 4 <= params["max_leaf_nodes"] <= 32:
            raise ValueError("External engine exceeded the declared model budget.")
        selected.fit(x[:split], y[:split])
        predicted = selected.predict(x[split:])
    if not np.isfinite(predicted).all() or not np.isfinite(search.best_loss):
        raise ValueError("External engine returned nonfinite predictions or validation loss.")
    return {
        "engine": "flaml",
        "engine_version": importlib.metadata.version("flaml"),
        "search_method": "CFO",
        "selected_family": search.best_estimator,
        "selected_parameters": {
            k: float(v) if isinstance(v, np.floating) else int(v) if isinstance(v, np.integer) else v
            for k, v in search.best_config.items()
        },
        "iterations_requested": iterations,
        "iterations_executed": sum(search._iter_per_learner.values()),
        "search_fit_rows": fit_end,
        "purge_gap": gap,
        "validation_rows": validation,
        "train_rows": split,
        "test_rows": len(frame) - split,
        "validation_rmse": float(search.best_loss),
        "test_rmse": float(np.sqrt(np.mean((predicted - y[split:]) ** 2))),
        "test_mae": float(np.mean(np.abs(predicted - y[split:]))),
        "training_sha256": digest(np.column_stack([x[:split], y[:split]]).tolist()),
        "fit_scope": "FLAML sees only inner fit/validation partitions. Selected estimator is refit on the outer training prefix once.",
        "model_persisted": False,
        "baseline_changed": False,
    }


def pandera_validation(frame, spec):
    """Execute Pandera's real schema engine; return only bounded positions, never cells."""
    fields(spec, ("columns",), ("strict",))
    rules = spec["columns"]
    strict = spec.get("strict", False)
    if type(strict) is not bool or not isinstance(rules, list) or not 1 <= len(rules) <= 100:
        raise ValueError("Declare 1..100 column rules and a boolean strict policy.")
    import pandera.pandas as pa
    from pandera.errors import SchemaErrors

    schema_columns = {}
    for rule in rules:
        fields(rule, ("column", "dtype"), ("nullable", "unique", "minimum", "maximum"))
        name, dtype = rule["column"], rule["dtype"]
        if not isinstance(name, str) or name not in frame.columns or name in schema_columns:
            raise ValueError("Schema columns must exist and be unique.")
        if dtype not in ("float64", "int64", "str", "bool"):
            raise ValueError("Unsupported external schema dtype.")
        nullable, unique = rule.get("nullable", False), rule.get("unique", False)
        if type(nullable) is not bool or type(unique) is not bool:
            raise ValueError("Nullable and unique policies must be booleans.")
        checks = []
        for key, factory in (("minimum", pa.Check.ge), ("maximum", pa.Check.le)):
            if key in rule:
                if dtype not in ("float64", "int64"):
                    raise ValueError("Bounds require an explicit numeric dtype.")
                checks.append(factory(number(rule[key], key)))
        if "minimum" in rule and "maximum" in rule and rule["minimum"] > rule["maximum"]:
            raise ValueError("Inverted numeric bounds are invalid.")
        schema_columns[name] = pa.Column(dtype, checks=checks, nullable=nullable, unique=unique, coerce=False)
    schema = pa.DataFrameSchema(schema_columns, strict=strict, coerce=False)
    # Reset the index of the isolated validation copy: evidence always uses row positions,
    # never a caller-supplied index that could contain private identifiers.
    isolated = frame.copy(deep=True).reset_index(drop=True)
    failures = 0
    positions = []
    schema_level = 0
    try:
        schema.validate(isolated, lazy=True, inplace=False)
    except SchemaErrors as error:
        cases = error.failure_cases
        failures = len(cases)
        for index in cases["index"]:
            if isinstance(index, (int, np.integer)) and not isinstance(index, bool) and 0 <= index < len(frame):
                positions.append(int(index))
            else:
                schema_level += 1
    return {
        "engine": "pandera",
        "engine_version": importlib.metadata.version("pandera"),
        "passed": failures == 0,
        "rows_checked": len(frame),
        "columns_checked": len(schema_columns),
        "failure_case_count": failures,
        "schema_level_failure_cases": schema_level,
        "failed_row_positions": sorted(set(positions))[:100],
        "row_positions_truncated": len(set(positions)) > 100,
        "coercion_performed": False,
        "raw_values_exported": False,
        "baseline_changed": False,
    }


def _ocr_images(spec):
    """Decode bounded single-frame local files without delegating URLs/paths to OCR."""
    from PIL import Image, ImageOps
    from ..io.paths import validate_path

    root, names = spec["image_root"], spec["image_files"]
    if not isinstance(root, str) or not isinstance(names, list) or not 1 <= len(names) <= 4:
        raise ValueError("OCR requires an explicit root and 1..4 relative image names.")
    root_path = validate_path(root, allowed_roots=[root], must_exist=True)
    if not root_path.is_dir():
        raise ValueError("OCR root must be a directory.")
    images, sources = [], []
    total = 0
    try:
        for name in names:
            if not isinstance(name, str) or Path(name).is_absolute():
                raise ValueError("OCR names must be relative to the declared root.")
            path = validate_path(root_path / name, allowed_roots=[root_path], must_exist=True)
            if path.suffix.lower() not in (".png", ".jpg", ".jpeg", ".bmp") or not path.is_file():
                raise ValueError("OCR supports only local PNG/JPEG/BMP files.")
            with path.open("rb") as stream:
                raw = stream.read(5_000_001)
            if len(raw) > 5_000_000:
                raise ValueError("OCR source byte budget exceeded.")
            with Image.open(BytesIO(raw)) as image:
                pixels = image.width * image.height
                total += pixels
                if pixels > 2_000_000 or total > 4_000_000 or min(image.size) < 16 or max(image.size) > 4000:
                    raise ValueError("OCR decoded pixel/dimension budget exceeded.")
                if getattr(image, "n_frames", 1) != 1:
                    raise ValueError("OCR accepts only single-frame images.")
                rgb = np.asarray(ImageOps.exif_transpose(image).convert("RGB"))
                images.append(np.ascontiguousarray(rgb[:, :, ::-1]))
            sources.append(
                {
                    "source_sha256": hashlib.sha256(raw).hexdigest(),
                    "decoded_bgr_sha256": hashlib.sha256(images[-1].tobytes()).hexdigest(),
                    "height": images[-1].shape[0],
                    "width": images[-1].shape[1],
                }
            )
    except (OSError, ValueError, Image.DecompressionBombError):
        raise ValueError(
            "OCR image loading failed path, size or decoding validation; source details are private."
        ) from None
    return images, sources


def _rapidocr_module():
    from ..config.runtime_env import preserve_native_import_environment

    with preserve_native_import_environment():
        import rapidocr_onnxruntime
    return rapidocr_onnxruntime


def _verified_ocr_models():
    """Verify the exact pretrained bytes bundled in the hash-locked dependency wheel."""
    rapidocr_onnxruntime = _rapidocr_module()

    if importlib.metadata.version("rapidocr-onnxruntime") != "1.4.4":
        raise ValueError("The approved OCR distribution version is required.")
    root = Path(rapidocr_onnxruntime.__file__).parent / "models"
    return _verify_model_directory(root)


def _verify_model_directory(root):
    for name, (size, expected) in OCR_MODELS.items():
        with (root / name).open("rb") as stream:
            raw = stream.read(size + 1)
        if len(raw) != size or hashlib.sha256(raw).hexdigest() != expected:
            raise ValueError("Installed OCR weights failed the approved pretrained hash binding.")
    return {name: expected for name, (_, expected) in OCR_MODELS.items()}


def _make_ocr_engine(confidence):
    import onnxruntime

    rapidocr_onnxruntime = _rapidocr_module()
    onnxruntime.disable_telemetry_events()
    model_root = Path(rapidocr_onnxruntime.__file__).parent / "models"
    engine = rapidocr_onnxruntime.RapidOCR(
        det_model_path=str(model_root / "ch_PP-OCRv4_det_infer.onnx"),
        rec_model_path=str(model_root / "ch_PP-OCRv4_rec_infer.onnx"),
        cls_model_path=str(model_root / "ch_ppocr_mobile_v2.0_cls_infer.onnx"),
        intra_op_num_threads=1,
        inter_op_num_threads=1,
        det_limit_side_len=512,
        det_limit_type="max",
        det_use_cuda=False,
        det_use_dml=False,
        cls_use_cuda=False,
        cls_use_dml=False,
        rec_use_cuda=False,
        rec_use_dml=False,
        max_side_len=1024,
        min_side_len=30,
        text_score=confidence,
        cls_batch_num=1,
        rec_batch_num=1,
        print_verbose=False,
    )
    engine.text_det.postprocess_op.max_candidates = 32
    for session in (engine.text_det.infer.session, engine.text_cls.infer.session, engine.text_rec.session.session):
        if session.get_providers() != ["CPUExecutionProvider"]:
            raise ValueError("OCR must use only the explicit CPU provider.")
    return engine


def pretrained_ocr(spec):
    """Real pretrained detection/orientation/recognition, with no recognized plaintext export.

    This public operation is a privacy-preserving OCR audit, not a plaintext extraction
    endpoint. The underlying engine recognizes strings in memory; only line geometry,
    confidence and character counts enter transferable result evidence.
    """
    fields(spec, ("image_root", "image_files"), ("confidence",))
    confidence = number(spec.get("confidence", 0.5), "confidence", 0.1, 0.99)
    images, sources = _ocr_images(spec)
    try:
        return _execute_pretrained_ocr(images, sources, confidence)
    except Exception:
        # ONNX/OpenCV expose nonstandard exception classes whose messages may include
        # source paths. This is the external-library boundary, not a success fallback.
        raise ValueError(
            "Pretrained OCR failed model verification or bounded execution; raw details are private."
        ) from None


def _execute_pretrained_ocr(images, sources, confidence):
    weights = _verified_ocr_models()
    engine = _make_ocr_engine(confidence)
    outputs = []
    for image, source in zip(images, sources):
        recognized, _ = engine(image)
        lines = []
        for box, text, score in recognized or []:
            geometry = np.asarray(box, dtype=float)
            if geometry.shape != (4, 2) or not np.isfinite(geometry).all() or len(text) > 4096:
                raise ValueError("Invalid or excessive pretrained OCR output.")
            score = number(float(score), "recognition confidence", 0, 1)
            lines.append({"quadrilateral": geometry.tolist(), "confidence": score, "character_count": len(text)})
        if len(lines) > 32:
            raise ValueError("OCR detected-line budget exceeded.")
        outputs.append({**source, "line_count": len(lines), "lines": lines})
    # Detect installed-weight changes across construction/execution; arbitrary local models
    # and dynamic downloads are never supported. This is not a hostile-OS sandbox claim.
    if _verified_ocr_models() != weights:
        raise ValueError("OCR model identity changed during execution.")
    return {
        "engine": "rapidocr-onnxruntime",
        "engine_version": importlib.metadata.version("rapidocr-onnxruntime"),
        "runtime_version": importlib.metadata.version("onnxruntime"),
        "provider": "CPUExecutionProvider",
        "pretrained_weights_used": True,
        "model_sha256": weights,
        "models": "PP-OCRv4 detector and recognizer; PP-OCR mobile v2 orientation classifier",
        "training_performed": False,
        "runtime_downloads": False,
        "images": outputs,
        "recognized_plaintext_exported": False,
        "baseline_changed": False,
        "scope": "Pretrained printed Chinese/English OCR audit. Confidence is not a calibrated accuracy guarantee; no plaintext extraction/export.",
    }
