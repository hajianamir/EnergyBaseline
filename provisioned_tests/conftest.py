"""Execution tests for separately provisioned engine runtimes and pretrained model assets.

These tests never install anything. Point ``EB_PROVISIONED_HOME`` at an application home
that was provisioned with ``energybaseline engines install`` and ``assets install``; the
session fails immediately when it is missing, because nothing here may be skipped.
"""

from __future__ import annotations

import os
from pathlib import Path
import sys

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
    for key in ("DATABASE_URL", "POSTGRES_URL", "DB_CONNECTION_STRING", "DB_URI", "EB_ENGINE_DIAGNOSTICS"):
        os.environ.pop(key, None)


@pytest.fixture(scope="session")
def provisioned_home():
    return Path(os.environ["EB_HOME"])
