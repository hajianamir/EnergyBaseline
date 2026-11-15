# Advanced Stage 4 execution record

## Scope

Twelve added operations cover actual Optuna model-family search, real neural architecture selection, six scratch-trained reference forecasting networks, Conv2d image classification, U-Net segmentation/component detection, printed-template OCR, CVRP/job-shop MILP, constrained NSGA-II, stock-flow integration, graph agents and a frozen state-space twin candidate. See `docs/ADVANCED_WORKBENCH.md` for schemas, research-model differences, limits and remaining scope.

This is not a claim of integrating every named external AutoML package, full research-model feature parity, pretrained foundation/vision models, an unrestricted simulator, or the accepted main-baseline lifecycle. The full catalog remains incomplete.

## Verification status

Final-source focused, native, full-regression and actual Windows/Linux executions are pending. Draft source passes are not final-source acceptance evidence. Final counts, exact exit codes and actual hosted API receipts will be added only after completion.

A genuine constrained-feasibility oracle caught an NSGA-II closure-variable collision: a parent vector overwrote the inequality right-hand side. The constraint vector now has a dedicated name; parent fitness is cached, offspring are actually evaluated and every returned feasible front is checked against independently recomputed constraints/objectives. The test was not weakened.

`research.json` contains ten actual primary GitHub source reads with blob/content hashes. These inform patching/channel independence, sparse-query context updates/distillation, residual interpolation, gated fusion, dense temporal decoding, autoregressive likelihood training, search/survival selection and numerical integration. Research is not integration of the source packages or pretrained weights.

Raw user images, recognized plaintext, request literals, fitted weights and reference datasets are not automatically exported into reports/project files. Workbench results have bounded metrics/digests; template OCR deliberately returns template positions rather than recognized text. Existing gate, baseline, dependency, SQL, renderer, mandatory-test and CI thresholds are not weakened.
