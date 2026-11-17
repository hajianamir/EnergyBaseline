# Stage 6: verified NLP, predictive survival and graph learning

## Scope and final status

Four new executable operations address additional NLP, survival and graph-learning scope: TF-IDF text classification, NMF topic modeling, Cox/Breslow or Weibull AFT regression, and CPU GCN node classification. The registry contains **39 operations**, including 11 specification-only operations. Five actual synthetic jobs live in `examples/catalog/manifest.json`. See `docs/CATALOG_EXPANSION.md` for schemas, algorithms, limits and remaining work. No new dependency or pretrained NLP/GNN weight was added.

**Bounded Stage 6 scope is verified.** Actual [Windows/Linux workflow 43834852648](https://github.com/hajianamir/EnergyBaseline/actions/runs/43834852648) succeeded on `742ab337f30681054f2ec102fe16e08c3d9b3df8`, including all five jobs through the **full windowed Windows executable**, expanded full-suite coverage, complete GUI coverage and existing lite installer/runtime/external-engine gates. Separate [full-console diagnostic workflow 43834852664](https://github.com/hajianamir/EnergyBaseline/actions/runs/43834852664) also passed on that source. Console success is not substituted for windowed acceptance.

Local complete/focused/GUI runs executed source `76159f41e93fd7cec299b1a6f65cbc75a3dba4ee`. All **436** product/test/fixture/dependency/script Git blobs are identical in hosted source `742ab33`; only two CI workflows changed to add the correct full-profile packaging paths (`local-hosted-equivalence.json`). Local runs are not misrepresented as reruns of the workflow-only commit. Later direct absent-stream CLI oracles executed `742ab33` itself. This documentation/evidence follow-up changes no tested product, test, dependency, fixture or workflow.

**This is not whole-catalog completion or a redefinition of the full request.** All per-row remaining scope and untouched families remain open, and `program_complete=false` remains mandatory.

## Actual execution evidence

| Execution | Actual result | Evidence |
| --- | --- | --- |
| Focused workbench/external/new integration | 322 passed, no skips; 116.81 seconds | `focused.log`, `focused.xml` |
| Expanded workbench coverage | 89.25%, unchanged 80% local gate | `capability-coverage.xml` |
| Complete local GUI directory | 49 passed, no skips; 184.08 seconds | `gui.log`, `gui.xml` |
| GUI coverage | 72.10%, unchanged 56% gate | `gui-coverage.xml` |
| Complete local regression, uninstrumented | 975 passed, no skips; 825.24 seconds | `full-direct.log`, `full-direct.xml` |
| Full interpreter teardown | Actual exit 0; wrapper elapsed 829.50 seconds | `full-direct-exit.json` |
| Independent actual CLI processes | Five successful semantic oracles; stdout equals output files when emitted | `cli-examples.json` |
| Actual operations with DNS/connections denied | All five jobs executed; no mocked models | `offline-oracles.json` |
| Absent Python stdout/stderr | Five successful real fresh-process CLI jobs; authoritative output files | `windowless-python-oracles.json` |
| Runtime, dependencies, lint, architecture/integrity/static checks | Every recorded command exit 0 | `static-checks.log`, `static-exit.json` |
| Local tested-source binding | 438 tracked non-documentation/evidence files on `76159f4`; zero Git-clean-blob drift at local execution | `source-binding.json` |
| Local/hosted product equality | 436 identical product/test/fixture/dependency/script blobs; only two CI workflows changed | `local-hosted-equivalence.json` |
| Actual Ubuntu job 133188827635 | Success, including five semantic jobs and unchanged full/GUI coverage gates | `hosted-run.json`, `hosted-jobs.json` |
| Actual Windows job 133188827173 | Success, including existing lite gates and all five jobs through full windowed executable | `hosted-run.json`, `hosted-jobs.json` |
| Actual Windows full-console job 133188827457 | All five frozen semantic jobs and earlier external-engine/OCR jobs succeeded | `console-run.json`, `console-jobs.json` |

Local full regression was uninstrumented; focused coverage is not represented as expanded hosted full-suite coverage. The unchanged expanded full-suite 60/80% gates and native GUI gates passed on both hosted OSes. `focused-gui-exit.json` records commands, source and actual teardown exits. `environment.json` records the real Python 3.12/native Qt/unixODBC environment. No local test was skipped. Windows-only hosted steps are intentionally inapplicable to Linux, not omitted local tests. No frozen Linux result or physical-desktop human sign-off is claimed; existing installer/DPI evidence remains CI/non-normative under D2.

`hosted-artifacts.json` and `console-artifacts.json` contain actual GitHub artifact metadata, not downloaded artifact contents. Raw hosted Windows log retrieval failed through the integration (`hosted-log-access.json`); no raw hosted-log archive is claimed. Completed API job/step receipts and raw local logs/JUnit remain available. Hosted timestamps are preserved verbatim as reported by GitHub.

## Algorithms and actual oracles

- **NLP:** actual TF-IDF binary/multiclass logistic classification and NMF topic modeling fit vocabulary, IDF and model only on the chronological training prefix, excluding purge/holdout. Real altered-holdout/purge oracles verify unchanged fitted hashes. No documents, vocabulary words or class labels are exported; checksums are identity metadata, not anonymization.
- **Survival:** actual Cox/Breslow maximizes PHReg loglike/score without calling inferential fitting/results APIs. Weibull AFT uses the conditional observed/censored likelihood with left truncation and an analytic gradient checked against independent SciPy density/survival and finite differences. Risk-set ties, delayed entry, monotone conditional predictions and concordance have independent numeric oracles. No significance tests, covariance matrices, intervals or baseline promotion are produced.
- **Graph learning:** a real two-layer CPU GCN learns through symmetric-normalized adjacency with self-loops. Tests verify loss reduction, classification, hand-calculated normalization, held-out-label isolation and restored Torch thread/RNG state. A topology-only oracle replaces homophilic edges with a complete graph, leaving data/seed/training budget fixed: identical normalized messages reduce actual test accuracy from 1.0 to 0.5 and change the prediction hash (`message-passing-oracle.json`). This demonstrates executed message passing, not a topology-independent classifier. The protocol is explicitly **static transductive**: full graph/features visible, test labels excluded from loss. No unseen-node/temporal/held-out-edge or paper benchmark parity claim is made.

`research.json` records actual upstream source-retrieval hashes and reviewed installed implementation versions/hashes/findings. Source availability and upstream GCN layers are not represented as executed pretrained models or full-paper reproduction.

## Genuine defects, failures and corrections

1. **Earlier binary coercion:** the shared helper accepted boolean zero/one equivalents without warning and complex zero/one values despite `ComplexWarning`. `binary-coercion-reproduction.json` loads the actual prior Git blob, reproduces both and verifies rejection before casting. Regressions preserve actual integer/float/nullable 0/1 inputs. This is stricter validation, not a weakened test or runtime downgrade.
2. **NMF reconstruction cancellation:** source `ea814cc` passed 965 complete local tests but its real Windows Python `text-topics` step failed in workflow `43827508268`; the unchanged follow-up source also failed that step. A real prior-blob corpus permutation reproduced NaN in the library diagnostic for a near-exact valid factorization, rejected correctly by the finite-JSON guard. The sparse expanded norm-squared identity suffered roundoff cancellation. Direct residual squares in bounded 32-row blocks now replace that unstable scalar and the earlier clipped holdout expression. Learned component hashes are unchanged (`nmf-roundoff-reproduction.json`). Ten additional degenerate-basis/block-boundary regressions passed; the standalone new/regression module passed 93 tests before the final complete runs. No true convergence warning was suppressed, tolerance relaxed, iteration budget increased or NaN allowed in output. Failed hosted receipts remain under `historical-ea814-*`, `historical-b30-*`; they are not acceptance.
3. **Correct native profile:** review found the mandatory lite profile deliberately excludes Torch, so it cannot execute GCN or earlier Torch models. The workflow-only update retains all existing lite installer/runtime/external-engine checks and adds a separate **full windowed** build for all five jobs. The full-console diagnostic is additional evidence, not a replacement. The `76159f4` workflow passed Linux but failed the new windowed semantic step against lite (`historical-761-run.json`, `historical-761-jobs.json`); its available annotation reports exit 1 without a detailed inner traceback, which is not fabricated here. Earlier direct-Windows/lite-console diagnostic receipts remain under `historical-761-console-*`; they are not represented as frozen GCN proof. Final actual full-profile workflows above passed.
4. **Development test fixture:** a new invalid-input case initially inserted an integer into pandas' strict string dtype before reaching the product validator (`historical-fixture-failure.xml.gz`: byte-exact compressed JUnit, one failed and 54 passed; original/archive hashes in `historical-fixture-failure.json`). Explicit object dtype now allows the actual invalid-input rejection to run. No assertion was removed; that failed development run is excluded from acceptance.

## Remaining scope and preserved contracts

All `remaining_scope` fields and pending families remain open. Examples include link prediction, NER/translation/summarization/LLM/RAG, additional external AutoML engines, pretrained foundation/semantic vision assets, full-paper parity and accepted model persistence. Safety prohibitions (unauthorized inference, arbitrary user-code execution, target leakage and automatic baseline mutation) are not missing features to bypass. See `docs/CAPABILITY_BACKLOG.json` for the broader request.

Mandatory baseline tests/LICENSE/review baseline, coverage thresholds, Python/dependency pins, VSG/TCP authorization, monitoring snapshot bindings and ReportModel/Platypus renderer contracts are unchanged. No engine is mocked, no dependency downgrade/override is introduced, and no prior mandatory gate or per-operation 120-second budget is removed or weakened.
