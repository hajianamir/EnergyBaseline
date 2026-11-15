"""Real cross-process artifacts: an intact self-hashed pickle is still untrusted."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest

from energybaseline.lock.baseline_lock import BaselineLockManager, LockedBaselineRecord, ArtifactIntegrityError

CREATE = r"""
import sys
from pathlib import Path
from energybaseline.lock.baseline_lock import BaselineLockManager
class ExecutableOnLoad:
    def __reduce__(self):
        expression = "__import__('pathlib').Path(" + repr(sys.argv[2]) + ").write_text('executed')"
        return eval, (expression,)
obj = ExecutableOnLoad() if sys.argv[3] == 'executable' else {'approved': 42}
manager = BaselineLockManager(sys.argv[1])
record = manager.lock_baseline(run_id='foreign', target='target', model_name='test', family='F1',
    is_parametric=False, fitted_artifact=obj, formula_or_architecture='test')
print(record.manifest_path)
"""


def create_external(tmp_path, kind):
    marker = tmp_path / "must-not-exist"
    completed = subprocess.run(
        [sys.executable, "-c", CREATE, str(tmp_path / "locks"), str(marker), kind],
        capture_output=True,
        text=True,
        check=True,
        timeout=45,
    )
    manifest = Path(completed.stdout.strip())
    return LockedBaselineRecord(**json.loads(manifest.read_text())), marker


def test_valid_hashes_do_not_authorize_executable_external_artifact(tmp_path):
    record, marker = create_external(tmp_path, "executable")
    record.verify_integrity()
    manager = BaselineLockManager(Path(record.manifest_path).parent)
    with pytest.raises(ArtifactIntegrityError, match="independently trusted"):
        manager.load_verified_artifact(record)
    with pytest.raises(ArtifactIntegrityError):
        manager.load_verified_artifact(record, trusted_record_sha256="0" * 64)
    assert not marker.exists()


def test_independently_approved_record_can_be_loaded(tmp_path):
    record, marker = create_external(tmp_path, "data")
    manager = BaselineLockManager(Path(record.manifest_path).parent)
    # In production this approval must come from a separately trusted channel.
    assert manager.load_verified_artifact(record, trusted_record_sha256=record.record_sha256) == {"approved": 42}
    assert not marker.exists()
