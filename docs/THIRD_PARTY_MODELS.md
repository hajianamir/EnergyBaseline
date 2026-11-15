# Third-party pretrained model provenance and notices

The Stage 5 OCR adapter uses real pretrained weights bundled in the exact hash-locked PyPI distribution `rapidocr-onnxruntime==1.4.4`. EnergyBaseline does not claim authorship, training, or ownership of these weights. They are not committed to this repository as synthetic fixtures.

## Components and attribution

- **RapidOCR engineering:** RapidOCR Authors; distribution metadata identifies Apache-2.0. Upstream: https://github.com/RapidAI/RapidOCR .
- **PP-OCRv4 detection and recognition, and PP-OCR mobile v2 orientation weights:** derived from PaddleOCR; upstream model rights belong to Baidu and/or the applicable PaddleOCR rights holders. Upstream: https://github.com/PaddlePaddle/PaddleOCR .
- **ONNX Runtime:** Microsoft and contributors; runtime distributed under its own MIT license and applicable dependency notices.
- **FLAML:** Microsoft and contributors; MIT-licensed external AutoML code, not a pretrained model.
- **Pandera:** Pandera contributors; MIT-licensed external schema code, not a pretrained model.

RapidOCR's inspected model attribution notice declares its registered PaddleOCR-derived models, including the legacy orientation and PP-OCRv4 families, Apache-2.0. The upstream notice is retained verbatim in `docs/licenses/RapidOCR-MODEL-NOTICE.md`; the Apache-2.0 license text is retained in `docs/licenses/RapidOCR-APACHE-2.0.txt`. Source identifiers and actual read receipts are in `artifacts/capabilities/external-stage5/research.json`. This records upstream licensing declarations; it is not legal advice or a claim of independent ownership verification.

## Exact weight identities

Approved sizes and full SHA-256 values are fixed in `energybaseline/workbench/external.py::OCR_MODELS` and reproduced in `docs/EXTERNAL_ENGINES.md`. The dependency wheel hash is fixed in `requirements.lock`. The adapter rejects a missing, changed, substituted or differently versioned installed OCR model set. It does not accept arbitrary ONNX files, dynamic code, pickle checkpoints, user URLs or runtime weight downloads.

## Redistribution

The native build configuration collects RapidOCR model/configuration data and copies this notice, the external-engine documentation, and `docs/licenses/` next to the application. Preserve attribution and the Apache-2.0 license/notice when redistributing the OCR models. Dependency code retains its own applicable license and notices; the generated CycloneDX SBOM records installed software components. These model notices supplement, not replace, dependency licenses or the repository's unchanged `LICENSE`.

The package's recognition dictionary is embedded in the upstream ONNX recognizer metadata; its provenance follows the inspected PaddleOCR-derived model declaration. No third-party font file or example photograph is redistributed by this stage; `examples/external/printed.png` is a generated synthetic raster.

No YOLO, foundation-forecasting, language-foundation, face-identification or other pretrained weights are silently included. No model accuracy, fitness for a regulated use, legal fairness, handwriting coverage or general language coverage is guaranteed. OCR plaintext is transient and is not included in the transferable workbench result.
