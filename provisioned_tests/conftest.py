"""Execution tests for separately provisioned engine runtimes and pretrained model assets.

These tests never install anything. Point ``EB_PROVISIONED_HOME`` at an application home
that was provisioned with ``energybaseline engines install`` and ``assets install``; the
session fails immediately when it is missing, because nothing here may be skipped.
"""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import sys
import tempfile

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def pytest_sessionstart(session):
    home = os.environ.get("EB_PROVISIONED_HOME", "")
    if not home or not Path(home, "engines").is_dir() or not Path(home, "model-assets").is_dir():
        raise pytest.UsageError(
            "EB_PROVISIONED_HOME must name an application home with provisioned engines and model assets."
        )
    os.environ["EB_HOME"] = str(Path(home).resolve())
    os.environ["EB_NONINTERACTIVE"] = "1"
    for key in ("DATABASE_URL", "POSTGRES_URL", "DB_CONNECTION_STRING", "DB_URI"):
        os.environ.pop(key, None)
    # Every input here is the repository's synthetic example, so a failed engine's own log may
    # be kept and shown. Product results never export it.
    session.config._engine_logs = tempfile.mkdtemp(prefix="eb-engine-logs-")
    session.config._engine_failures = []
    os.environ["EB_ENGINE_DIAGNOSTICS"] = session.config._engine_logs


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    kept = getattr(item.config, "_engine_logs", None)
    if kept is None or report.when != "call":
        return
    for log in sorted(Path(kept).glob("*-engine.log")):
        if report.failed:
            text = log.read_text(encoding="utf-8", errors="replace")[-12000:]
            item.config._engine_failures.append((item.nodeid, log.name, text))
            report.sections.append((f"retained {log.name}", text))
        log.unlink()


def pytest_terminal_summary(terminalreporter, config):
    failures = getattr(config, "_engine_failures", [])
    for nodeid, name, text in failures:
        terminalreporter.write_sep("-", f"{name} retained for {nodeid}")
        terminalreporter.write_line(text)
        if os.environ.get("GITHUB_ACTIONS") == "true":
            escaped = text[-6000:].replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
            terminalreporter.write_line(f"::error title={name} retained for {nodeid.replace(',', ' ')}::{escaped}")


def pytest_sessionfinish(session):
    shutil.rmtree(getattr(session.config, "_engine_logs", ""), ignore_errors=True)


@pytest.fixture(scope="session")
def provisioned_home():
    return Path(os.environ["EB_HOME"])
