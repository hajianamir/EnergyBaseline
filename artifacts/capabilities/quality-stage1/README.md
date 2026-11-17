# Capability Stage 1: raw evidence

This directory concerns the new six-axis quality engine only. It does not certify completion of the broader capability request.

- `deep-search.json`: second-pass search of 146 production modules at the pre-change commit, 1,449 AST import entries, candidate identifier matches and actual probes of 11 installed public backend APIs. Candidate token matches do not establish semantic integration.
- `external-candidates.json`: 16 actual external GitHub repository lookups with source URLs and license metadata. Research only; none is silently installed or claimed executable.
- `focused.log` / `focused.xml`: 48 new unit/CLI and real Qt integration tests, exit 0.
- `example.json`: actual CLI execution of the versioned synthetic example policy with all six dimensions fully assessed and the complete-score threshold enforced; not a benchmark or certification of a real reference source.
- `full-suite.log` / `.xml`: **552 passed in 569.01 seconds**, no failures/errors/skips, exit **0** including interpreter teardown. The earlier 550-pass run belonged to source `35769ac` and is not substituted for final-source acceptance.
- `gui.log` / `.xml`: **25 passed in 50.30 seconds**, exit **0**, complete real Qt GUI test directory.
- `static-checks.log`: successful Ruff, formatting, Pyflakes, pip check, original-test integrity, web/environment/thread guards and requirement matrix checks on a clean `git archive` of the tested commit.
- `environment.json`, `source-binding.json`, `local-results.json`: actual runtime/packages/native adapters, all tracked non-doc/evidence source bindings and observed local test exit/count/duration summaries.

Historical VSG/Python migration CI is not acceptance evidence for these additions. Fresh hosted status is recorded below after completion.

Implementation does not add dependencies, modify the Python 3.12 contract, weaken/delete/skip original tests, change baseline acceptance or run inference before authorization. No raw or reference data values are serialized by the audit. Existing legacy quality scoring remains unchanged; this is a new Setup-tab/CLI read-only workflow.

During development the GUI reference test exposed timestamp-typed input keys versus CSV-string reference keys. Explicit `datetime_utc` matching normalization was implemented instead of guessing coercions or silently dropping the check; the final GUI test exercises that route and a unit test rejects timezone-equivalent duplicate keys.

A further precision regression exposed the risk of float-rounding integer truth/limits above 2**53. Exact comparison/alignment and range handling were added with three regressions; nonzero-tolerance checks on those integers fail closed pending explicit rescaling. The initial full-suite attempt on the superseded draft was interrupted before completion and is not acceptance evidence. The first hosted run (`43720811540`, source `fc7c445`) likewise predates the precision correction and must not be substituted for the final tested source.

## Tested source and hosted verification

Final implementation commit: `ef78c2b6bd0e2a8b81ef7220007950337b3d73cf`.

Final workflow: https://github.com/hajianamir/EnergyBaseline/actions/runs/43724721792

The additional mixed/string numeric coercion guard has two regressions and supersedes source `35769ac` / workflow `43721127916`. Earlier completed runs, if green, are not substituted for this final source. Source-binding/runtime/local result files now bind the final implementation commit: 301 tracked non-doc/evidence files checked, zero drift. **Final workflow status: SUCCESS. Windows and Linux jobs both completed successfully on the final implementation commit.** Both full-suite/coverage and complete GUI-directory/coverage steps passed on each OS. Windows packaging/signing smoke, native receipts, dependency audit and SBOM steps completed under the unchanged workflow; these are CI smoke results, not production signing, physical desktop, remote database TLS or reboot certification.

`hosted-run.json`, `hosted-windows-job.json`, and `hosted-linux-job.json` are actual completed GitHub run/job API responses. Windows job: `132844076676`; Linux job: `132844076405`. The follow-up commit contains documentation/evidence only, with no tested-code drift. Raw timestamps reflect environment clocks and were not rewritten.
