"""Semantic runtime/manifest checks for the explicitly authorized Python 3.12 migration."""

from pathlib import Path
import tomllib

from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
import yaml

ROOT = Path(__file__).resolve().parents[2]


def test_project_and_linter_require_the_shared_python_minor():
    version = (ROOT / ".python-version").read_text().strip()
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert version == "3.12"
    constraint = SpecifierSet(project["project"]["requires-python"])
    assert "3.12.0" in constraint and "3.12.16" in constraint
    assert "3.11.18" not in constraint and "3.13.0" not in constraint
    assert project["tool"]["ruff"]["target-version"] == "py312"


def test_ci_and_nightly_share_version_file_and_check_runtime_before_tests():
    for filename in ("ci.yml", "r-reference.yml"):
        workflow = yaml.load((ROOT / ".github/workflows" / filename).read_text(), Loader=yaml.BaseLoader)
        for job in workflow["jobs"].values():
            steps = job["steps"]
            setups = [step for step in steps if step.get("uses", "").startswith("actions/setup-python@")]
            assert len(setups) == 1
            assert setups[0]["with"]["python-version-file"] == ".python-version"
            assert "python-version" not in setups[0]["with"]
            commands = "\n".join(step.get("run", "") for step in steps)
            assert "python scripts/verify_runtime.py" in commands
            assert "pip check" in commands
            assert commands.index("verify_runtime.py") < commands.index("pytest")


def pins(filename):
    result = {}
    for line in (ROOT / filename).read_text().splitlines():
        if not line or line.startswith(("#", " ")):
            continue
        requirement = Requirement(line.removesuffix(" \\"))
        result[requirement.name.lower().replace("_", "-")] = str(requirement.specifier)
    return result


def test_direct_pins_match_both_generated_manifests_and_keep_qt_native():
    direct, exact, hashed = (pins(name) for name in ("requirements.in", "requirements.txt", "requirements.lock"))
    for package, version in direct.items():
        assert exact[package] == hashed[package] == version
    assert exact == hashed
    assert direct["pyside6-essentials"] == direct["pyside6-addons"] == "==6.12.0"
    assert "pyside6" not in hashed and "pyside6-webengine" not in hashed
    assert "pyside6-pdf" not in hashed
