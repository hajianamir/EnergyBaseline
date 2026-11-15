# Second-pass remediation and raw-fold evaluation

> Subsequent implementation: see `docs/REMEDIATION_REMAINING_IMPLEMENTATION.md`, `docs/REMEDIATION_NATIVE_EXECUTION.md` and `docs/SDK_AND_EXTENSION_CONTRACTS.md`. Earlier checkpoint results apply only to their recorded source snapshots.

Status: staged implementation, not release completion. This document supersedes
implementation-status claims in the first checkpoint where they differ.

## Re-review findings corrected

1. **Non-finite evidence could pass acceptance.** NaN comparisons previously did
   not fail configured limits. Non-finite validation error metrics now fail
   qualification; undefined R2 cannot pass a configured R2 requirement. Numeric
   subscores remain finite instead of poisoning the composite score.
2. **Sensitivity did not exactly reproduce the production shortlist.** Equal
   scores used a different tie rule, and fewer than five qualified models could
   be padded with rejected models. Production and sensitivity now share the
   score/name tie contract and the qualified-only / one-information-only fallback
   shortlist rule. Invalid perturbations and non-finite criteria are rejected.
3. **Credential-bearing objects exposed values in repr.** Passwords, opaque ODBC
   strings, query templates and query parameter values are excluded from generated
   representations. This complements diagnostic sanitization; it does not make
   arbitrary Python objects safe to log.
4. **Negative/fractional lags could create future-looking features.** Lag/window
   settings now require positive integral values, including when building a loaded
   FeatureSpec. Zero, negative, fractional, infinite and boolean specifications
   are rejected rather than silently truncated or shifted into the future.
5. **Boolean gate state was insufficient authorization.** Accepted VSG is now
   bound to a captured input-frame hash (values, dtypes, columns and timestamps),
   targets and time column. Locked TCP is checked against its actual current
   VSG/TCP canonical preregistration digest, not only a nonempty saved hash. Direct
   post-lock mutation or reuse with changed input fails closed in orchestration.
6. **Outlier removal inside a training fold can null target observations.** The
   new evaluator explicitly removes those labels from the training fit; it does
   not pass NaN targets to estimators. The untouched validation population remains
   present for honest error evaluation.
7. **pandas 3 rejects fractional updates to integer predictors.** Fold-local
   history matrices now explicitly promote numeric model columns before applying
   training-only imputation/clipping. Categorical history uses object dtype.
8. **A legitimate kernel winner exposed an old representation mismatch.** Kernel
   and network descriptions now state the honest mapping `ŷ = fitted_model(X)`
   and require the executable artifact. They remain explicitly nonparametric;
   diagnostic surrogate equations remain labeled as approximations, not the
   actual prediction formula. Models were not removed or artificially demoted to
   satisfy an old test.

## Raw-fold evaluator

`energybaseline/core/evaluation.py` introduces `RawEvaluationPlan`:

- Snapshot the temporally normalized raw calibration input. Only explicit,
  declared NRE exclusions and missing target labels affect the evaluation
  population before splitting. Learned outlier filtering does not remove
  validation observations.
- Generate outer splits through the shared walk-forward generator. Purge sizing
  uses conservative upper bounds on AUTO lag/rolling memory, not memory selected
  using all calibration labels. An infeasible embargo is not shrunk.
- For every training fold, fit quality screening, outlier policy, category
  vocabulary, feature configuration/balance points and feature selection using
  only that training subset. Transform validation without its target column.
- Preserve the temporal grid for predictor lags/rolls, including timestamps whose
  target is missing, instead of compressing training segments together.
- Prepare explicit Optuna inner folds independently from raw data. Candidate
  design/scaler fitting occurs inside each inner fit. No stride-based gap
  approximation is used by this evaluator.
- Fit NNLS ensemble weights on independently prepared inner OOF predictions, then
  score each outer validation block without fitting on that block's labels.
- Fit the deployment model separately on the final cleaned calibration data.
  Alignment to that final training index does not redefine the raw OOF metrics.
- Persist fold audit data and a raw validation trace for shortlisted models.
  Metrics can be recomputed from that trace even when final training excludes
  observations. Both are passed through the ReportModel adapter as artifacts.

These changes address the historical core outer-fold preprocessing leakage and
explicit nested tuning/weight-estimation paths. They do **not** imply that every
third-party estimator's internal CV, every legacy entry point, or repeated model
selection on the same CV evidence has become an independent holdout experiment.
Blocked CV deliberately permits later training blocks; it is not a claim of
causal forecasting validation.

## Entry points and authorization transport

- Desktop and SDK continue through the same core orchestration, now using the raw
  fold plan. Both accepted gate objects can cross the existing bounded/hash-checked
  spawn transport. Workers validate them against the actual unpacked data rather
  than trusting a configuration flag. Gate profiling uses the same unit-normalized
  frame as desktop analysis.
- `run_in_pool` forwards the gate objects as well as direct `submit_analysis`.
- The new CLI command uses the SDK and the same evaluator, with a neutral default:

```bash
python -m energybaseline.cli analyze process.csv --time-col timestamp --targets quality_offset --config config.json
```

  Energy requires `--domain-pack energy`. `--inference` is an explicit CLI action
  accepting VSG and locking TCP for the actual inputs; no inference is requested
  by default. The command returns versioned JSON, not executable model pickles.
- `scoring-register` remains a deprecated compatibility command for the historical
  energy portfolio and emits an explicit warning. It has not been relabeled as
  the new evaluator. Other legacy energy helpers and baseline-lock defaults still
  require migration rather than silent behavior changes.

## External artifact trust

A matching manifest/sidecar hash is integrity evidence, **not origin approval**.
The locked-baseline loader now permits implicit loading only for record digests
issued by application code in the current process. Reloading a manifest from a
directory never adds it to that trusted set.

Other locked artifacts require an independently trusted record digest:

```python
manager.load_verified_artifact(record, trusted_record_sha256=approved_digest)
```

CLI monitoring exposes `--trusted-record-sha256`. Do not automatically copy this
value from the same untrusted manifest: obtain it from an independently trusted
channel. After restart, legitimate persisted external records also require this
approval. This is an intentional fail-closed migration behavior.

The loader hashes the exact byte buffer subsequently passed to joblib, closing
the hash-then-reopen gap for that path. Joblib remains executable Python
serialization. This is not a sandbox, signature authority, safe plugin loader or
safe arbitrary project import. Ordinary application run/bundle storage still
assumes locally trusted application storage and uses mandatory sidecar integrity;
its broader origin/migration contract remains open.

## Verification

Behavioral proofs are in these new test modules; existing tests were not edited:

- `test_remediation_recheck.py`: invalid evidence, exact shortlist/sensitivity
  agreement and credential repr redaction.
- `test_raw_fold_evaluation.py`: held-out-label canaries, unseen categories,
  unchanged validation population, nested HPO, positive-memory enforcement,
  independently prepared ensembles, integer predictor treatment, CLI execution
  and raw validation trace/metric reproduction.
- `test_inference_authorization_recheck.py`: input/specification binding and a
  real spawn worker receiving locked authorization and executing authorized tests.
- `test_external_artifact_trust.py`: artifacts created in a separate process,
  including an executable-on-load canary that is blocked despite valid hashes.

Raw evidence is under `artifacts/remediation/recheck/`. `pytest-headless.txt` is an
intermediate run: **355 passed, 2 failed**. Those failures exposed the integer
assignment and kernel-description issues above; they were not suppressed. Later
source changes also mean that log is not final-source acceptance evidence.
`pytest-final-headless.txt` is the subsequent frozen-source run:

- **362 passed in 499.39 seconds**, exit code 0 (headless, excluding `tests/gui`).
- Focused six-module remediation suite: **86 passed in 24.42 seconds**.
- Static/integrity verification: **11 commands, zero failures**.
- Product/test Python source manifest rechecked after completion: **no drift**.
- Existing tracked tests, LICENSE and frozen review baseline: unchanged.
- Native QtWidgets probe: failed on missing `libGL.so.1`; not GUI acceptance.

A source hash manifest is retained separately from the first checkpoint's
manifest. Static source review also confirmed `apply_units` belongs to
`SetupPage`; gate creation and analysis both use that same method. No speculative
wrong-class fix was applied, and this does not substitute for executing the GUI.

## Remaining acceptance gates

- Finish internal-CV estimator consolidation and the explicit migration of the
  other pipeline/legacy CLI families, including energy-only metric and lock
  defaults. Only the new analysis routes are unified here.
- Respect and migrate the complete domain-neutral role/unit and VSG decision
  contract; historical energy-name heuristics and some report terminology remain.
- Complete versioned acceptance/result/SDK/plugin contracts and external ordinary
  run/bundle import trust. No plugin marketplace/server expansion is claimed.
- Validate cancellation/performance at large data sizes: caching independently
  prepared folds trades memory for repeated-model runtime and needs production
  budgets. No large-scale performance claim follows from small regression cases.
- Test real external databases and OS secure stores, native GUI acceptance and
  the exact final source on both required operating systems. The sandbox still
  lacks the native `libGL.so.1` dependency for QtWidgets; no GUI/two-OS success is
  inferred from headless tests.
