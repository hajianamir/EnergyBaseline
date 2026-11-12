# Third-Party Software Notices

EnergyBaseline (`Proprietary / Internal` — see `LICENSE`) incorporates third-party open-source Python packages under permissive (`BSD`, `MIT`, `Apache-2.0`, `PSF-2.0`) and dynamically linked `LGPL-3.0` licenses.

Per ratified decision `D5 = A` and standing assumption `S7`:
- Zero `GPL` viral runtime libraries are included (legacy GTK/Pango/Cairo HTML-to-PDF dependencies have been permanently removed).
- `PySide6` and `shiboken6` are dynamically loaded unmodified wheel binaries under `LGPL-3.0-only`, permitting proprietary desktop distribution in one-folder (`_internal/`) layout where Qt shared libraries remain replaceable on disk.
- Machine-readable exact versions and cryptographic package hashes are maintained in `requirements.lock` and exported on every CI build as a CycloneDX SBOM (`sbom.json`).

## Direct Runtime & Packaging Dependencies

| Package | Version | License | Upstream Project |
|---|---|---|---|
| `alembic` | `1.20.1` | MIT | https://alembic.sqlalchemy.org |
| `arch` | `9.0.0` | NCSA / BSD-like | https://github.com/bashtage/arch |
| `catboost` | `1.2.10` | Apache-2.0 | https://catboost.ai |
| `cryptography` | `46.0.7` | Apache-2.0 OR BSD-3-Clause | https://github.com/pyca/cryptography |
| `Jinja2` | `3.1.6` | BSD-3-Clause | https://palletsprojects.com/p/jinja |
| `joblib` | `1.6.0` | BSD-3-Clause | https://joblib.readthedocs.io |
| `keyring` | `25.7.0` | MIT | https://github.com/jaraco/keyring |
| `lightgbm` | `4.6.0` | MIT | https://github.com/microsoft/LightGBM |
| `mapie` | `1.6.0` | BSD-3-Clause | https://github.com/scikit-learn-contrib/MAPIE |
| `matplotlib` | `3.10.8` | PSF-based (Matplotlib License) | https://matplotlib.org |
| `numpy` | `2.4.6` | BSD-3-Clause | https://numpy.org |
| `openpyxl` | `3.1.5` | MIT | https://openpyxl.readthedocs.io |
| `optuna` | `4.8.0` | MIT | https://optuna.org |
| `pandas` | `3.1.1` | BSD-3-Clause | https://pandas.pydata.org |
| `pillow` | `12.2.0` | MIT-CMU | https://python-pillow.org |
| `psycopg2-binary` | `2.9.11` | LGPL-3.0-with-linking-exception | https://www.psycopg.org |
| `pydantic` | `2.14.3` | MIT | https://docs.pydantic.dev |
| `PyInstaller` | `6.19.0` | GPL-2.0-or-later WITH Bootloader-Exception | https://pyinstaller.org |
| `PyMySQL` | `1.1.2` | MIT | https://github.com/PyMySQL/PyMySQL |
| `pyodbc` | `5.3.0` | MIT | https://github.com/mkleehammer/pyodbc |
| `PySide6` / `PySide6-Essentials` / `shiboken6` | `6.9.3` | LGPL-3.0-only | https://wiki.qt.io/Qt_for_Python |
| `pywin32` | `312` | PSF-2.0 | https://github.com/mhammond/pywin32 |
| `PyYAML` | `6.0.3` | MIT | https://pyyaml.org |
| `reportlab` | `5.1.0` | BSD-3-Clause | https://www.reportlab.com |
| `scikit-learn` | `1.9.1` | BSD-3-Clause | https://scikit-learn.org |
| `scipy` | `1.17.1` | BSD-3-Clause | https://scipy.org |
| `shap` | `0.51.0` | MIT | https://github.com/shap/shap |
| `SQLAlchemy` | `2.1.8` | MIT | https://www.sqlalchemy.org |
| `sqlglot` | `30.31.0` | MIT | https://github.com/tobymao/sqlglot |
| `statsmodels` | `0.15.0` | BSD-3-Clause | https://www.statsmodels.org |
| `torch` | `2.15.0` | BSD-3-Clause | https://pytorch.org |
| `xgboost` | `3.2.0` | Apache-2.0 | https://xgboost.readthedocs.io |

## Direct Development & Verification Tooling (Not Distributed in Runtime Bundle)

| Package | Version | License | Purpose |
|---|---|---|---|
| `cyclonedx-bom` | `8.0.1` | Apache-2.0 | CycloneDX SBOM generation |
| `hypothesis` | `6.170.15` | MPL-2.0 | Property-based test generation |
| `pip-audit` | `2.10.1` | Apache-2.0 | Dependency CVE scanning |
| `pyflakes` | `4.1.0` | MIT | Unused-symbol / undefined-name static analysis |
| `pytest` / `pytest-cov` / `pytest-qt` | `9.2.0` / `7.1.0` / `4.5.0` | MIT | Automated test runner, coverage, and Qt harness |
| `ruff` | `0.17.5` | MIT | Python linter and formatter |
