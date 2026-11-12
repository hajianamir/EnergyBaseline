import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


@pytest.fixture(scope="session", autouse=True)
def eb_home():
    tmp = tempfile.mkdtemp(prefix="eb_test_")
    os.environ["EB_HOME"] = tmp
    for k in ("DATABASE_URL", "POSTGRES_URL", "DB_CONNECTION_STRING", "DB_URI"):
        os.environ.pop(k, None)
    return Path(tmp)
