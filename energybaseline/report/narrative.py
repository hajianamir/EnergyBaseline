"""Artifact-bound factual statements and a build-time numeric agreement validator."""

from __future__ import annotations
import math
import re

NUMBER = re.compile(r"(?<![\w])[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?")
REFERENCE = re.compile(r"\[artifact:([A-Za-z][A-Za-z0-9_.-]*)\]")


def numeric_leaves(value):
    if isinstance(value, (float, int)) and not isinstance(value, bool):
        return [float(value)]
    if isinstance(value, str):
        return [float(m.group()) for m in NUMBER.finditer(value)]
    if isinstance(value, dict):
        return [n for v in value.values() for n in numeric_leaves(v)]
    if isinstance(value, list):
        return [n for v in value for n in numeric_leaves(v)]
    return []


def validate_sentence(text, artifacts):
    references = REFERENCE.findall(text)
    if not references or any(ref not in artifacts for ref in references):
        raise ValueError("Every narrative statement requires resolved artifact references")
    # A reference at the end of a paragraph cannot authorize earlier sentences.
    body = REFERENCE.sub("", text)
    if re.search(r"[.!?]\s+\S", body.strip()):
        raise ValueError("Narrative statements must be individual artifact-bound sentences")
    allowed = [n for ref in references for n in numeric_leaves(artifacts[ref]["value"])]
    for match in NUMBER.finditer(body):
        value = float(match.group())
        if not any(math.isclose(value, v, rel_tol=0, abs_tol=1e-12) for v in allowed):
            raise ValueError("Narrative number disagrees with its cited artifact")
    return True


def metric_statement(label, value, artifact_id):
    # Human labels containing decimal punctuation belong in tables, not prose.
    return f"{label}: {value!r} [artifact:{artifact_id}]"


def bind_annotation(text, artifact_id):
    """Quote each persisted annotation sentence with its source artifact reference."""
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    return [part + f" [artifact:{artifact_id}]" for part in parts if part]
