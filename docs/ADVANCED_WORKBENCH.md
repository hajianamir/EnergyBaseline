# Advanced search, forecasting, vision, planning and simulation

## What is executable

Twelve new operations expand the native exploratory workbench from 20 to 32 operations. They are available through the same API, CLI and isolated spawn-worker Setup panel. Nineteen synthetic jobs in `examples/advanced/manifest.json` cover all six forecasting architectures, genuine neural architecture search, actual PNG loading and every new operation. No dependency is added; these implementations execute the already hash-locked Optuna, PyTorch, SciPy, scikit-learn and Pillow backends.

Verification status, tested source and actual Windows/Linux receipts are recorded in `artifacts/capabilities/advanced-stage4/README.md`. Do not substitute earlier-stage CI for this source. The complete original backlog is **not** made complete by these additions: named external AutoML packages, pretrained foundation forecasting, general pretrained vision systems and accepted main-baseline lifecycle integration are not silently claimed.

```bash
python -m energybaseline.cli workbench examples/advanced/automl-search.json \
  --dataset examples/advanced/series.csv --out search-result.json
python -m energybaseline.cli workbench examples/advanced/forecast-patchtst.json \
  --dataset examples/advanced/series.csv --out forecast-result.json
python -m energybaseline.cli workbench examples/advanced/image-classification-files.json \
  --out vision-result.json
python -m energybaseline.cli workbench examples/advanced/route-optimize.json \
  --out routing-result.json
python -m energybaseline.cli workbench examples/advanced/digital-twin.json \
  --dataset examples/advanced/series.csv --out twin-result.json
```

Run from the repository root; image file roots in the supplied job are explicit relative roots. CLI exit 0 means a valid operation/result was produced, **not** that an optimization problem is feasible, a neural model is accurate, or a twin is physically valid. Inspect the result status and actual metrics.

API: `run_workbench({"version": 1, "operation": name, "parameters": parameters}, frame)`; omit `frame` for specification-only jobs. In **Setup → Exploratory Workbench**, load the corresponding JSON and run it. Tabular jobs consume the current raw dataset. Specification-only jobs explicitly ignore the currently loaded tabular frame at the UI boundary and reject an implicitly supplied frame at the API boundary. No separate web server or image upload service is introduced.

All jobs retain finite-JSON, request/input hash binding, privacy-bounded error and immutability checks. File-image results additionally bind decoded pixel values and source file bytes. Configuration edits/data reload cancel or invalidate stale native results. The existing process pool supports cancellation at job boundaries and hard-stop fallback; these numerical libraries are not falsely described as interruptible at every inner iteration.

## AutoML and neural architecture search

### `automl-search`

Required: `time`, `target`, nonempty `features`, `train_rows`, `seed`. Optional: `engine` (only `optuna`), `trials` (2–40), `folds` (2–5), `gap` (0–100), `families`.

An actual seeded Optuna TPE study chooses model family and family-specific hyperparameters among ridge, random forest, ExtraTrees and histogram gradient boosting. Each expanding temporal inner fold fits a separate median imputer, scaler and estimator; the purge gap is explicit. All-missing training-fold predictors are rejected, not fabricated. Up to one trial per family is enqueued within the requested trial budget. The winning configuration is refit on the outer training prefix; the untouched suffix is evaluated once. No outer-holdout metric selects the family or hyperparameters. Target values are never imputed.

Outputs record actual study trials, inner MSE/RMSE, chosen parameters, final holdout RMSE/MAE, training-data digest and backend version. There are row/feature/fold-work bounds and one-thread native estimator execution. This is real **Optuna-backed AutoML**, not auto-sklearn, TPOT, H2O, FLAML or AutoGluon relabeled behind another engine name. Those named external engines remain unintegrated. A finite trial budget is not a global-best guarantee.

### `deep-forecast` with `architecture: "search"`

Required search list: two to eight explicit `{ "architecture": ..., "width": ... }` configurations. Every candidate actually initializes/trains its own neural network on an inner training prefix and scores a later training-only suffix. The winning architecture/width is then trained afresh on the complete outer training prefix. The holdout cannot select an architecture. This is genuine bounded weight-training/architecture selection, not just hyperparameter tuning under a NAS badge. It is not DARTS, evolutionary NAS or a named external NAS backend.

## Scratch-trained deep forecasting

`deep-forecast` requires `time`, `target`, `train_rows`, `architecture`, `seed`. Optional: historical-only `features`, `context` (4–96), even `width` (4–64), `epochs` (1–100), `learning_rate`, and NAS `search`. Inputs must be regular, strictly ordered unique timestamps with finite observed targets. There are at most 2,000 training rows, eight historical channels and 48 heldout steps, plus window and combined-search work budgets.

Training windows contain only training-prefix observations; each target window ends within that prefix. Scaling is learned only from the training prefix. All heldout steps are forecast at a single origin, without feeding observed holdout targets or future covariates back into the network. N-HiTS and DeepAR variants are univariate; unsupported exogenous inputs are rejected, not silently ignored. CPU RNG/thread settings are scoped/restored, and all weights remain in memory. Weight digests, parameter count and actual first/last training objectives are returned; raw series values and weights are not exported.

| Identifier | Actual implemented network | Explicit boundaries |
|---|---|---|
| `patchtst` | Channel-independent nonoverlapping patch projection, learned positions, transformer encoder and direct horizon head | Small reference network; no RevIN or research-package feature parity. Recent complete patches are retained for nondivisible context length. The target-channel-only readout/training objective means additional channels do not influence the target forecast in this reduced variant; use the covariate-mixing Informer/TFT/TiDE variants when that is required. |
| `informer` | Top-query sparsity scoring/updates, mean initial value context, convolution/pooling encoder distillation, masked generative decoder | Deterministic full-key query scoring is quadratic in context; **no** claimed full ProbSparse O(L log L) implementation or benchmark equivalence. |
| `tft` | Learned variable selection, LSTM processing, gated residual normalization, temporal multihead attention and direct horizon head | Historical-only gated-fusion reference. No static-variable encoder, known-future decoder inputs, full interpretable-attention decomposition or quantile TFT feature parity. |
| `nhits` | Multi-rate max pooling, residual backcast subtraction and interpolated hierarchical forecast knots | Three small univariate blocks, not every N-HiTS research configuration. |
| `tide` | Dense encoder residual/normalization, horizon decoder, per-step residual projection and history skip | Small historical-only dense reference, not all static/future covariate interfaces. |
| `deepar` | Recurrent Gaussian autoregressive likelihood, learned positive scale, training-only teacher forcing and recursive mean feedback | Univariate Gaussian reference; no global panel training, stochastic forecast sampling, count likelihood or calibrated prediction-interval claim. |

Training is actual gradient optimization with finite-loss checks and clipping, not a naive forecast returned under a neural label. However, short demo training can legitimately produce poor heldout metrics. No pretrained weights are downloaded. These are **reduced research-inspired reference variants**, not integration of PatchTST/Informer/PyTorch Forecasting/GluonTS packages or pretrained foundation models. They are separate exploratory jobs, not silently added to the accepted main candidate registry.

No inferential tests or uncertainty intervals are computed. The DeepAR training likelihood is an optimization objective, not a frequentist hypothesis test or a distribution-free time-series guarantee.

## Image learning and OCR

Specification-only operations: `image-classification`, `image-segmentation`, `image-detection`, `template-ocr`. These consume actual image pixels, not numeric time-series convolution rebranded as vision.

### Image arrays and real files

Learning jobs require `labels`, `train_images`, `seed`, and either:

- `images`: grayscale `[N,H,W]` or NCHW `[N,C,H,W]`, one/three channels, finite pixels in [0,1]; or
- `image_files` plus explicit `image_root`, optionally `image_size: [H,W]` and `color_mode: "L" | "RGB"`. PNG/JPEG/BMP paths must remain under the root. Traversal, UNC, absolute child paths and symlinks/reparse points are rejected by the shared path guard. Images are EXIF-oriented, converted and resized in memory using Pillow. File contents and resulting pixels are hashed; metadata/text is not exported.

There are 8–256 images, 8–32 pixel sides, 10 MB per file, 50 MB total source bytes, four-million decoded pixels per source file, one frame only, and an image-training pixel-work budget. `epochs`, `width` and `learning_rate` are explicit bounded options. The heldout suffix uses caller ordering. Exact duplicate images across the split are rejected; optional integer `sample_groups` must also be disjoint. This does **not** detect every near-duplicate or related subject: callers must supply meaningful groups.

Normalization uses training images only. Classification requires contiguous integer class positions present in training and no unknown holdout class. A genuine Conv2d classifier is trained with cross entropy; heldout accuracy, confusion matrix and per-class F1 are reported without raw labels or image pixels. Segmentation trains a two-level skip-connected U-Net with binary masks/BCE; output includes foreground IoU, Dice and pixel accuracy. The threshold is prespecified, never optimized on holdout. Empty-set IoU/Dice are explicit nulls rather than fabricated perfect scores.

Detection uses the **learned foreground segmentation** followed by connected components and a prespecified minimum component area. It reports boxes and IoU-0.5 matching precision/recall against components of the binary ground-truth mask. It is a binary foreground/component detector, **not** YOLO, Faster R-CNN, semantic multi-class detection or instance segmentation of touching foreground objects. At most 20 boxes per image are exported; metrics use all bounded components.

### `template-ocr`

Required: grayscale `image`, explicit grayscale `templates` array, threshold; optional maximum Hamming distance. The pipeline segments separated dark printed glyphs by occupied columns, crops/resizes glyphs and performs actual template matching. Ties and out-of-threshold matches are explicit unknowns. The result contains **template positions**, distances and counts, not recognized plaintext or source pixels. The caller can map positions against its own template bank outside the exported report. This is deliberately bounded printed-template OCR, not neural OCR, handwriting, touching glyphs, arbitrary languages or a pretrained OCR engine. No passwords/text from images are automatically written to logs/results.

## Advanced operations research

### `route-optimize`

Required: square `costs`, `demands` with zero depot demand, positive `vehicle_capacity`, `vehicles`. Optional time limit (0.01–60 seconds). There are 2–12 customers and 1–4 vehicles. Costs may be asymmetric but must be finite/nonnegative with zero diagonal; demands/capacity must be explicitly rescaled into supported numeric ranges.

An actual vehicle-indexed capacitated vehicle-routing MILP enforces exactly-once customer coverage, vehicle flow/depot continuity, capacity and MTZ subtour elimination. It executes HiGHS, independently checks variable/constraint feasibility, reconstructs depot-to-depot routes and independently rechecks coverage, capacity, connectivity and cost. It returns `optimal`, `limit_reached`, `infeasible`, etc.; a time-limited incumbent is never labeled proven optimal. No incumbent means no fabricated route/cost. No time windows, split delivery, pickup-delivery or traffic prediction is claimed.

### `schedule-optimize`

Required `jobs`: ordered lists of `{ "machine": integer, "duration": positive_number }`. Optional per-job `release_times`, `deadlines`, time limit. Up to 15 jobs, ten operations per job and 60 total operations. An actual disjunctive job-shop MILP minimizes makespan with precedence, shared-machine nonoverlap and timing constraints. Repeated machine visits are supported. Every incumbent is independently checked algebraically and for machine overlap; impossible deadlines produce infeasibility, not a synthetic schedule. This is not workforce calendars, setup-dependent durations, preemption or a general constraint-programming language.

### `evolutionary-optimize`

Required finite decision `lower`/`upper`, 2–4 minimization `objectives`, `seed`. Each objective explicitly supplies diagonal `quadratic`, `linear`, `bias`. Optional linear inequalities `matrix @ x <= constraint_upper`, population (even 8–80), generations (1–100). No executable user objectives are evaluated.

Actual constrained NSGA-II performs feasibility-first nondominated sorting, crowding distances, binary tournaments, simulated binary crossover, bounded polynomial mutation and elitist parent/offspring selection. Objective evaluation counts reflect initial population plus generated offspring; retained parents use cached fitness. The final feasible nondominated points/objective values are returned, or an explicit no-feasible-solution-found status. A nondominated population is **not** proof of the global Pareto frontier or a general black-box optimizer.

## Simulation and frozen twin validation

### `stock-flow-simulate`

Required `initial`, `stoichiometry`, `rate_matrix`, `constant_rates`, `duration`, `steps`. Rates are the explicit linear function `rate_matrix @ stocks + constant_rates`, and derivatives are `stoichiometry @ rates`. Actual DOP853 integration uses fixed tolerances; no arbitrary expressions run. Nonnegative-stock policy rejects negative trajectories rather than clipping. Column-sum conservation is checked structurally and numerically. At most 30 stocks, 60 flows and 1,000 output steps, with a dynamics growth/stiffness budget. This is linear stock-flow system dynamics, not an unrestricted nonlinear simulator.

### `agent-simulate`

Required agent count, undirected contact `edges`, initial infected indices, transmission/recovery rates, step size, steps and seed. Optional ordered step interventions change transmission multipliers. This is a genuine synchronous individual SIR simulation: exposure depends on infectious graph neighbors, rates become transition probabilities through `1-exp(-rate*dt)`, and all decisions use the previous state. New infections do not transmit/recover within the same step. Output contains aggregate S/I/R trajectories, not individual states, and enforces population conservation. It is not a calibrated epidemiological prediction or a general Mesa/agent-programming integration.

### `digital-twin`

Tabular required `time`, disjoint `states` and externally prescribed `inputs`, `train_rows`, `ridge`; optional `time_unit_seconds`. A discrete affine state transition and control matrix are calibrated only on training-prefix transitions. Validation is a frozen rollout from the final training state using explicit control inputs; observed holdout states never feed back. Unregularized rank-deficient designs fail closed. Outputs include transition/control coefficients, spectral radius, training digest and actual per-state holdout errors. There is no automatic online recalibration, physical-validity certification, baseline replacement or monitoring-history mutation. This is a **linear state-space twin candidate**, not a universal digital-twin platform.

## Unchanged governance and remaining work

All these operations are exploratory/descriptive decision support. They never accept gates, unlock TCP, execute inferential tests, impute targets, promote/refit a locked baseline or inject results into baseline ReportModel renderers. File readers do not load user pickles or arbitrary model/code artifacts. Requests/references/models/pixels are not automatically added to `.ebproj`.

Remaining named external AutoML engines, pretrained foundation forecasting, complete research-model feature parity, general pretrained vision/OCR/object detection, richer routing/scheduling domains and unrestricted simulation are explicitly retained in the capability backlog. Other catalog families (causal, NLP, survival, etc.) are not declared complete by this stage. Full-program completion remains false until their real implementation and evidence exist.
