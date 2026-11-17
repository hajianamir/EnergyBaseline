# Stage 7: isolated AutoML engines, foundation forecasting and pretrained vision

## Scope and status

Three workbench operations run real third-party engines and real pretrained weights. The registry now holds **42 operations**, 12 of them specification-only. Six example jobs live in `examples/isolated/manifest.json`. Contracts and limits are in `docs/ISOLATED_ENGINES.md`.

| Operation | Engine or model | Where it runs |
| --- | --- | --- |
| `external-automl` | TPOT 1.1.0, H2O AutoML 3.46.0.12, AutoGluon Tabular 1.6.3, auto-sklearn 0.15.0 | separately provisioned hash-locked runtime, own process |
| `foundation-forecast` | Chronos-Bolt tiny through chronos-forecasting 2.3.2 | separately provisioned hash-locked runtime, own process |
| `pretrained-image-classification` | GhostNet 1.0x ImageNet-1k weights | application worker, the application's PyTorch |

**The bounded Stage 7 scope is verified on source `14900def3dbcde425a292e2f0f47c9d0ca8c0d95`.** Local runs and hosted CI used that commit. The hosted engine workflow and the native diagnostics ran on its parent `05845c6ac2d2af28ae5ba3b3174392fc543232ff`; the only file that differs is `tests/test_isolated_engines.py`, which neither of those workflows reads (`source-equivalence.json`). They were not rerun because they trigger on product paths only and manual dispatch is refused for this integration. The application's own dependency lock is unchanged.

**This is not whole-catalog completion.** Every per-row `remaining_scope` and every untouched catalog family stays open, and `program_complete=false` remains mandatory.

## Execution evidence

Local runs used a fresh `git clone` of the commit. No test was skipped and no engine was mocked.

| Execution | Result | Evidence |
| --- | --- | --- |
| Complete local non-GUI regression | 952 passed; 633.49 s; exit 0 | `local-full.log`, `local-full.xml.gz` |
| Complete local GUI directory | 52 passed; 157.60 s; exit 0 | `local-gui.log`, `local-gui.xml.gz` |
| GUI coverage | 72.14%, unchanged 56% gate | `gui-coverage.xml` |
| Local execution tests against provisioned runtimes | 17 passed; 276.40 s; exit 0 | `local-provisioned.log`, `local-provisioned.xml.gz` |
| Local command-line receipts | 6 jobs, each executed twice with identical results, 0 refused | `local-python-engines.json`, `local-evidence.log` |
| Focused workbench and isolation tests | 348 passed; 96.79 s; exit 0 | `local-focused.log`, `local-focused.xml.gz`, `capability-coverage.xml` |
| Hosted engine workflow [43969676343](https://github.com/hajianamir/EnergyBaseline/actions/runs/43969676343) on `05845c6`, Ubuntu 24.04 job 133658216781 | success: five engines and both assets provisioned from the package index and the fixed origins, execution tests, command-line receipts | `hosted-isolated-*.json` |
| Same workflow, Windows job 133658216538 | success: four engines and both assets provisioned, execution tests, command-line receipts, full windowed bundle built, receipts repeated through the frozen executable | `hosted-isolated-*.json` |
| Hosted CI [43982762989](https://github.com/hajianamir/EnergyBaseline/actions/runs/43982762989) on `14900de`, Ubuntu and Windows | success on both: unchanged full-suite coverage gates (60/80%), GUI gate, and the existing lite and full windowed Windows packaging steps | `hosted-ci-*.json` |
| Hosted native backend diagnostics [43969676419](https://github.com/hajianamir/EnergyBaseline/actions/runs/43969676419) on `05845c6` | success | `hosted-native-*.json` |

`local-runs.json` holds the commands, exit codes after interpreter teardown and JUnit counts. `environment.json` holds the local interpreter, library versions, installed runtimes and how the assets were provisioned locally. `hosted-history.json` lists every workflow run of the Stage 7 commits, including the failed ones.

Coverage statements are limited to what was measured. `energybaseline/isolated/__init__.py` is 62% covered by the runtime-free tests and 67% by the provisioned tests; `workbench/pretrained.py` is 54% covered by the runtime-free tests, because its operations execute only against provisioned runtimes, in worker processes that the coverage tool did not instrument. A combined figure was not measured.

Hosted receipt files could not be downloaded from the development sandbox (the artifact storage host is unreachable). `hosted-isolated-artifacts.json` therefore holds artifact metadata and digests only. The key facts of each hosted receipt are kept as workflow annotations in `hosted-isolated-annotations-<job>.json`.

## Results on the synthetic examples

`external-automl` uses 288 rows: 190 fitting rows, a purge gap of 2, 48 validation rows and 48 holdout rows. The fit-mean reference test RMSE is 2.7643.

| Example | Local Linux | Hosted Linux | Hosted Windows, Python and frozen executable |
| --- | --- | --- | --- |
| TPOT | ExtraTreesRegressor, 16 evaluated, 5 failed, test RMSE 1.2647, digest `aa284a23e3e11f3e` | identical | same family, test RMSE 1.2692, digest `2a9c0b8bbb175f3a` |
| H2O | XGBoost, 5 evaluated, 1 failed, test RMSE 0.9448, digest `82754e8efccc2b98`, no refit | identical | GBM, test RMSE 0.9815, digest `f506f0dd4f9624fc` |
| AutoGluon | WeightedEnsemble_L2, 5 evaluated, test RMSE 0.6635, digest `aad8ecb880f8bdb3` | identical | identical |
| auto-sklearn | random forest, 4 evaluated, test RMSE 0.7101, digest `805a0e0d52357232` | identical | refused: unsupported platform |
| Chronos-Bolt | median MAE 0.7163 against last-value 5.8953; 39 of 48 inside the 10–90 band; digest `5f250161238efbfd` | same metrics; same digest in this run, `17dbcde0aec535fb` in run 43964480054 | same metrics, digest `aa35a047dd835fc4` |
| GhostNet | Egyptian cat 0.491, espresso 0.915 | identical | identical |

The Windows H2O leader differs because the H2O 3.46.0.12 jar carries XGBoost native libraries for Linux and macOS only. Python and the frozen executable agree on Windows for every example.

The execution tests check more than these numbers: that changing holdout targets and purged rows cannot change a prediction while changing fitting targets does, that altered or extended model assets are refused, that a budget expiry kills the whole engine tree including the H2O JVM, and that killing the application mid-run leaves no engine process.

## Defects found and corrected during the stage

1. **Windows engine hang.** The first hosted run ([43939472689](https://github.com/hajianamir/EnergyBaseline/actions/runs/43939472689), source `6e8a709`) failed nine Windows tests. The worker's parent-death watch blocked in a read on the standard-input pipe, and on Windows a blocked pipe read stalls every child that inherits the handle. The watch now waits on the parent process handle on Windows. Evidence: `first-windows-failure-annotations-133552805992.json`.
2. **Windows job-object race.** The kill-on-close job was assigned after process creation, so a virtual-environment launcher could start the real interpreter outside the job. The worker is now created suspended, assigned to the job and then resumed.
3. **auto-sklearn 0.15.0 start-up stall.** auto-sklearn waits for its log server's port in a loop that sleeps while holding the lock the server needs. On some machines the server never obtains it and the search waits until the budget. Job 133604927275 recorded three stalled searches with thread stacks in that loop (`autosklearn-stall-annotations-133604927275.json`). The first Linux failure, job 133552806399, failed the same way before stack recording existed, so its cause is inferred. The worker now publishes the port without that lock, in-process; the installed distribution is not modified. Before the workaround 1 of 22 sampled machines stalled; with it, 14 of 14 passed three rounds each (`autosklearn-stall-sampling.json`). A provisioned test holds the lock for the whole wait and requires the port to be published anyway.
4. **Process listing in a test.** `ps -eo args` truncated command lines on the hosted runner, so the orphan check now reads `/proc/*/cmdline`.
5. **Race in a test helper.** Hosted CI [43954380205](https://github.com/hajianamir/EnergyBaseline/actions/runs/43954380205) failed on Linux in `test_death_of_the_application_stops_the_engine_tree` with `FileNotFoundError` on `/proc/<pid>/stat`. The helper that decides whether a process is alive probed it with a signal and then read its `/proc` entry; a process reaped in between raised instead of reading as not alive. The helper now treats that case as not alive. Seven other CI runs of the stage passed on Linux with the old helper, so the race was rare. Evidence: `liveness-helper-race-annotations-133604926589.json`. The product code was not involved.
6. **Documentation corrections.** Disk sizes, the list of source-only distributions (seven, all pure Python in the inspected runtimes, so no compiler is needed) and the wording of the stall evidence were corrected after checking them against the installed runtimes and the package index.

`research.json` records asset origins, hashes, licences, interpreter availability and ten engine findings that shaped the implementation.

## Limits stated plainly

- Regression search only. No classification, time-series AutoML modes, GPU or distributed search, H2O stacked ensembles or deep learning, AutoGluon bagging or stacking, or auto-sklearn meta-learning.
- auto-sklearn has no Windows release and needs CPython 3.9. Its workaround relies on the `fork` start method that auto-sklearn uses with one job; the worker fails the run if that ever changes.
- H2O AutoML has no refit, so its leader stays trained on the fitting rows. The validation scores reported by H2O and AutoGluon are optimistic, because those rows also drive early stopping or ensemble weights; the result says so and the holdout metrics are computed in the application.
- One foundation model (Chronos-Bolt tiny): univariate, horizon at most 64, three uncalibrated quantiles, no covariates and no fine-tuning.
- One pretrained classifier (GhostNet, ImageNet-1k). No pretrained detection, segmentation or embeddings. Row C023 (learned image and language embeddings) is unchanged.
- The process boundary isolates dependencies, bounds time and stops process trees. It is not an operating-system sandbox.
- No selected model is persisted, exported or promoted to a baseline.
- Engine provisioning needs package-index access once per engine. Cross-platform results are not bit-identical, as the table shows.

## Implementation notes a reviewer may want to check

- The H2O worker chooses a free loopback port and then starts the JVM on it. Another process could take the port in between; the worker then fails its authenticated health check and retries, up to three times. This path was not exercised by a test.
- Provisioning passes the operator's `PATH` to the installer; engine runs never inherit it.
- Resuming the suspended Windows worker uses `ntdll.NtResumeProcess` on the process handle held by `subprocess.Popen`, which is a private attribute of that class.
