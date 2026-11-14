"""Disclosures are mandatory shared report content, never renderer-specific omissions."""


def disclosure_banner(model):
    records = model.data["disclosures"]
    return [
        {"category": key, "details": list(records.get(key, [])) or ["None recorded"]}
        for key in ("manual_overrides", "skipped_families", "assumption_fallbacks")
    ]
