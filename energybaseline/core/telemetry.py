"""Product runtime telemetry and CycloneDX SBOM emitters (system.health, system.sbom, E2)."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import platform
import sys
from typing import Any

from .. import __version__
from ..config import get_base_dir, get_paths
from ..db.session import db_status, self_test
from .integrity import NON_DISABLEABLE_RULES
from .pool import get_pool_manager
from .tcp import MultipleTestingCorrection, TEST_CATALOG_SPECS


def build_system_health(*, run_db_selftest: bool = True) -> dict[str, Any]:
    """Produce deterministic-structure machine-readable ``system.health`` payload."""
    paths = get_paths()
    st = self_test() if run_db_selftest else {}
    dbs = db_status()
    ps = get_pool_manager().status_snapshot()
    overall_ok = bool((st.get("ok", True) if st else True) and ps.get("orphan_count", 0) == 0)
    body: dict[str, Any] = {
        "app_version": __version__,
        "command": "system.health",
        "compliance_core": {
            "multiple_testing_corrections": [c.value for c in MultipleTestingCorrection],
            "non_disableable_integrity_rules": sorted(NON_DISABLEABLE_RULES),
            "test_catalog_ids": sorted(TEST_CATALOG_SPECS.keys()),
        },
        "database": {
            "banner_level": dbs.get("banner_level", "none"),
            "fallback": bool(dbs.get("fallback", False)),
            "masked_url": dbs.get("masked_url", ""),
            "pragmas": dict(dbs.get("pragmas") or {}),
            "rung": dbs.get("rung", "sqlite"),
            "selftest": st,
        },
        "ok": overall_ok,
        "paths": {
            "app_home": str(get_base_dir()),
            "cache_dir": str(paths["cache"]),
        },
        "platform": platform.platform(),
        "pool": {
            "active_job_count": len(ps.get("active_jobs", [])),
            "max_workers": ps.get("max_workers", 0),
            "orphan_count": ps.get("orphan_count", 0),
            "protocol_version": ps.get("protocol_version", 1),
            "state": ps.get("state", "idle"),
        },
        "python_version": sys.version.split()[0],
    }
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str).encode("utf-8")
    body["payload_sha256"] = hashlib.sha256(canonical).hexdigest()
    return body


def build_system_sbom() -> dict[str, Any]:
    """Produce CycloneDX 1.5 JSON SBOM directly from active runtime distributions (``system.sbom``)."""
    seen: set[tuple[str, str]] = set()
    components: list[dict[str, Any]] = []
    for dist in importlib.metadata.distributions():
        meta = dist.metadata
        name = (meta.get("Name") or "").strip()
        version = (dist.version or "").strip()
        if not name or (name.lower(), version) in seen:
            continue
        seen.add((name.lower(), version))
        lic = (meta.get("License") or meta.get("Classifier") or "UNKNOWN").splitlines()[0][:120]
        purl = f"pkg:pypi/{name.lower()}@{version}"
        components.append(
            {
                "bom-ref": purl,
                "licenses": [{"license": {"name": lic}}],
                "name": name,
                "purl": purl,
                "type": "library",
                "version": version,
            }
        )
    components.sort(key=lambda c: (c["name"].lower(), c["version"]))
    doc: dict[str, Any] = {
        "bomFormat": "CycloneDX",
        "command": "system.sbom",
        "component_count": len(components),
        "components": components,
        "metadata": {
            "component": {
                "name": "energybaseline",
                "type": "application",
                "version": __version__,
            }
        },
        "specVersion": "1.5",
        "version": 1,
    }
    canonical = json.dumps(doc, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    doc["sbom_sha256"] = hashlib.sha256(canonical).hexdigest()
    return doc
