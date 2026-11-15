# Remaining-work implementation checkpoint

> Later native execution and boundary fixes: see `docs/REMEDIATION_NATIVE_EXECUTION.md`.
> The Qt missing-library limitation below is historical; genuine Linux offscreen
> Qt now runs. Two-OS, secure-store and other release gates remain open. Results
> and source manifests in this document certify only this earlier checkpoint.

Status: implementation plus staged verification, not release completion. This
checkpoint follows `REMEDIATION_RECHECK.md`; older source manifests and test logs
do not certify this working tree. All changes remain on the active Arena branch.

## Work implemented

### Evaluation, tuning and bounded resource use

- Removed hidden RidgeCV/LassoCV/ElasticNetCV from the production portfolio.
  Regularization is now fixed conservative defaults when tuning is disabled, or
  explicit tuning through independently prepared temporal inner folds. Design
  selection and scaling are refitted inside each inner fold.
- Removed the stride-based embargo approximation in the compatibility candidate
  tuner. It uses exact shared temporal folds without resampling the time grid.
- Removed hidden estimator CV from the legacy portfolio wrappers too; their
  defaults are explicit, not disguised as independent validation. Existing F3/F4
  model families already used explicit regularization parameters.
- Neural estimators no longer make hidden unpurged validation tails after fitting
  preprocessing. They monitor training convergence and leave validation evidence
  to external temporal folds. Empty estimator validation curves are intentional;
  training loss is not relabeled as validation loss.
- Piecewise/TOWT transforms reuse fitted training medians instead of deriving new
  medians from the prediction batch.
- Raw evaluation's retained matrix cache is LRU-bounded by
  `evaluation_cache_mb` (default 128 MiB; zero disables). Oversize entries are
  recomputed, not retained. This budget excludes input snapshots, audit metadata,
  transient transform allocations and estimator memory; it is not a process RSS
  limit. Cancellation is checked before cached reuse, transformation stages and
  successive HPO/ensemble fits, not inside arbitrary native estimator calls.

### Inference authorization and cache correctness

- All three direct cached executors check the actual input-bound authorization.
  Their legacy accepted-only call shape explicitly freezes the unchanged accepted
  panel before computation, warns about migration and then validates the locked
  preregistration. Post-acceptance mutation is not silently preregistered.
- Cache identity uses actual observations rather than trusting caller-supplied
  dataset labels. Catalog cache keys now include residual values, seed, library
  versions and the complete TCP specification, not only OOF model names.
- Application EDA/residual significance tests are disabled instead of applying
  hardcoded thresholds outside the TCP policy. The canonical catalog owns
  inferential decisions. Its raw OOF residual input now uses the same evaluation
  population as reported validation metrics, not the smaller final fit population.
- Skipped results are constructed through `TestContractResult`, not separate
  incomplete dictionary schemas. Executed and skipped exports carry a version.

### Artifacts, lock integrity and credential handling

- Ordinary run/bundle joblib loading now requires current-process issuance or an
  independently approved artifact digest, in addition to mandatory sidecar
  integrity. Loading hashes the exact buffer later given to joblib.
- Desktop previous-run, bundle and baseline-lock routes expose explicit
  independent-digest approval; they never prefill approval from the adjacent
  untrusted manifest. Approval authorizes executable code, not merely data.
- Baseline selection no longer swallows immutable-lock write failures. The
  verified artifact/lock must exist before changing the active selection. Tests
  verify prior selection survives a persistence failure and missing artifacts.
- Lock creation defaults to neutral/no universal limits. Energy policy must be
  explicit; defaults only apply at recognized declared calibration intervals.
  Generic card contents do not claim energy standards despite retaining the
  historical serialized card field name for compatibility.
- Newly written OS-keyring master keys require read-back confirmation before the
  backend is labeled persistent. A nonpersisting provider falls back to session
  protection, not a false secure-store success.
- SQL driver exceptions are raised without their original SQLAlchemy exception
  chain: a formatted failure traceback no longer re-exposes bound sensitive
  values. A real SQLite driver-error regression verifies this behavior.

### Domains, public API and compatibility

- Added validated `AnalysisRequest`/`AnalysisResponse` v1 and a JSON-native SDK
  projection shared by CLI analysis. The tuple-returning SDK route remains
  supported through the same request validation.
- Built-in `water` and `manufacturing` policies deliberately reuse neutral
  statistics. They do not infer regulatory limits or physics from their names.
  Actual SDK/CLI runs demonstrate volume regression and signed manufacturing
  quality alongside explicit energy calibration.
- Neutral schema suggestions do not infer energy units, flow semantics or future
  availability from energy-like column names. VSG's energy-name priority/submeter
  heuristic is energy-only; mandatory affine/target/time integrity stays active.
- Extended the common physical-unit registry with pressure, mass and duration.
  Monitoring stores the pack/version, uses generic baseline ratios, and only
  provides energy savings/EnPI aliases for energy/legacy-energy bundles.
- Defined the bounded built-in extension trust contract and migration APIs in
  `SDK_AND_EXTENSION_CONTRACTS.md`. Arbitrary executable plugin discovery remains
  disabled, not advertised as sandboxed or production-ready.
- The Linux CI runtime is explicitly Ubuntu 26.04 while preserving historical
  matrix labels; checkout/setup-python use Node 24 action majors. No CI run is
  claimed without pushing/dispatching the exact source.

## Actual evidence

Evidence directory: `artifacts/remediation/remaining/`.

- `pytest-focused.txt`: seven remediation suites, **119 passed in 27.52 seconds**.
- `static-checks.txt`: **11 commands, zero failures**, including unchanged tracked
  original tests/LICENSE/frozen baseline, all four guards, Ruff and Pyflakes.
- `pytest-final-headless.txt`: **395 passed in 579.61 seconds**, exit code 0,
  on frozen final product/test/evidence Python source (GUI suite excluded).
  `final-source-manifest.json` identifies the source;
  `final-source-verification.json` rechecks all **199** recorded files with no drift.
- `pytest-headless.txt`: an intentionally stopped exploratory run after the CI
  logical-label compatibility check failed. Workflow was corrected without
  editing the original test. This log is not final-source acceptance evidence.
- `postgres-adapter.json` / `.txt`: **7 checks passed** against a real disposable
  PostgreSQL 16.2 process over TCP using the application SQLAlchemy/psycopg2
  adapter: date/value binding, injection-shaped values, table survival, row bound,
  read-only transaction state, AST write rejection and DB write rejection even
  when the AST wrapper is bypassed. Not a hosted-service/TLS/ODBC certificate.
- `domain-proof/summary.json`: actual energy, water and signed-manufacturing runs
  with persisted raw inputs, raw OOF predictions, validated ReportModel exports
  and per-shortlist metrics. CLI and SDK agree on fold audits, selected features,
  metrics and scores. No inference was requested. The small synthetic fixtures
  are retained specifically as raw reproducible evidence, not user datasets.
- `domain-proof-script.txt`: exact reproducible Python input for that proof.
- `benchmark-1m.json`: existing one-million-row streaming/paging/downsampling
  benchmark passed. This is actual computational evidence but **not** full raw-CV
  training on one million rows, native GUI interaction or a production capacity
  guarantee. See the artifact for environment, memory scope and measured timings.
- `native-probes.json`: actual session-only credential roundtrip and loss of
  recoverability after restart passed; native secure-store and two-OS acceptance
  remain false. QtWidgets import failed due to missing native libraries.

The disposable PostgreSQL runtime came from the PyPI `pgserver==0.1.4` CPython 3.11
manylinux x86-64 wheel, SHA-256
`d595789b47624a3d963aa9aa6359da9be31beb7e61f1a45541953242068b8813`.
Only its real PostgreSQL binaries were used; it was not installed as a product
requirement. The server was stopped and its temporary runtime/data removed.
The reusable adapter proof is `scripts/evidence/remediation_postgres.py`.

## Remaining release acceptance, not replaced by mocks

1. **Native GUI/two OS:** QtGui lacks `libGL.so.1`, `libEGL.so.1`,
   `libxkbcommon.so.0` and `libdbus-1.so.3` here. Execute native desktop acceptance
   and the final source on both required operating systems. Workflow preparation
   is not successful execution evidence.
2. **Native OS credential stores:** real Windows DPAPI/Credential Manager and
   Linux/macOS secure-store durability/failure tests are still required. Current
   evidence proves session fallback and provider-failure unit behavior, not those
   OS implementations.
3. **Additional real adapters:** real PostgreSQL and existing SQLite evidence do
   not certify external MySQL, SQL Server or actual ODBC drivers. Their adapter
   unit tests are not substitutes for those deployment checks.
4. **Legacy APIs:** the old separate portfolio/validation/research APIs remain
   compatibility paths with historical metric/role contracts. They are not
   secretly redirected into a scientifically different evaluator. Retiring them
   requires an explicit version/removal decision and downstream migration.
5. **Capacity envelope:** no fixed RSS, native-fit interruption latency, or
   all-model raw-CV million-row performance claim is made. LRU matrix retention
   and existing streaming benchmarks cover narrower, explicitly stated scopes.
6. **External executable plugins:** runtime third-party discovery is deliberately
   unsupported until provenance, permissions and isolation contracts are approved.
   Built-in data policies and immutable report/unit contracts are implemented;
   arbitrary plugin execution is not a remaining safe-by-default toggle.

No full remediation, regulatory certification, defect-free, coverage, native GUI
or two-OS E3 completion claim follows from this checkpoint.
