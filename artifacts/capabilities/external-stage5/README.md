# Stage 5 external engines and pretrained models: execution record

## Scope and final status

**Verified bounded Stage 5 scope** on source `cc6be9991d88b999325f921bd9e6b4a799fd6178`. Three new operations execute FLAML CFO regression search, Pandera schema validation, and pretrained RapidOCR detection/orientation/recognition with three real ONNX models bundled in a pinned wheel. There are 35 total workbench operations, including 11 specification-only operations. API, CLI and native spawn-worker execution share schemas. See `docs/EXTERNAL_ENGINES.md` and `docs/THIRD_PARTY_MODELS.md`.

Actual [Windows/Linux workflow 43779750271](https://github.com/hajianamir/EnergyBaseline/actions/runs/43779750271) completed successfully, including expanded full-suite coverage, complete native GUI coverage, independent external-engine CLI executions on both OSes, and **all three actual operations through the windowed Windows executable**. Separate [console-bundle diagnostic workflow 43779750245](https://github.com/hajianamir/EnergyBaseline/actions/runs/43779750245) also succeeded on the same source. Console success is not substituted for windowed acceptance.

This is **not all-engine/catalog completion**. Auto-sklearn/AutoGluon compatibility conflicts, TPOT/H2O lifecycle work, foundation/YOLO weights, full research-model parity and other catalog families remain explicitly open. `program_complete=false` remains mandatory.

## Final-source execution evidence

| Execution | Actual result | Evidence |
| --- | --- | --- |
| Combined focused/native workbench integration | 244 passed, no skips; 117.07 seconds | `focused.log`, `focused.xml` |
| Workbench coverage | 88.95%, unchanged 80% local gate | `capability-coverage.xml` |
| Complete GUI directory | 44 passed, no skips; 155.54 seconds | `gui.log`, `gui.xml` |
| GUI coverage | 72.10%, unchanged 56% gate | `gui-coverage.xml` |
| Complete local regression, uninstrumented | 877 passed, no skips; 768.18 seconds | `full-direct.log`, `full-direct.xml` |
| Full interpreter teardown | Actual exit 0; wrapper elapsed 772.80 seconds | `full-direct-exit.json` |
| Independent CLI subprocesses | All three exit 0; stdout equals output files | `cli-examples.json` |
| Actual operations with connections and DNS denied | All three external/pretrained engines executed | `offline-oracles.json` |
| Static/runtime/dependency/architecture guards | All recorded commands exit 0 | `static-checks.log`, `static-exit.json` |
| Dependency vulnerability audit | Exit 0; no findings in 171 audited dependency records, none skipped | `dependency-audit.json`, `dependency-audit.log` |
| Tested-source binding | 422 tracked non-documentation/evidence files; zero Git-clean-blob drift | `source-binding.json` |
| Actual hosted Ubuntu job 133014016815 | Success, including real engine executions and full/GUI coverage | `hosted-run.json`, `hosted-jobs.json` |
| Actual hosted Windows job 133014017014 | Success, including real windowed executable engine executions | `hosted-run.json`, `hosted-jobs.json` |
| Actual hosted console diagnostic job 133014016446 | Success on the same source; not a replacement for windowed verification | `console-run.json`, `console-jobs.json` |

`environment.json` records the actual Python 3.12/native Qt/unixODBC and dependency environment. No local test was skipped. Local full regression was uninstrumented; focused coverage is not represented as expanded full-suite coverage. The unchanged full-suite 60/80% coverage gates passed in the actual Windows/Linux jobs. Platform-inapplicable hosted steps retain their existing conditions: the frozen Windows step is intentionally inapplicable to Linux, not an omitted local test. Windows installer/DPI evidence remains CI/non-normative under D2, not physical-desktop human sign-off.

`hosted-artifacts.json` and `console-artifacts.json` contain actual GitHub artifact metadata, not downloaded artifact contents. Raw hosted Windows log retrieval failed through the current integration (`hosted-log-access.json`); no raw hosted-log archive is claimed. Completed API job/step receipts and raw local logs/JUnit are retained. Hosted timestamps are preserved verbatim as reported by GitHub. This follow-up documentation/evidence update changes no tested product source, tests, dependencies or workflows.

## Genuine failures, diagnosis and fixes

1. **Registry contract:** `historical-precontract-focused.xml` and its log record a failed development run that still expected 32/10 operations after expansion to 35/11. The assertion was updated and strengthened with exact membership and tabular/spec-only contract checks. This failed run is excluded from acceptance.
2. **Windowed Python streams:** source `7429afd` passed Python/full/GUI executions but failed the actual frozen Windows step in workflow `43765206366`. A real FLAML CLI execution reproduced `AttributeError` from unconditional `sys.stdout.write` after successful output-file generation. Windowed PyInstaller processes lack Python stdout/stderr. The CLI now honors explicit output files, fails closed without a destination, and avoids a secondary absent-stderr error. Four fresh-process cases execute the actual engines/error contract without standard streams. Receipts/traceback: `historical-7429-*`, `historical-windowless-reproduction.*`.
3. **Native versus Python logging:** workflow `43771703951` exposed an incorrect new test assumption on Linux: ONNX Runtime's C++ logger can write a PCI-enumeration warning to OS descriptor 2 even when Python `sys.stderr` is None. The test now asserts absent Python streams directly and permits only that exact, bounded, known hardware diagnostic. Unknown logs, Python tracebacks, appended text and excessive output are independently tested as failures. Real output/spec identity, engine execution, model metrics and privacy assertions were strengthened, not replaced by availability checks. Actual failed API annotations remain in `historical-9ad-linux-annotations.json`; prior-source failure receipts are retained under `historical-9ad-*`.
4. **Actual native library closure:** console workflow `43778675169` on source `6a3dc5f` exposed `XGBoostLibraryNotFound`: FLAML imports its estimator module even for forest-only search, and the XGBoost DLL was absent from the frozen bundle. Native XGBoost/LightGBM/ONNX binaries and VERSION resources are now explicitly collected at package-relative destinations. No backend was disabled and the 120-second execution budget was not increased. Exact failed traceback/job/run receipts: `diagnostic-annotations-6a3.json`, `diagnostic-jobs-6a3.json`, `diagnostic-run-6a3.json`. `native-collection-oracles.json` independently records actual local library/resource discovery; it is not claimed as frozen Windows evidence. Both final-source workflows above passed after this fix.

These historical failures are retained for traceability, not counted as acceptance or hidden by a skipped/removed mandatory step. Final-source results supersede them.

## Provenance, privacy and remaining limitations

`research.json` records actual PyPI release metadata/artifact hashes, installed source reads and upstream requirements/model-license reads. It separately identifies failed YOLO model retrieval; no YOLO inference is claimed. The pretrained OCR oracle checks actual semantic recognition in memory and separately verifies that the public result exports no recognized plaintext. Models are neither mocks nor synthetic trained fixtures and are not added to Git. Attribution and Apache-2.0 notices accompany the native build.

A supplementary local Linux bundle probe could not build because the sandbox Python lacks shared libpython. `supplementary-linux-bundle.json` and its raw log retain this non-acceptance limitation. No frozen Linux executable result is claimed; the mandatory frozen native target is Windows. Generated icon changes from that probe were restored before source binding.

No prior dependency pin, Python version, mandatory baseline test, coverage threshold, accepted-baseline lifecycle, ReportModel renderer contract or inference authorization gate was weakened. New dependencies use exact pins and PyPI release hashes. All per-row remaining scope and untouched catalog families remain incomplete; successful bounded engine integration is not full-paper or full-program completion.
