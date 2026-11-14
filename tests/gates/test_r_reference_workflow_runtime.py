"""Guard the nightly R workflow's supported runner/runtime pair and parity gates."""

from pathlib import Path

import yaml


WORKFLOW = Path(__file__).resolve().parents[2] / ".github/workflows/r-reference.yml"


def load_workflow():
    # BaseLoader preserves the GitHub Actions `on` key instead of YAML 1.1 booleans.
    return yaml.load(WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)


def test_nightly_python_has_a_build_for_the_pinned_ubuntu_runner():
    job = load_workflow()["jobs"]["r-reference-nightly"]
    assert job["runs-on"] == "ubuntu-26.04"
    setup = next(step for step in job["steps"] if step.get("uses", "").startswith("actions/setup-python@"))
    # Verified against actions/python-versions/versions-manifest.json: the x64
    # 3.11.18 build supports Ubuntu 26.04; the old 3.11.9 build does not.
    assert setup["with"]["python-version"] == "3.11.18"


def test_nightly_first_party_actions_use_node24_generations():
    steps = load_workflow()["jobs"]["r-reference-nightly"]["steps"]
    actions = {step["uses"].split("@")[0]: step["uses"].split("@")[1] for step in steps if "uses" in step}
    assert actions["actions/checkout"] == "v5"
    assert actions["actions/setup-python"] == "v6"
    assert actions["actions/github-script"] == "v8"


def test_runtime_fix_keeps_nightly_triggers_and_reference_checks():
    workflow = load_workflow()
    assert workflow["on"]["schedule"] == [{"cron": "0 3 * * *"}]
    assert "workflow_dispatch" in workflow["on"]
    steps = workflow["jobs"]["r-reference-nightly"]["steps"]
    r_setup = next(step for step in steps if step.get("uses") == "r-lib/actions/setup-r@v2")
    assert r_setup["with"]["r-version"] == "4.4.1"
    commands = "\n".join(step.get("run", "") for step in steps)
    assert "pip install --require-hashes -r requirements.lock" in commands
    assert "pytest tests/test_reference_staleness.py -q" in commands
    assert 'raise SystemExit("R reference divergence detected:' in commands
    assert 'committed["tolerances"]["statistic_abs"]' in commands
    assert 'committed["tolerances"]["p_value_abs"]' in commands
    assert not any(step.get("continue-on-error") == "true" for step in steps)
