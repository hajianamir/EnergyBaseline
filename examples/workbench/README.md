# Executable workbench examples

These are small synthetic fixtures, not user data or performance benchmarks. See `docs/EXPLORATORY_WORKBENCH.md` for contracts, assumptions and limits.

```bash
python -m energybaseline.cli workbench examples/workbench/linear-optimize.json --out optimization.json
python -m energybaseline.cli workbench examples/workbench/regression-holdout.json --dataset examples/workbench/regression.csv --out regression.json
python -m energybaseline.cli workbench examples/workbench/entity-candidates.json --dataset examples/workbench/entities.csv --reference examples/workbench/entity-reference.csv --out candidates.json
```

`manifest.json` enumerates all 25 runnable jobs and their explicit dataset/reference bindings. Automated tests execute every entry through the real CLI. For the desktop, load the entry's dataset, open Setup → Exploratory Workbench, load its job JSON and any listed reference, then explicitly run. The optimization job needs no dataset.

Success means execution, not baseline acceptance or a favorable finding. Check solver status, PageRank convergence, suppressed groups, undefined quantities and cold-start coverage. Workbench jobs never promote or refit a locked baseline. The `prediction.csv` fixture deliberately shifts the holdout numeric predictors to exercise novelty detection and classification under a distribution change; its classification metrics are not an IID benchmark claim.
