# Stage 5 external engines and pretrained models: execution record

## Scope

Three actual new operations integrate FLAML CFO regression search, Pandera schema validation, and pretrained RapidOCR detection/orientation/recognition with three real ONNX models bundled in a pinned wheel. There are 35 total workbench operations, 11 specification-only. API, CLI and native spawn-worker execution share schemas. See `docs/EXTERNAL_ENGINES.md` and `docs/THIRD_PARTY_MODELS.md`.

This is not all-engine/catalog completion. Auto-sklearn/AutoGluon compatibility conflicts, TPOT/H2O lifecycle work, unavailable foundation/YOLO weights, full research-model parity and other catalog families remain explicitly open. `program_complete=false` remains mandatory.

## Verification status

Development executions passed actual engine/semantic recognition/native-worker tests. Final-source focused/GUI/full-regression execution and actual Windows/Linux CI are pending; no Stage 5 acceptance claim is made yet. Frozen Windows external-engine execution is an additional CI requirement, not inferred from Python tests or the old packaging smoke test. Earlier-stage CI is not Stage 5 evidence.

`research.json` records actual PyPI release metadata/artifact hashes, installed source reads and upstream requirements/model-license reads. It separately identifies failed YOLO model retrieval; no YOLO inference is claimed. The pretrained OCR oracle checks semantic recognition in memory and separately checks that the public result exports no recognized plaintext. Models are neither mocks nor synthetic trained fixtures, and are not added to Git.

No prior dependency pin, Python version, coverage threshold, mandatory test, baseline/report contract or inference gate is weakened. New dependencies use exact pins and PyPI release hashes. Model attribution and Apache-2.0 notices accompany the build configuration. Raw development receipts are non-acceptance evidence and may be superseded by final-source receipts.
