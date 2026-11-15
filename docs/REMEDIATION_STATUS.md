# Architecture review remediation — staged implementation

> Subsequent implementation: see `docs/REMEDIATION_REMAINING_IMPLEMENTATION.md`, `docs/REMEDIATION_NATIVE_EXECUTION.md` and `docs/SDK_AND_EXTENSION_CONTRACTS.md`. Earlier checkpoint results apply only to their recorded source snapshots.

> Historical first checkpoint: see `docs/REMEDIATION_RECHECK.md` for the subsequent re-review, fixes, raw-fold evaluator and current remaining gates.

Status: **in progress; not a release acceptance statement**.

The architecture review under `artifacts/architecture_review/` describes the original
reviewed source. Its integrity inventory must not be interpreted as a manifest of
these later implementation changes. Existing tests, LICENSE, and
`artifacts/review_baseline/` are unchanged.

## Implemented in this working tree

### Connection security and storage policy

- Database URL diagnostics retain only a recognized driver, not userinfo, host,
  database paths, query names/values, or nested ODBC strings. Internal temporary
  SQLite fallback paths remain visible for local storage diagnosis.
- Driver/migration/audit failure diagnostics omit exception messages, which may
  contain connection strings or SQL parameter dictionaries.
- `EB_DB_STRICT=true` requires external storage and fails before local database
  fallback. An explicit `get_engine(strict_external=True)` also refuses a cached
  local engine. Invalid non-empty boolean configuration is rejected.
- Credential encryption uses Windows DPAPI or supported OS keyring providers.
  Arbitrary working third-party keyrings are not automatically considered secure.
  Without a supported functioning store, credentials are session-only. No new
  master-key file is created by default.
- `EB_ALLOW_INSECURE_KEYFILE=true` is an explicit development opt-in, **not secure
  OS storage**. Legacy private keyfiles can be read without creating replacement
  keys. A newly available OS key does not prevent trying an existing legacy key
  when decrypting old ciphertext.
- The connection dialog defaults to not remembering credentials. Metadata can be
  saved while credentials remain in process memory; they are not restored after
  restart. `save_profile` retains its old API default for compatibility.

### SQL execution contract

- The query builder returns frozen `QuerySpec(sql, parameters)` rather than
  interpolating date literals. SQLAlchemy uses named parameters; the ODBC adapter
  compiles named parameters into positional qmark bindings.
- Simple dotted identifiers, ISO date ranges, timezone compatibility, and limits
  are validated. `sqlglot.parse` still validates the read-only AST.
- The monitoring editor retains the binding object only while its SQL remains
  unchanged. Edited SQL cannot silently reuse unrelated builder parameters.
- Real read-only SQLite execution is tested. Rendering/validation for SQLite,
  PostgreSQL, MySQL and TSQL is tested. **Live PostgreSQL/MySQL/SQL Server/ODBC
  execution has not been demonstrated in this environment.**

### Statistical correctness and inference authorization

- Integer lag/rolling settings no longer undergo tuple unpacking.
- Purge gaps are no longer silently reduced to fit short histories, including
  nested ensemble folds and autocorrelation gap recommendations. An infeasible
  split must fail/skip rather than advertise an embargo it did not enforce.
- Feature-engineering deployment medians are not injected into the matrix passed
  to cross-validation. **This is not the complete global preprocessing leakage
  repair**; see the open work below.
- Core orchestration does not invoke the inferential test catalog, residual
  inferential diagnostics, Shapiro test or Kendall trend test without an accepted
  VSG and accepted, preregistration-locked TCP supplied to that orchestration.
  Descriptive runs emit explicit skip records with no p-values. Accepted panel
  settings are forwarded to the catalog when inference is authorized.
- WAPE is calculated as absolute error sum divided by absolute target sum; it is
  no longer fabricated from sMAPE for a parallel ranking-sensitivity rubric.
- Sensitivity now perturbs the actual production ranking criteria and normalized
  weights. Inapplicable zero-weight criteria remain zero in every scenario.

### Explicit domain policy

New desktop configuration defaults (`default_config`) resolve `domain_pack=AUTO`
to `neutral`. The new SDK entry point never infers a domain from dataset names:

```python
from energybaseline.sdk import analyze

run, bundles = analyze(frame, "timestamp", ["quality_offset"])
energy_run, energy_bundles = analyze(energy_frame, "timestamp", ["consumption"], domain_pack="energy")
```

- Neutral policy supplies no universal CVRMSE/NMBE/R2 acceptance limits. Explicit
  user limits remain available. No configured limits means acceptance was **not
  assessed**, not that a standard was passed.
- Neutral competition excludes degree-day and TOWT models and gives energy
  physical constraints and ASHRAE criteria zero weight. Outdoor temperature names
  alone do not activate an energy pack. EDA does not describe negative process
  targets as meter/sensor problems under neutral policy.
- Energy selection enables temperature/degree-day behavior. Built-in acceptance
  limits in the new energy policy apply to monthly and hourly calibration only;
  unsupported daily/weekly/subhourly/quarterly intervals require explicit limits.
  These limits are application policy, not a certification of standards compliance.
- The old `core.pipeline.run_analysis` entry point, when called without the new
  domain setting, retains **recorded `legacy_energy` behavior** and emits a
  deprecation warning. It is a compatibility facade, not the neutral SDK default.
  Existing monthly energy tests continue to exercise that old contract.
- Reports use a neutral title for neutral runs, through the ReportModel adapter.
  Renderers remain ReportModel-only; PDF generation remains ReportLab-based.
- A real non-energy signed process-target analysis exercises the neutral SDK,
  explicit integer lag/rolling configuration, lack of energy limits/models, and
  actual ranking-sensitivity agreement.

### Artifact loading

Missing, malformed or mismatched integrity sidecars now fail before stored-run
or bundle deserialization. **A sidecar is not authentication or a safe serializer.**
Only locally trusted joblib artifacts may be loaded. Authenticated provenance and
an explicit external-artifact trust/migration contract remain open; do not market
this integrity check as protection against an attacker replacing both files.

## Open exit gates (not delivered)

1. Fit *all* learned quality filtering, target-informed feature selection,
   imputation, balance points, categorical vocabularies and transformations inside
   each training fold, including inner HPO/ensemble folds. The historical core
   pipeline still learns several of these globally. Its scores must not yet be
   described as fully leakage-free.
2. Consolidate desktop worker, CLI and both pipeline families onto one evaluator,
   split and domain-policy contract. In particular, the separate CLI portfolio
   still has energy-specific metric/locking defaults. Forward validated VSG/TCP
   authorization through worker boundaries without trusting configuration flags.
3. Bind authorization to input data, selected variables, and locked TCP identity
   consistently across all entry points. Verify that no other standalone legacy
   test/diagnostic path can bypass its intended authorization boundary.
4. Finish the neutral role/unit service and explicit migration of the energy
   schema/VSG name heuristics; validate contrasting domain packs beyond the
   demonstrated process-regression example.
5. Complete authenticated artifact trust, stable versioned SDK/plugin contracts,
   typed result boundaries and durable acceptance/ranking schema migration.
6. Exercise real external databases and native secure stores, run desktop GUI
   acceptance, and obtain raw evidence for the exact final source on both required
   operating systems. Linux sandbox evidence alone cannot satisfy two-OS E3.

## Verification evidence

New behavioral regression tests are in:

- `tests/gates/test_security_correctness_remediation.py`
- `tests/gates/test_domain_policy_remediation.py`

Raw verification output is recorded under `artifacts/remediation/`. Each log names
its command and records whether it predates later changes. Initial exploratory
runs are not evidence that the final working tree passed a full suite.

The dependency environment was installed with
`pip install --require-hashes -r requirements.lock`. Native Qt import initially
failed because this sandbox lacks `libGL.so.1`; no GUI success or Windows result
is inferred from headless tests. No release completion or defect-free guarantee
is made by this checkpoint.

### Verified checkpoint results

- Final headless suite: **333 passed in 516.48 seconds**, exit code 0
  (`pytest-headless-checkpoint.txt`). This includes 57 new remediation cases.
- Focused security/domain/storage/validation/scoring regression group:
  **79 passed in 23.73 seconds** (`pytest-focused-checkpoint.txt`).
- Static/integrity checks: **11 commands, zero failures**
  (`static-checkpoint.txt`), including pip consistency, Ruff lint/format,
  Pyflakes, compilation, all four guards, whitespace, and unchanged tracked
  tests/LICENSE/frozen baseline artifacts.
- Product/test source hashes were rechecked after the final suite: **no drift**
  from `source-manifest.json`.
- The initial 318-pass log predates subsequent changes and is retained as an
  intermediate result, not final-source acceptance evidence.
- Native QtWidgets import remains blocked by missing `libGL.so.1`
  (`gui-environment-probe.txt`). GUI, coverage and two-OS E3 acceptance are not
  claimed. This remains an incomplete staged remediation, not a release.
