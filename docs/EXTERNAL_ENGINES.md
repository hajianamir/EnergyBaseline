# External engines and pretrained OCR: Stage 5

## Status and honest scope

Three additional executable operations call **FLAML**, **Pandera**, and **RapidOCR with actual pretrained ONNX weights**. The operation registry now contains 35 operations, including 11 specification-only operations. API, CLI and the native spawn-worker panel use the same contracts. This is not integration of every external engine or full paper parity. Final-source local and two-OS verification status belongs to `artifacts/capabilities/external-stage5/README.md`; development passes are not acceptance.

No prior runtime, gate, privacy, baseline, report renderer, test-integrity or coverage requirement is relaxed. The Python 3.12 runtime and existing dependency versions remain unchanged. New dependencies are exact-pinned, with release hashes in `requirements.lock`. No incompatible dependency overrides or `--no-deps` production install is used.

## FLAML: actual external AutoML

Operation: `flaml-search`. Required parameters:

- `time`, `target`, `features`: explicit, disjoint time/target/predictor selection. Time must be unique and strictly increasing. Predictors and target must be finite numeric values; no target imputation or implicit predictor imputation.
- `train_rows`: outer training-prefix length, at least 24, leaving a nonempty outer holdout.
- `validation_rows`: 6 or more, leaving at least 12 inner fitting rows.
- `seed`: integer 0 through 2**31-1.
- Optional `gap`: 0 through 100, leaving at least 12 inner fitting rows. Optional `iterations`: 2 through 16, default 6.

Let `b = train_rows - validation_rows`. Actual FLAML CFO search sees only rows `[0, b-gap)` for fitting and `[b, train_rows)` for validation. Round-robin learner selection searches real FLAML random-forest and extra-trees estimators with 8..32 trees and 4..32 leaves. A finite iteration budget, 4,000-row limit, 32-predictor limit and single-threaded CPU limit bound work; this is not a hard hostile-code wall-clock sandbox.

FLAML never receives outer holdout features or targets. A fresh clone of its selected underlying estimator is fit on the complete outer training prefix, including the formerly purged inner gap, then evaluated once on the outer holdout. Results include actual validation/test errors, executed iteration count, selected family/configuration and a training digest. No fitted model, data rows or prediction vectors are persisted in results. There is no main baseline promotion or lock integration.

Example:

```sh
python -m energybaseline.cli workbench examples/external/flaml-search.json --dataset examples/advanced/series.csv --out flaml-result.json
```

This is **FLAML integration**, not a relabeling of the existing Optuna operation. It is not FLAML's entire estimator/task catalog, distributed search, forecasting backend, or every named AutoML engine.

## Pandera: actual external schema execution

Operation: `pandera-validation`. Required `columns` is a list of 1..100 unique rules with `column` and `dtype` (`float64`, `int64`, `str`, `bool`). Optional per-column booleans `nullable` and `unique` default false. Numeric dtypes support finite `minimum` and `maximum`. Optional global `strict` rejects extra columns. Columns must already exist. Coercion is always disabled.

The adapter constructs a real `pandera.pandas.DataFrameSchema` and runs lazy validation on an isolated copy with a positional index. It does not evaluate user Python or callbacks. Real validation failures are audit findings, not successful data validation: `passed=false`, failure-case counts, schema-level failure counts and at most 100 failing row positions are returned. Cells, caller column names, category values, private index labels and third-party exception details are not exported. Positions can repeat across checks internally; `failure_case_count` is not falsely labeled unique failed-row count.

```sh
python -m energybaseline.cli workbench examples/external/pandera-validation.json --dataset examples/advanced/series.csv --out pandera-result.json
```

This is a bounded, executed Pandera adapter. Arbitrary Pandera extension code and Great Expectations are not integrated. Cross-column/reference business rules remain available through the existing separate `business-validation` operation.

## Genuine pretrained OCR, without runtime downloads

Operation: `pretrained-ocr` is specification-only. Required parameters: explicit `image_root` and 1..4 relative `image_files`. Optional `confidence` is 0.1..0.99, default 0.5. No implicit dataset, reference frame, URLs, arbitrary model paths, code or pickle files are accepted.

The dependency wheel `rapidocr-onnxruntime==1.4.4` contains three real pretrained models:

| Model | Bytes | SHA-256 |
| --- | ---: | --- |
| PP-OCRv4 detection | 4,745,517 | `d2a7720d45a54257208b1e13e36a8479894cb74155a5efe29462512d42f49da9` |
| PP-OCRv4 recognition | 10,857,958 | `48fc40f24f6d2a207a2b1091d3437eb3cc3eb6b676dc3ef9c37384005483683b` |
| PP-OCR mobile v2 orientation | 585,532 | `e47acedf663230f8863ff1ab0e64dd2d82b838fceb5957146dab185a89d6215c` |

The adapter verifies all three files against fixed approved byte counts/hashes before construction and after inference. It explicitly selects their installed paths, CPU-only ONNX Runtime, one inference thread, bounded detector resizing/candidates and single-crop batches. Installed dependency code/configuration remains part of the trusted installation; hash checks are not an operating-system privilege boundary. No network model provisioning occurs in the application. ONNX telemetry is disabled. OpenCV's import-time path and Qt5-plugin environment changes are restored at the centralized runtime configuration boundary so they do not leak into native Qt6 worker launches.

PNG/JPEG/BMP files must resolve under the explicit root. Absolute names, traversal outside the root, corrupt/animated images and excessive inputs fail closed. Each compressed file is bounded to 5 MB, each decoded image to 2 million pixels, total to 4 million pixels; sides are 16..4000 pixels. Sources are decoded locally to EXIF-corrected BGR arrays before reaching RapidOCR, so its URL/path loader is not exposed to requests. Source-byte and decoded-pixel hashes bind evidence to actual read content; they are not claims that a mutable filesystem remains unchanged afterward.

The real detector, orientation classifier and recognizer run, and strings exist transiently in engine memory. The public operation is deliberately a **privacy-preserving OCR audit**, not a plaintext extraction/export endpoint: output contains line quadrilaterals, confidence and character counts, never recognized text, text hashes, pixel arrays or user paths. There is no `include_text` escape hatch. Confidence is a model score, not calibrated accuracy or guaranteed correctness. Printed Chinese/English is the pretrained model scope; general language/handwriting parity is not claimed.

```sh
python -m energybaseline.cli workbench examples/external/pretrained-ocr.json --out ocr-result.json
```

The example is a synthetic printed raster, not a model/template used to train or match the recognizer. The semantic oracle actually checks that the pretrained engine recognizes `HELLO WORLD 123` above 0.85 confidence; the public result is separately checked to suppress this plaintext. Another test executes the real operation in a fresh process with socket connection/name-resolution events denied. A blank-image oracle checks that no template text is fabricated.

See [THIRD_PARTY_MODELS.md](THIRD_PARTY_MODELS.md) for model rights/provenance and redistribution notices. The pretrained models remain inside the hash-locked dependency wheel, not Git evidence fixtures. PyInstaller configuration collects their data/configuration, external distribution metadata and Pandera dispatch modules. CI adds actual independent CLI receipts on both OSes and an additional frozen Windows engine execution after packaging; success is not assumed until those steps actually finish.

## Outstanding integrations, not silently declared complete

- **Auto-sklearn:** the inspected upstream requirements demand `scikit-learn>=0.24,<0.25`; this conflicts with the project's locked `scikit-learn==1.9.1` and cannot be resolved by suppressing dependency checks. A genuinely supported isolated legacy backend would need a separately approved runtime/lifecycle boundary, not a downgrade of the main application.
- **AutoGluon Tabular 1.6.3:** inspected metadata requires `pandas>=2,<2.4`, conflicting with locked `pandas==3.1.1`. No compatible execution is claimed.
- **TPOT 1.1.0:** metadata was inspected; actual dependency closure, Dask/distributed lifecycle, offline/no-dashboard policy, execution and native packaging remain unimplemented. This is not labeled impossible.
- **H2O 3.46.0.12:** actual Java/JVM backend and its local HTTP service require a separately reviewed boundary under the standing Python-only/web-free desktop policy. No Java executable is available in this sandbox and no H2O execution is claimed.
- **Pretrained general vision:** YOLO release metadata and published SHA-256 values were retrieved. Actual model downloads failed when GitHub redirected to a host outside the sandbox allowlist. No YOLO weights were obtained and no YOLO execution/integration is claimed. AGPL/commercial licensing also needs an explicit redistribution decision; the weights are not silently bundled.
- **Pretrained foundation forecasting:** no foundation weights have been obtained/executed here. A library installation or a model identifier is not a pretrained forecasting integration.
- **Full research-model parity:** Stage 4 reduced networks retain their documented exclusions. This stage does not implement static/known-future TFT, Informer full ProbSparse complexity, global-panel likelihood training, probabilistic sampling, pretrained foundation forecasting or paper benchmark parity.
- **Remaining catalog families:** causal identification/model families, richer survival/graph/NLP/recommenders, mitigation/recourse, accepted model lifecycle and other per-row remaining scope remain open.

Exact inspected PyPI metadata, actual installed source hashes, upstream requirement/model-license reads and failed-download limitations are recorded in `artifacts/capabilities/external-stage5/research.json`. Research is not execution. Full-program completion remains false.
