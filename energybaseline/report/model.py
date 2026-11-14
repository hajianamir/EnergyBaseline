"""Immutable, JSON-native single source of report facts for every renderer."""

from __future__ import annotations
import hashlib
import json
import math
from pathlib import Path
import re
from .provenance import canonical
from .safety import assert_safe
from .template import FIELDS
from .narrative import validate_sentence, NUMBER


class ReportModel:
    __slots__ = ("_json",)

    def __init__(self, payload):
        data = json.loads(canonical(payload))
        self.validate(data)
        digest = hashlib.sha256(canonical({k: v for k, v in data.items() if k != "report_id"}).encode()).hexdigest()
        if "report_id" in data and data["report_id"] != digest:
            raise ValueError("Report digest mismatch")
        data["report_id"] = digest
        object.__setattr__(self, "_json", canonical(data))

    def __setattr__(self, name, value):
        raise TypeError("Report state is immutable")

    def __delattr__(self, name):
        raise TypeError("Report state is immutable")

    @property
    def data(self):
        return json.loads(self._json)

    @property
    def report_id(self):
        return self.data["report_id"]

    @property
    def metrics(self):
        data = self.data
        return [{**m, "value": data["artifacts"][m["artifact_id"]]["value"]} for m in data["metrics"]]

    def table(self, artifact_id):
        return self.data["artifacts"][artifact_id]["value"]

    def to_json(self):
        return self._json + "\n"

    def save(self, path):
        Path(path).write_text(self.to_json(), encoding="utf-8")

    @classmethod
    def load(cls, path):
        return cls(json.loads(Path(path).read_text(encoding="utf-8")))

    @staticmethod
    def validate(data):
        assert_safe(data)
        required = {
            "schema_version",
            "title",
            "artifacts",
            "metrics",
            "sections",
            "charts",
            "provenance",
            "disclosures",
        }
        if set(data) - required - {"report_id"} or not required <= set(data) or data["schema_version"] != 1:
            raise ValueError("Unsupported report schema")
        artifacts = data["artifacts"]
        if any(not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]*", k) for k in artifacts):
            raise ValueError("Unsafe artifact ID")
        for item in artifacts.values():
            if set(item) != {"kind", "value", "source"}:
                raise ValueError("Invalid artifact record")
        metric_ids = []
        for metric in data["metrics"]:
            aid = metric["artifact_id"]
            metric_ids.append(aid)
            if aid not in artifacts or artifacts[aid]["kind"] != "metric":
                raise ValueError("Unresolved metric")
            value = artifacts[aid]["value"]
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
            ):
                raise ValueError("Metrics must be finite or explicitly unavailable")
        if len(set(metric_ids)) != len(metric_ids):
            raise ValueError("Duplicate metric ID")
        ids = []
        for section in data["sections"]:
            ids.append(section["id"])
            if not re.fullmatch(r"[a-z][a-z0-9_]*", section["id"]) or set(section["template"]) != set(FIELDS):
                raise ValueError("Invalid section grammar")
            if any(NUMBER.search(str(v)) for v in section["template"].values()):
                raise ValueError("Numeric facts belong in artifact-bound statements, not section explanations")
            for statement in section["sentences"]:
                validate_sentence(statement, artifacts)
            for aid in section["tables"]:
                if (
                    aid not in artifacts
                    or artifacts[aid]["kind"] != "table"
                    or not isinstance(artifacts[aid]["value"], list)
                ):
                    raise ValueError("Unresolved table artifact")
            if any(c not in data["charts"] for c in section["charts"]):
                raise ValueError("Unresolved chart")
        if len(set(ids)) != len(ids):
            raise ValueError("Duplicate section ID")
        for values in data["disclosures"].values():
            for statement in values:
                validate_sentence(statement, artifacts)
        from .charts import validate_chart

        for chart in data["charts"].values():
            validate_chart(chart, artifacts)
