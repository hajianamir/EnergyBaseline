"""Gate-command compatibility spelling; delegate unchanged to the installed auditor.

The supplied gate uses ``python -m pip-audit`` whereas the distribution's Python
module is ``pip_audit``. This alias preserves arguments, exit status and the real
pinned implementation; it does not skip resolution or vulnerability checks.
"""

import runpy

if __name__ == "__main__":
    runpy.run_module("pip_audit", run_name="__main__")
