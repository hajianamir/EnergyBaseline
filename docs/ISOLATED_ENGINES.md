# Isolated AutoML engines, foundation forecasting and pretrained vision: Stage 7

## Status and honest scope

Three executable workbench operations call real third-party engines and real pretrained weights:

| Operation | Engine | Where it runs |
| --- | --- | --- |
| `external-automl` | TPOT 1.1.0, H2O AutoML 3.46.0.12, AutoGluon Tabular 1.6.3, auto-sklearn 0.15.0 | separately provisioned runtime, own process |
| `foundation-forecast` | Chronos-Bolt tiny through `chronos-forecasting` 2.3.2 | separately provisioned runtime, own process |
| `pretrained-image-classification` | GhostNet 1.0x ImageNet-1k weights on the application's PyTorch | application worker process |

The operation registry now holds 42 operations, 12 of them specification-only. API, command line and the native spawn-worker panel share one contract. Verification status and receipts are in `artifacts/capabilities/isolated-stage7/README.md`; a passing development run is not acceptance.

This is a complete integration of the named engines for **bounded regression search**, **zero-shot univariate forecasting** and **closed-vocabulary image classification**. It is not each engine's full task catalog, not accepted-model promotion, and not completion of the whole capability catalog. The limits are listed at the end.

## Why the engines are isolated

Each engine pins dependencies that cannot coexist with the application's hash lock (for example scikit-learn 0.24 for auto-sklearn and pandas below 2.4 for AutoGluon, against scikit-learn 1.9.1 and pandas 3.1.1). The application's pins are unchanged and no `--no-deps` or override installation is used. Instead every engine has:

- its own exact-pinned, hash-locked requirement set in `energybaseline/isolated/locks/<engine>.lock` (the `.in` file beside it is the resolver input);
- its own virtual environment under `<application home>/engines/<engine>-<first 16 hex digits of the lock SHA-256>`;
- its own operating-system process, started for one request and terminated afterwards.

The application never imports TPOT, H2O, AutoGluon, auto-sklearn or Chronos. A test asserts that none of them is installed in or imported by the application environment.

| Engine | Distribution | Runtime Python | Platforms | Licence (installed metadata) |
| --- | --- | --- | --- | --- |
| `tpot` | `tpot==1.1.0` | 3.12 | Linux, Windows | LGPL-3.0 |
| `h2o` | `h2o==3.46.0.12`, `jdk4py==17.0.9.2` | 3.12 | Linux, Windows | Apache-2.0 (H2O); GPL-2.0 with Classpath Exception (bundled OpenJDK 17) |
| `autogluon` | `autogluon.tabular[lightgbm,xgboost,catboost]==1.6.3` | 3.12 | Linux, Windows | Apache-2.0 |
| `autosklearn` | `auto-sklearn==0.15.0` | 3.9 | Linux only | BSD-3-Clause |
| `chronos` | `chronos-forecasting==2.3.2`, `torch==2.15.0` | 3.12 | Linux, Windows | Apache-2.0 |

auto-sklearn 0.15.0 is the final upstream release. It supports POSIX only and needs CPython 3.9 with scikit-learn 0.24.2; there is no Windows release to integrate. On Windows the engine reports `unsupported-platform` and a request for it is refused with exit status 2. This is an upstream fact, not a silent omission.

The engines are installed by the operator from the package index into the application home. They are not redistributed inside the application bundle.

## Provisioning

Provisioning is an explicit operator action that needs package-index access. Nothing is downloaded while an analysis runs.

```sh
python -m energybaseline.cli engines status
python -m energybaseline.cli engines install tpot
python -m energybaseline.cli engines install h2o
python -m energybaseline.cli engines install autogluon
python -m energybaseline.cli engines install chronos
python -m energybaseline.cli engines install autosklearn --python /path/to/python3.9
python -m energybaseline.cli engines remove tpot

python -m energybaseline.cli assets status
python -m energybaseline.cli assets install chronos-bolt-tiny
python -m energybaseline.cli assets install ghostnet-imagenet
python -m energybaseline.cli assets install ghostnet-imagenet --source /media/offline/ghostnet-imagenet
```

`engines install` creates the virtual environment from a base CPython of the version the lock was resolved for (`--python`; the running interpreter is used when it matches and the application is not frozen, so the frozen executable always needs `--python`). It then runs `pip install --require-hashes -r <lock>` with no cache, and records `energybaseline-engine.json` only after the installed closure equals the lock. A failed installation removes the partial runtime. During provisioning only, the installer process sees the operator's `PATH` so that build tools can be found; engine runs never inherit it. Seven locked distributions are published only as source archives: auto-sklearn, liac-arff, pynisher and smac; TPOT's func-timeout and stopit; and H2O itself. pip verifies each archive against its hash and then runs the project's own build code. In the inspected Linux runtimes all seven installed as pure Python, so no compiler was used.

Engine states are `installed`, `stale` (the manifest was written for a different lock), `not-installed` and `unsupported-platform`.

`assets install` either fetches the fixed HTTPS origins or copies the files from a local directory (`--source`) for machines without network access. Both paths stage the files privately and accept them only when byte count and SHA-256 match; a mismatch installs nothing. Asset states are `installed`, `invalid` and `not-installed`.

Disk use measured on Linux: TPOT 865 MB, H2O 413 MB, AutoGluon 1.1 GB, auto-sklearn 474 MB, Chronos 5.7 GB. The Chronos lock pins PyTorch 2.15.0 with 19 GPU-related distributions (15 NVIDIA libraries, three CUDA helper packages and Triton) that are marked Linux-only, so the Windows runtime installs none of them; its size was not measured.

## Execution boundary

For every request `run_engine`:

1. refuses an absent, stale or unsupported runtime with fixed operator guidance (`ProvisioningRequired`, exit status 2);
2. asks the runtime interpreter for its installed distributions and requires them to equal the lock for this platform, with nothing unlocked, missing or changed (only the virtual environment's own `pip`, `setuptools` and `wheel` may be unpinned);
3. writes float64 arrays as pickle-free `.npy` files and the request as JSON into a private directory under `<temporary directory>/EnergyBaselineEngines`;
4. starts the packaged worker with an environment built from nothing: private home and temporary directories, no inherited proxy, credential, token or `PYTHONPATH` variables, offline flags for the Hugging Face libraries, one thread;
5. waits up to the wall-clock budget (180 s for AutoML, 120 s for forecasting; 600 s is the hard maximum), then terminates the whole process tree (process group on POSIX, job object on Windows with a `taskkill /T` fallback);
6. requires a result envelope whose engine name, engine version and distribution list equal the registry and the lock, and whose loaded modules all come from the runtime, its base Python or the packaged workers;
7. removes the private directory before returning.

Inside the worker an interpreter audit hook refuses name resolution and any connection, datagram or listening socket that is not loopback; a wildcard bind is refused, which keeps engine dashboards off other interfaces. The engine must not outlive the application. On POSIX the worker watches a pipe held by the application: when the application is killed the worker terminates its own process group, and a detached helper removes the private directory. On Windows the worker is created suspended, placed in a kill-on-close job object and only then resumed, so the kernel terminates the whole tree when the application dies; as a fallback the worker also waits on the application's process handle. A pipe is not used on Windows because a blocked read on a Windows pipe stalls every child process that inherits it. There the private directory is removed by the sweep that runs before every engine call (directories older than the maximum budget plus five minutes cannot belong to a live call).

**What this boundary is not.** It isolates dependencies, bounds time and stops processes. It is not an operating-system sandbox, container or kernel firewall against hostile third-party code. The audit hook covers Python code in the worker; it does not cover the H2O JVM, which is constrained by its own loopback bind and authentication (below). Hash locks bind what is installed; they are not a privilege boundary around the installed directory.

Third-party logs are never exported, because they can echo data values. For support work only, an operator may set `EB_ENGINE_DIAGNOSTICS` to an existing directory; the log of an engine run that failed or exceeded its budget is then copied there. Five seconds before a budget of at least twelve seconds ends, the worker writes its thread stacks and, on Linux, the state of the processes in its session to that log, so a stalled engine shows where it waits. These lines hold file names, line numbers and command lines, not data values.

### H2O and the web-free policy

H2O is a Java server with an HTTP interface; its Python package is only a client. The application remains web-free: it starts no server of its own and embeds no browser. The H2O worker starts one private JVM from the locked `jdk4py` Java 17 runtime, bound to `127.0.0.1` on an ephemeral port, with `-hash_login` and a random one-time user and password that exist only in the private directory. The worker picks a free loopback port and then starts the JVM on it; if another process takes the port first, the JVM cannot answer with the one-time credentials and the worker retries, up to three times. Before use the worker confirms that an unauthenticated request receives HTTP 401. The JVM is shut down after the request and is inside the process tree that the budget and the parent-death watch terminate. Tests kill the application mid-run and after a budget expiry and require that no JVM survives.

### TPOT and Dask

TPOT schedules work on Dask. The worker supplies its own in-process `LocalCluster` (no worker processes, no TCP transport), removes the scheduler and worker HTTP routes, and binds the remaining listener to loopback. TPOT's default cluster would bind a dashboard on every interface; the audit hook refuses that.

## `external-automl`

Required parameters: `engine` (`tpot`, `h2o`, `autogluon`, `autosklearn`), `time`, `target`, `features` (1..32, disjoint from target and time), `train_rows` (at least 48), `validation_rows` (at least 12, leaving at least 24 fitting rows), `seed`. Optional: `gap` (0..100), `models` (2..6, default 4). At most 4,000 rows. Time must be unique and strictly increasing; values must be finite numbers.

With `b = train_rows - validation_rows`, the engine process receives fitting rows `[0, b - gap)`, validation rows `[b, train_rows)` and the **features** of the outer holdout `[train_rows, n)`. Purged gap rows and every holdout target stay in the application process, which computes all holdout metrics from the returned predictions. Predictions are reduced to metrics and a digest; neither rows nor prediction vectors are exported.

| Engine | Search | Budget from `models = m` | Final fit |
| --- | --- | --- | --- |
| TPOT | genetic programming over linear pipelines, scored on the validation rows | 2 generations, population `2m` | selected pipeline refit on fitting plus validation rows |
| H2O | AutoML leaderboard on the validation frame; deep learning excluded (not reproducible) and stacked ensembles excluded (they need cross-validation) | `max_models = m` | **none**: H2O AutoML offers no refit, so the leader stays trained on the fitting rows |
| AutoGluon | portfolio of the first `m` of LR, GBM, XGB, CAT, RF, XT plus a validation-weighted ensemble; no bagging or stacking | `m` families | selection refit on fitting plus validation rows (`refit_full`) |
| auto-sklearn | SMAC Bayesian optimisation without meta-learning warm start, holdout on the validation rows, ensemble of size one | `m` configurations within a 45 s task limit | selected configuration refit on fitting plus validation rows |

The result states `search_fit_rows`, `purge_gap`, `validation_rows`, `final_fit_rows` and a `fit_scope` sentence, so the H2O difference is visible in every result. `candidates_evaluated` and `candidates_failed` are read from each engine's own records (TPOT evaluation errors, the H2O event log against its leaderboard, AutoGluon's leaderboard against the requested families, auto-sklearn run statuses). `engine_reported_validation_rmse` carries a note where it is optimistic: H2O also uses the validation frame for early stopping, and AutoGluon fits ensemble weights on it. `fit_mean_reference_test_rmse` is the holdout error of predicting the fitting-row mean.

auto-sklearn 0.15.0 has a start-up defect that the worker works around. It waits for its log-server process to publish a port in a loop that sleeps while holding the shared lock the server needs, so on some machines the server never obtains the lock and the search waits until the wall-clock budget. This was observed on hosted Linux machines. One sampling job recorded three stalled searches with the main thread in that loop and the server process waiting for the lock; an earlier job failed the same way before stack recording existed. Before the workaround 1 of 22 sampled machines stalled; with it, 14 of 14 passed (three rounds each), and so did both operating systems in the engine workflow. The sampled machines and stacks are in the stage evidence. The worker replaces only the server's publishing step so that it stores the port without that lock; auto-sklearn's code is otherwise unchanged and the installed distribution is not modified. An execution test holds the lock for the whole wait and requires that upstream's server stays unpublished while the replacement publishes.

When no auto-sklearn configuration beats its built-in mean predictor on the validation rows, auto-sklearn selects that predictor; the result then reports `selected_model: dummy_mean` instead of failing.

auto-sklearn's limit is partly wall-clock, so a heavily loaded machine can complete fewer configurations than requested; `candidates_evaluated` reports what actually ran.

```sh
python -m energybaseline.cli workbench examples/isolated/automl-autogluon.json --dataset examples/isolated/series.csv --out automl.json
```

## `foundation-forecast`

Required parameters: `time`, `target`, `train_rows` (at least 32). Optional `horizon` (1..64, at most the rows after `train_rows`; default the smaller of 64 and those rows). At most 20,000 rows.

The Chronos runtime receives only the last up to 2,048 target values before `train_rows` and returns the 0.1, 0.5 and 0.9 quantiles for the horizon from the fixed pretrained Chronos-Bolt tiny weights (8,652,672 parameters). There is no fine-tuning and no download: the weights directory is verified before the call and again after it. Holdout values never enter the model process.

The result reports median MAE and RMSE, the last-value (persistence) MAE and their ratio, mean pinball loss, the number of holdout observations inside the 0.1–0.9 band and the number of quantile crossings. **The quantiles are uncalibrated model outputs.** No coverage guarantee and no distribution-free claim is made, and nothing here is a calibrated forecast interval.

```sh
python -m energybaseline.cli workbench examples/isolated/chronos-bolt.json --dataset examples/isolated/series.csv --out forecast.json
```

## `pretrained-image-classification`

Specification-only. Required: `image_root` and 1..4 relative `image_files`. Optional `top_k` (1..5, default 3). Image loading reuses the bounded loader of `pretrained-ocr`: PNG/JPEG/BMP under the declared root, at most 5 MB per file, 2 million pixels per image and 4 million in total, single frame, EXIF orientation applied.

The model is GhostNet 1.0x (Han et al., CVPR 2020) with the authors' published ImageNet-1k weights (5,182,508 parameters, published top-1 accuracy 73.98%). Preprocessing follows the authors' validation script: shorter side to 256 with bilinear resampling, centre crop 224, ImageNet mean and standard deviation. The weights file is loaded with `weights_only=True`, which refuses pickled code, into the vendored model definition with strict key matching. Weights and class list are verified by size and SHA-256 before construction and again after inference.

The output lists, per image, the source and decoded-pixel hashes, dimensions and the top classes with softmax scores. No pixels are exported. Scores are not calibrated probabilities, and an object outside the 1,000 ImageNet classes still receives one of those labels.

The lite build profile excludes PyTorch by design, so this operation needs the full profile.

```sh
python -m energybaseline.cli workbench examples/isolated/ghostnet.json --out classes.json
```

## Reproducibility

Repeating a request on the same machine with the same runtime returns the same predictions; tests and receipts compare the prediction digest of two executions. Results are not promised to be identical across operating systems or processors:

| Example | Local Linux and hosted Linux | Hosted Windows (Python and frozen executable agree) |
| --- | --- | --- |
| TPOT | identical digest; ExtraTreesRegressor, test RMSE 1.2647 | same pipeline family, test RMSE 1.2692 |
| H2O | identical digest; XGBoost leader, test RMSE 0.9448 | GBM leader, test RMSE 0.9815: the H2O 3.46.0.12 jar carries XGBoost native libraries for Linux and macOS only |
| AutoGluon | identical digest; test RMSE 0.6635 | identical digest to Linux |
| auto-sklearn | identical digest; random forest, test RMSE 0.7101 | refused, unsupported platform |
| Chronos-Bolt | same metrics to four decimals; the forecast digest equalled the local one in one hosted run and differed in another | same metrics to four decimals, digest differs |
| GhostNet | Egyptian cat 0.491, espresso 0.915 | same |

The fit-mean reference test RMSE for the AutoML example is 2.7643, and the last-value reference MAE for the forecasting example is 5.8953 against a median-forecast MAE of 0.7163.

## Verification layers

- `tests/test_isolated_engines.py` and `tests/gui/test_isolated_gui.py` run in the mandatory suite on both operating systems and need no provisioned runtime. They use real files, interpreters and child processes: lock structure for both platforms, fail-closed refusal of an absent, stale or lock-divergent runtime, the audit hook, the result envelope, budget expiry and parent death with real process trees, the rebuilt environment, workspace sweeping, asset hash refusal, the vendored model's provenance hashes, GhostNet's published parameter count and the preprocessing geometry.
- `provisioned_tests/` executes every engine supported on the platform against provisioned runtimes and assets named by `EB_PROVISIONED_HOME`. The session fails at start when that home is missing; no test is skipped. It checks search/refit contracts, semantic accuracy against a reference, identical results on repetition, that changing holdout targets and purged rows cannot change a prediction while changing fitting targets does, asset tampering, budget expiry with a live JVM, killing the application mid-run, the command line and the native worker panel.
- `scripts/evidence/isolated_engines.py` runs every example twice through the real command line, or through the frozen executable with `--executable`, and writes a receipt.
- The workflow `Isolated engines and pretrained models` provisions everything from the package index and the fixed asset origins on Ubuntu 24.04 and Windows, runs the execution tests and receipts, and on Windows repeats the receipts through the full windowed executable. Ubuntu 24.04 is used because the hosted CPython 3.9.25 build exists for Ubuntu 22.04 and 24.04 only. Each receipt's key facts are also emitted as a workflow annotation.
- `scripts/evidence/isolated_diagnose.py` is a support script, not a test. After a workflow failure it runs each provisioned AutoML engine once on the synthetic example with `EB_ENGINE_DIAGNOSTICS` set and prints the exception and the retained log. It always exits 0.

## Limits that remain

- Regression search only. Classification, multi-output, time-series-specific AutoML modes, GPU training, distributed execution, H2O stacked ensembles and deep learning, AutoGluon bagging/stacking and neural or tabular-foundation models, and auto-sklearn meta-learning are not exposed.
- No selected model is persisted, exported, promoted to a baseline or connected to the baseline lock.
- auto-sklearn has no Windows release; H2O has no refit.
- Chronos-Bolt tiny only, univariate, horizon at most 64, three fixed quantiles, no covariates, no fine-tuning, no panel input. Other foundation models (Chronos-2, TimesFM, Moirai, TinyTimeMixer) are not integrated.
- GhostNet ImageNet-1k classification only. Pretrained detection, segmentation, image embeddings and face models are not integrated.
- Engine provisioning needs package-index access once per engine; there is no offline engine installer. Assets can be provisioned offline from a local directory.
- The boundary is not an operating-system sandbox, as stated above.
