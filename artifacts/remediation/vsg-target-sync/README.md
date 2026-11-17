# VSG target synchronization: verification evidence

Tested implementation commit: `57c2fa179da3cbf6ca1d8d92d3bc0d696f93298c` on `arena/bcc82031-energybaseline`.

## Defect and scope

The target checklist and Column Metadata role editor updated UI roles but did not recreate the fixed VSG target tuple. The original VSG/TCP could remain associated with the dataset-load selection. The correction is confined to SetupPage synchronization; it does not weaken authorization, permit sibling/self-target leakage, automatically accept gates, or refit a locked baseline.

The VSG target combo is a **view of selected TARGET columns**. Select targets using the TARGET checkboxes or schema roles; the combo then switches which target's VSG decisions are displayed.

## Raw evidence

- `baseline-reproduction.log`, `.xml`, `.exit`: the new target-change test executed against the original `pages_setup.py` from `c5f32177dd699804381c3d8a19bf6b19bb9037ee`; exit **1**, expected failure because `state.targets` remains `['output_a']` after selecting `output_b`. The fixed file was restored with an EXIT trap before subsequent tests. This is defect reproduction, not an acceptance pass.
- `target-regression.log`, `.xml`: all six new target synchronization regressions on the fixed source; **6 passed**, exit **0**. Includes live checklist/schema signals, accepted authorization invalidation, empty-selection clearing/reselection, unchanged collect and override preservation, active-view preservation, schema JSON reload, and invalid-schema fail-closed recovery.
- `gui.log`, `.xml`: the entire Qt GUI test directory, **23 passed**, exit **0**, including the pre-existing GUI tests. Real offscreen Qt widgets, not mocked VSG controls.
- `full-suite.log`, `.xml`: **504 passed in 675.19 seconds**, no failures/errors/skips, process exit **0** including interpreter shutdown.
- `tracked-static-checks.log`: successful lint, formatting, Pyflakes, original-test integrity, architecture/environment/thread guards and requirement matrix checks against a `git archive` of the tested commit. The snapshot excludes downloaded native source/build trees, not any tracked product code.
- `static-checks.log`: an earlier partial static-check attempt stopped at the command wait limit while scanning the working checkout/native build trees. Not the complete static acceptance run; superseded by `tracked-static-checks.log`.
- `environment.json`: actual local interpreter, package versions, offscreen platform, and SQLite ODBC driver enumeration.
- `uap-import-scan.json`: production AST import scan. Zero UAP imports is **not** an assertion that equivalent algorithms are absent; see `docs/CAPABILITY_USAGE_AUDIT.md` for the manual semantic and call-path audit.

- `local-results.json`: machine-readable JUnit counts/durations and observed process exit codes.
- `source-binding.json`: all tracked non-document/evidence files matched the tested commit under Git clean-filter comparison; no product/test/workflow/lockfile drift.

## Runtime and setup limitations

The sandbox initially had only Python 3.11 and no scientific/Qt dependencies. Acceptance tests use a fresh **Python 3.12.16** source build and a `--require-hashes -r requirements.lock` install, plus the editable product without dependency replacement. Python sources were built using the existing migration build recipe, with a local work-directory/header-location adaptation. No product Python/dependency contract was changed.

The standalone-interpreter download failed with a TLS handshake error, so the interpreter was built from source. Real native Qt offscreen dependencies and SQLite ODBC were built using the repository's existing source manifests/bootstrap scripts. The bootstrap scripts' final interpreter invocation was adapted to the actual Python 3.12 test interpreter; no mocked native backend was substituted. System CA certificates were selected explicitly for Python HTTPS downloads. The Qt wrapper was initially attempted with the dependency-free 3.11 bootstrap interpreter and failed to import PySide6; actual Qt verification/tests use 3.12. Local native build output stays in ignored cache directories.

An initial full-suite launch was deliberately interrupted early after detecting an incorrect ODBC runtime path in the shell environment; it is not acceptance evidence. The recorded complete attempt uses `.cache/odbc-rebuild/{prefix/lib,config}` and `.cache/native-rebuild/prefix/lib`. No test, assertion, timeout, or original baseline contract was removed or weakened. A draft new invalid-metadata test incorrectly assumed a missing quantity type was invalid (the schema intentionally defaults it to QUALITY); the final test uses a genuinely invalid zero-time-column schema instead.

Raw runner/log timestamps reflect the execution environment's clock. No timestamps in raw evidence were rewritten.

## Hosted verification

Actual two-OS workflow: <https://github.com/hajianamir/EnergyBaseline/actions/runs/43713108678>, source commit `57c2fa179da3cbf6ca1d8d92d3bc0d696f93298c`.

- Windows job: `132808780944`.
- Linux job: `132808781148`.

**Final workflow status: SUCCESS. Both Windows and Linux jobs completed successfully on the tested commit.** Both the full-suite/coverage step and the complete GUI-directory/coverage step passed on each OS. Windows packaging/signing smoke, native-backend receipts, dependency audit and SBOM steps also completed under the unchanged workflow. These are CI smoke results, not a production signing, reboot, remote database TLS, or physical desktop certification.

`hosted-run.json`, `hosted-windows-job.json`, and `hosted-linux-job.json` are the actual GitHub run/job API responses captured after completion. Historical Python migration CI is not being reused as evidence for this correction. The follow-up evidence commit changes documentation/artifacts only; it does not change the tested implementation.
