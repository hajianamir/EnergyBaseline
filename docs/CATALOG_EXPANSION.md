# Stage 6: NLP, survival regression and graph learning

## Scope and acceptance

Four new executable operations extend the existing API, CLI and isolated native workbench worker. Five public synthetic jobs live in `examples/catalog/manifest.json`. They require no new package or dependency downgrade. Final-source local and actual Windows/Linux/frozen verification is recorded separately in `artifacts/capabilities/catalog-stage6/README.md`; implementation or development test success alone is not acceptance.

This is another implementation increment toward the requested complete catalog, **not a redefinition of that request as four operations**. External backend conflicts, pretrained foundation/vision assets, full-paper reproduction and every unimplemented per-row scope remain open. In particular this stage is not an LLM, translator, pretrained language model, causal inference engine or a survival inference authorization bypass.

All jobs have `version: 1`, `operation` and `parameters`. Tabular inputs must have unique named columns. No job persists models, accepts/changes/refits a baseline, emits p-values/intervals, imports user code, deserializes external models or downloads weights. Report renderers are unchanged.

## `text-classification`

Required parameters: `text`, `target`, `time`, `train_rows`. Optional: `gap` (default 1), `max_features` (default 2000).

Fits actual scikit-learn TF-IDF and logistic regression on rows `[0, train_rows-gap)`. Rows in the gap and outer holdout never fit vocabulary, IDF or classifier parameters. The `time` column must contain unique strictly increasing explicit timestamps. Text, label and time columns must be distinct. Uses 2..20 training classes with at least five training documents per class. Every holdout class must occur in training; a missing holdout class does not invalidate evaluation. Class ordering is first occurrence in training and output contains positions only. The binary logistic special case is supported alongside multiclass softmax.

Outputs holdout accuracy, macro-F1, log loss, confusion counts, training/vocabulary/model hashes and document/feature counts. Neither documents, vocabulary words nor target labels are exported. The vocabulary digest is an identity checksum, **not anonymization or encryption**. Applications must still treat source-bound results as sensitive metadata. Holdout documents with no known tokens use the actual model's intercept, are counted explicitly and never expand vocabulary.

Limits: 5000 documents, 10000 characters per document, two million total characters, 10..10000 maximum vocabulary size and 500000 total nonzero TF-IDF entries. Empty/missing/nonstring text is rejected. A real convergence warning fails closed rather than returning an unconverged fit as successful.

## `text-topics`

Required parameters: `text`, `time`, `train_rows`, `topics`. Optional gap/vocabulary settings and temporal/text limits match classification. Fits actual nonnegative matrix factorization with deterministic NNDSVDa initialization on the training TF-IDF matrix, then transforms only the outer holdout. Topic count is 2..20, strictly below both training matrix dimensions; factorization capacity is bounded. No topic is claimed to be a pretrained semantic class or a human interpretation.

Returns nonnegative aggregate topic mass, mean normalized holdout weights, training/holdout reconstruction errors and component/vocabulary/training hashes. No token lists, document text or individual document assignments are exported. A zero-vocabulary document has zero topic mass and is counted; it is not silently assigned a topic. Reconstruction errors use actual residual squares in bounded 32-row blocks, never a full dense document-by-vocabulary matrix. This avoids the cancellation in the expanded sparse norm identity that can make an almost exact fit report NaN on some BLAS/SVD bases. No learned component, convergence check, threshold or iteration budget is changed.

## `survival-regression`

Required parameters: `duration`, `event`, `features`, `time`, `train_rows`, `method`, `horizons`. Optional: `entry`, `gap` (default 1).

- `cox_breslow`: maximizes the actual statsmodels PHReg log partial likelihood using its analytic score and SciPy L-BFGS-B. Breslow handles tied failures. Does **not** call `PHReg.fit`, compute covariance matrices, perform significance tests or access inferential summaries. The training-only Breslow cumulative baseline hazard includes delayed-entry risk sets.
- `weibull_aft`: maximizes the Weibull accelerated failure-time conditional likelihood with an analytic gradient. Observed failures contribute log density; right-censored subjects contribute log survival; left truncation subtracts log survival at entry. This is not treating censored durations as observed regression outcomes. Independent SciPy density/survival and finite-difference gradient oracles are in the tests.

Both fit scaling and parameters on `[0, train_rows-gap)` only. Duration/event/time/optional entry columns must be distinct from predictors and each other. Durations must be positive and at most 1e9; entry must satisfy `0 <= entry < duration`. Outcomes require real numeric 0/1, not strings, booleans or complex numbers. There must be at least ten training events and five per selected predictor. At most ten predictors and one predictor per twenty training observations; collinearity and constant predictors fail closed. At most 2000 rows. Optimizer failure, active parameter bounds and nonfinite numeric capacity fail closed.

Returns mean holdout survival conditional on survival to each subject's entry, at 1..30 positive strictly increasing declared horizons. Horizons must be at or after every test subject's entry and cannot exceed maximum training follow-up. No individual subject identity/prediction or unrestricted baseline tail extrapolation is exported. Harrell concordance compares only observed earlier failures with subjects who were already at risk; tied event times are not ordered pairs, tied risks receive half credit. With no comparable pairs it returns null and zero pairs, not a fabricated score. Concordance is **not** an IPCW/calibration metric. Assumptions are independent noninformative censoring/truncation and a proportional-hazards or Weibull AFT specification. No causal, competing-risk, inferential or autocorrelation-robust coverage claim is made.

## `graph-node-classification`

Required parameters: `features`, `target`, `train_rows`, `edges`. Optional: `epochs` (80), `width` (16), `learning_rate` (0.02), `seed` (42).

Executes a real two-layer PyTorch graph convolutional network on CPU with `D^-1/2 (A+I) D^-1/2` normalization, ReLU, cross-entropy and Adam weight decay. Targets cannot enter numeric features. Training labels are the prefix `[0, train_rows)` only. Every test class must be represented in training; at least three training nodes per class, 2..10 classes. Normalization mean/scale are fit on training nodes only.

This is explicitly **static transductive** evaluation: full declared graph topology and all node features are visible during training message passing. Test labels are excluded from gradients. It is not an inductive/unseen-node, temporal or held-out-edge protocol; model changes when topology or test-node features change are expected, not hidden as training-only behavior. No claim of matching citation-dataset benchmarks or the complete original paper experiment is made.

`edges` is a list of undirected integer node-position pairs; every non-self edge must occur once. Duplicate/reversed edges, booleans, out-of-range positions and caller self-loops are rejected; self-loops are added exactly once by the implementation. Disconnected/isolated nodes are allowed. Limits: 10..1000 nodes, 10000 edges, 32 features, 1..300 epochs, width 2..64 and an explicit dense message-passing computation budget. CPU random state/thread count are serialized/restored through the existing Torch context. Outputs losses, test accuracy/macro-F1/confusion counts and weight/topology/label/prediction hashes, never raw labels or persisted weights.

## Earlier defect corrected

The shared binary-input helper previously used equality membership followed by `astype(int)`. This accepted booleans without a warning and complex zero/one values with a `ComplexWarning` but no rejection, contrary to the workbench's explicit-real numeric policy. `binary-coercion-reproduction.json` reproduces both behaviors using the actual earlier Git blob and verifies the corrected rejection. It now rejects boolean, complex and object/string binary inputs before conversion, while preserving actual integer/float and nullable real zero/one inputs. Fresh regressions cover both rejection and preservation. Callers that previously supplied booleans must explicitly encode numeric 0/1 before submission; no automatic coercion hides this change. Mandatory historical baseline tests are untouched.

## Native build profiles

GCN and the earlier Torch-based forecasting/vision models require the existing **full** build profile (the build script default). The intentionally Torch-free **lite** profile cannot execute them. CI retains its mandatory lite installer/runtime/external-engine gates unchanged and adds a separate full **windowed** bundle for all five new jobs. The separate diagnostic workflow also executes the new jobs through a full console bundle; console success never replaces windowed acceptance. No optional-backend omission is relabeled as engine success.

## Actual verification

`tests/test_catalog_expansion.py` covers real fitting/semantic oracles, train/holdout and purge isolation, input immutability, malformed inputs, analytic gradients, risk-set ties, delayed entry, concordance, privacy, real independent CLI processes and Torch-state restoration. `tests/gui/test_catalog_expansion_gui.py` runs all five jobs in actual spawned native workers with accepted input-bound VSG/TCP objects preserved and stale results invalidated. CI independently executes the semantic-oracle script on Linux and Windows and again through the real **windowed** Windows executable. Existing coverage gates, dependency lock, baseline integrity and other checks are retained.
