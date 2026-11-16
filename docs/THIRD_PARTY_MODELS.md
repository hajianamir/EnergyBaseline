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

No YOLO, language-foundation, face-identification or other pretrained weights are silently included; the Stage 7 weights below are provisioned by an explicit operator action and are not part of the repository or the application bundle. No model accuracy, fitness for a regulated use, legal fairness, handwriting coverage or general language coverage is guaranteed. OCR plaintext is transient and is not included in the transferable workbench result.

## Stage 7: separately provisioned pretrained weights

These files are **not committed and not bundled**. `python -m energybaseline.cli assets install <asset>` places them under `<application home>/model-assets/<asset>` from the fixed origins below, or from a local directory, and accepts them only when size and SHA-256 match `energybaseline/assets.py::ASSETS`. They are verified again before and after every use, and a directory holding any other file is refused.

| Asset | File | Bytes | SHA-256 |
| --- | --- | ---: | --- |
| `chronos-bolt-tiny` | `config.json` | 1,120 | `278f0086733031635fb1c861cb01c1bad6477420c7fcb19381a2993e335785e0` |
| `chronos-bolt-tiny` | `model.safetensors` | 34,622,352 | `75068728d376d2bec670379eeef4bfb4d24c0cfe24d957451f8d19b447030a32` |
| `ghostnet-imagenet` | `state_dict_73.98.pth` | 20,923,722 | `965143b0cfbb704283890f95f0037be632d8fa095047e3a2c9e63ed0a9a1df14` |
| `ghostnet-imagenet` | `imagenet_classes.txt` | 10,472 | `1f386e0d1cb6e28b9c2dac651c3dea6801e98ad1b41a14ce6bb1a093d72069f5` |

### Chronos-Bolt tiny

- **Rights holder and licence:** Amazon.com, Inc. and the Chronos authors; the model card declares `license: apache-2.0`. Upstream: https://huggingface.co/amazon/chronos-bolt-tiny and https://github.com/amazon-science/chronos-forecasting .
- **Fixed origin:** Hugging Face revision `a0e552de83495b5c28c14c71c374f3e33280b340`, files `model.safetensors` and `config.json`. `safetensors` is a data-only tensor format; no pickle is loaded.
- **How the identity was established:** the Hugging Face host is not reachable from the development sandbox, so the bytes were first obtained from public Git mirrors and accepted only because their SHA-256 values equal the values that independent public repositories pin for that Hugging Face revision. The identities and mirror blob identifiers are recorded in `artifacts/capabilities/isolated-stage7/research.json`. The hosted workflow downloads from the Hugging Face origin itself, so its success is the direct check that the pinned hashes are the publisher's files.
- **Citation:** Ansari et al., "Chronos: Learning the Language of Time Series", Transactions on Machine Learning Research, 2024.
- The inference library `chronos-forecasting==2.3.2` (Apache-2.0) and its PyTorch stack are installed in a separate runtime, see `docs/ISOLATED_ENGINES.md`.

### GhostNet 1.0x ImageNet-1k

- **Rights holder and licence:** Huawei Technologies Co., Ltd.; Apache-2.0, text retained in `docs/licenses/GhostNet-Apache-2.0.txt`. Upstream: https://github.com/huawei-noah/Efficient-AI-Backbones , directory `ghostnet_pytorch`, commit `f90e129b645c3b1684fe07cd361cd557d0ad71f7`.
- **Weights:** `models/state_dict_73.98.pth` from that commit, the authors' published checkpoint (top-1 73.98%, top-5 91.46% on ImageNet-1k validation according to their readme; not re-measured here). The file is a PyTorch state dictionary and is loaded with `weights_only=True`.
- **Vendored model definition:** `energybaseline/workbench/vendor/ghostnet.py` is the upstream `ghostnet.py` (SHA-256 `cfd6fe7e4705406b68111680dc5ab6904512f9f4a314c6f29052d94b0a74be5b`) **modified by EnergyBaseline**, as Apache-2.0 section 4(b) requires to be stated: a change notice was added above the original text and the export list `__all__ = ['ghost_net']`, which names a function the file never defines, was corrected to `['ghostnet']`. Nothing else differs; a test reverses those two edits and requires the upstream hash. Vendored file SHA-256: `31568e1265af1fa92f40b4fd4a415afdab81b3c80f6186c3ef393b9394808cec`.
- **Citation:** Han et al., "GhostNet: More Features from Cheap Operations", CVPR 2020.
- **ImageNet:** the weights were trained by their authors on ImageNet-1k, whose images are distributed under ImageNet's own research-oriented terms. No ImageNet image is redistributed here. Whether a use of ImageNet-trained weights is appropriate is the deploying organisation's decision.

### ImageNet class list

`imagenet_classes.txt` is taken from `pytorch/hub` at commit `c3beaae7d32fca2a23fec30aa7938ef5c9b6e5d5`. **That repository declares no licence**, so no licence is asserted for this file here and it is neither committed nor bundled; it is downloaded by the operator from its origin. Its content is the list of the 1,000 ImageNet-1k class names in index order.

### Example photographs

`examples/isolated/images/cat.jpg` and `espresso.jpg` are JPEG (quality 85) re-encodings of `chelsea.png` and `coffee.png` from scikit-image's data directory (commit `6a1a73f5b64cb7a0ba83367f30ee612b1d70d98c`). scikit-image's own data registry describes both as "No copyright restrictions. CC0 by the photographer" (Stefan van der Walt and Rachel Michetti respectively). They are real photographs, used so that the classification check is semantic rather than synthetic.

### Separately provisioned engines

TPOT (LGPL-3.0), H2O (Apache-2.0) with the `jdk4py` OpenJDK 17 runtime (GPL-2.0 with Classpath Exception), AutoGluon Tabular (Apache-2.0), auto-sklearn (BSD-3-Clause) and chronos-forecasting (Apache-2.0) are installed by the operator from the package index into the application home. They are not linked into, imported by or redistributed with EnergyBaseline, which communicates with them only through files and a child process. Each runtime carries the licence files of its own distributions. Licence names are those in the installed distribution metadata; this document is not legal advice.
