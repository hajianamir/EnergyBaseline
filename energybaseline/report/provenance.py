"""Deterministic seed lineage and explicit, connection-free runtime provenance."""

from __future__ import annotations
import hashlib
from importlib import metadata
import json
import os
import platform
import re
import subprocess
import random


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


def derive_seed(dataset_sha256, target, user_seed):
    if not re.fullmatch(r"[0-9a-f]{64}", dataset_sha256) or not isinstance(target, str) or not target:
        raise ValueError("Dataset digest and target are required for seed derivation")
    if isinstance(user_seed, bool) or not isinstance(user_seed, int):
        raise ValueError("User seed must be an integer")
    digest = hashlib.sha256(canonical([dataset_sha256, target, user_seed]).encode()).digest()
    return int.from_bytes(digest[:8], "big") % (2**31 - 1)


def seed_runtime(seed, *, torch_backend=False):
    """Seed process-global RNGs; estimator/Optuna seeds are passed explicitly by callers."""
    import numpy as np

    random.seed(seed)
    np.random.seed(seed)
    if torch_backend:
        import torch

        torch.manual_seed(seed)
        torch.use_deterministic_algorithms(True)


def provenance_manifest(dataset_sha256, config, *, commit_sha=None, target_seeds=None):
    from .safety import assert_safe

    assert_safe(config)
    if not re.fullmatch(r"[0-9a-f]{64}", dataset_sha256):
        raise ValueError("A dataset SHA-256 is required")
    if commit_sha is None:
        try:
            commit_sha = subprocess.check_output(
                ["git", "rev-parse", "HEAD"], stderr=subprocess.DEVNULL, text=True, timeout=5
            ).strip()
        except (OSError, subprocess.SubprocessError):
            commit_sha = "unavailable"
    packages = {d.metadata["Name"]: d.version for d in metadata.distributions() if d.metadata.get("Name")}
    return {
        "dataset_sha256": dataset_sha256,
        "config": json.loads(canonical(config)),
        "commit_sha": commit_sha,
        "packages": [{"name": name, "version": version} for name, version in sorted(packages.items())],
        "python": platform.python_version(),
        "os": platform.platform(),
        "cpu_count": os.cpu_count(),
        "target_seeds": dict(target_seeds or {}),
    }
