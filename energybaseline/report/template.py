"""Shared section grammar; all result values arrive through the report model."""

FIELDS = ("Purpose", "Method", "Settings", "Results", "Charts", "Interpretation", "Decision", "Caveats")
DEFAULTS = {
    "Purpose": "Document the evidence available for this analysis stage",
    "Method": "Persisted analysis artifacts; no statistical inference during rendering",
    "Settings": "See the linked configuration and provenance tables",
    "Results": "See artifact-linked tables and statements",
    "Charts": "Charts are omitted when their required observations are unavailable",
    "Interpretation": "Review evidence in its declared units and validity domain",
    "Decision": "Engineering review is required before changing a locked baseline",
    "Caveats": "Missing evidence is disclosed, not replaced with inferred results",
}


def section_template(settings=None):
    result = dict(DEFAULTS)
    if settings:
        if set(settings) - set(FIELDS):
            raise ValueError("Unknown section template field")
        result.update(settings)
    return result
