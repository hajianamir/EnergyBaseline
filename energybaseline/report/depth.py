"""Deterministic depth selection; executive details remain linked to the full archive."""

from enum import Enum


class Depth(str, Enum):
    EXECUTIVE = "executive"
    STANDARD = "standard"
    FULL = "full"


def selected_sections(model, depth="standard"):
    depth = Depth(depth)
    sections = model.data["sections"]
    if depth == Depth.FULL:
        return sections
    if depth == Depth.EXECUTIVE:
        wanted = {"executive", "selected_baseline", "disclosures", "reproducibility"}
    else:
        wanted = {
            s["id"]
            for s in sections
            if s["id"] not in {"test_appendix", "audit_appendix", "eda_atlas", "correlation_atlas"}
        }
    return [s for s in sections if s["id"] in wanted]


def visible_rows(rows, depth):
    return rows[:5] if Depth(depth) == Depth.EXECUTIVE else rows
