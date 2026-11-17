# Advanced Stage 4 execution record

## Scope

Twelve added operations cover actual Optuna model-family search, real neural architecture selection, six scratch-trained reference forecasting networks, Conv2d image classification, U-Net segmentation/component detection, printed-template OCR, CVRP/job-shop MILP, constrained NSGA-II, stock-flow integration, graph agents and a frozen state-space twin candidate. See `docs/ADVANCED_WORKBENCH.md` for schemas, research-model differences, limits and remaining scope.

This is not a claim of integrating every named external AutoML package, full research-model feature parity, pretrained foundation/vision models, an unrestricted simulator, or the accepted main-baseline lifecycle. The full catalog remains incomplete.

## Verification status

Verified bounded Stage 4 scope on source `8475dd232b00c1af73d4bafe318d26edf19cbe49`. Actual [workflow 43757468182](https://github.com/hajianamir/EnergyBaseline/actions/runs/43757468182) completed successfully on Windows and Linux, including full-suite expanded coverage gates and native GUI coverage. This is not whole-program completion.

| Execution | Actual result | Evidence |
| --- | --- | --- |
| Focused workbench and native integration | 192 passed, no skips; 100.03 seconds | `focused.log`, `focused.xml` |
| Workbench package coverage | 88.57%, gate 80% | `capability-coverage.xml` |
| Complete GUI directory | 41 passed, no skips; 223.18 seconds | `gui.log`, `gui.xml` |
| GUI coverage | 72.10%, gate 56% | `gui-coverage.xml` |
| Complete local regression, uninstrumented | 825 passed, no skips; 926.98 seconds | `full-direct.log`, `full-direct.xml` |
| Full interpreter teardown | Actual subprocess exit 0; wrapper elapsed 932.30 seconds | `full-direct-exit.json` |
| Independent CLI subprocesses | All 19 exit 0; stdout JSON equals output file | `cli-examples.json`, `examples/` |
| Static/runtime/architecture/dependency guards | All recorded commands exit 0 | `static-checks.log`, `static-exit.json` |
| Actual RGB training and historical-covariate gradients | RGB classifier plus Informer/TFT/TiDE checks passed | `additional-oracles.json` |
| Tested-source binding | 418 tracked non-documentation/evidence files; zero drift | `source-binding.json` |
| Actual hosted Linux job 132944605470 | Success | `hosted-run.json`, `hosted-jobs.json` |
| Actual hosted Windows job 132944605866 | Success | `hosted-run.json`, `hosted-jobs.json`, `hosted-windows-job.json` |

`environment.json` records the genuine Python 3.12/native Qt/unixODBC environment. The local full-suite run was not coverage-instrumented; unchanged full-suite 60/80% coverage gates passed in the actual hosted jobs, not inferred from local focused coverage. Platform-inapplicable hosted steps are skipped by the existing workflow; no local test was skipped or weakened. Windows packaging/DPI evidence remains CI/non-normative, not physical-desktop human sign-off.

`hosted-artifacts.json` is the actual GitHub artifact metadata, not downloaded artifact contents. Raw hosted job-log retrieval was unavailable through the current integration (`hosted-log-access.json`); no raw hosted-log archive is claimed. Completed API receipts and raw local execution logs are retained. Hosted timestamps are retained verbatim as reported by GitHub.

`draft-constraint-regression.xml` is a historical failed development run, explicitly excluded from acceptance. The later final-source results above supersede it. This follow-up evidence/documentation update changes no tested product source, tests, dependency lock or workflow.

A genuine constrained-feasibility oracle caught an NSGA-II closure-variable collision: a parent vector overwrote the inequality right-hand side. The constraint vector now has a dedicated name; parent fitness is cached, offspring are actually evaluated and every returned feasible front is checked against independently recomputed constraints/objectives. The test was not weakened.

`research.json` contains ten actual primary GitHub source reads with blob/content hashes. These inform patching/channel independence, sparse-query context updates/distillation, residual interpolation, gated fusion, dense temporal decoding, autoregressive likelihood training, search/survival selection and numerical integration. Research is not integration of the source packages or pretrained weights.

Raw user images, recognized plaintext, request literals, fitted weights and reference datasets are not automatically exported into reports/project files. Workbench results have bounded metrics/digests; template OCR deliberately returns template positions rather than recognized text. Existing gate, baseline, dependency, SQL, renderer, mandatory-test and CI thresholds are not weakened.
