"""Mandatory evidence sections and stable cross-format table identities."""

from .template import section_template

SECTIONS = (
    ("executive", "Executive Summary"),
    ("vsg_audit", "Data & VSG Audit"),
    ("tcp", "TCP"),
    ("test_catalog", "Statistical Test Catalog"),
    ("model_register", "Model Register"),
    ("shortlist", "Top-5 Shortlist"),
    ("selected_baseline", "Selected Baseline & UQ"),
    ("monitoring", "Monitoring/CUSUM"),
    ("auto_log", "AUTO Resolutions Log"),
    ("eda_atlas", "EDA Atlas"),
    ("correlation_atlas", "Correlation Atlas"),
    ("test_appendix", "Full Test Results Appendix"),
    ("sensitivity", "Decision-Sensitivity Summary"),
    ("rejected", "Rejected Models Register"),
    ("not_shortlisted", "Qualified Not-Shortlisted Register"),
    ("model_cards", "Model Cards"),
    ("drift", "Drift"),
    ("reproducibility", "Reproducibility"),
    ("glossary", "Glossary"),
    ("audit_appendix", "Audit Appendix"),
    ("selections", "User Selections Log"),
    ("storage", "Local Storage Confirmation"),
    ("disclosures", "Disclosures"),
)


def mandatory_sections(artifacts, *, tables=None, sentences=None, charts=None):
    tables = tables or {}
    sentences = sentences or {}
    charts = charts or {}
    sections = []
    for key, title in SECTIONS:
        aid = "availability." + key
        artifacts[aid] = {
            "kind": "status",
            "value": bool(tables.get(key) or sentences.get(key) or charts.get(key)),
            "source": "report assembly",
        }
        sections.append(
            {
                "id": key,
                "title": title,
                "template": section_template(),
                "tables": tables.get(key, []),
                "sentences": sentences.get(key, []) or [f"Evidence availability is recorded [artifact:{aid}]"],
                "charts": charts.get(key, []),
            }
        )
    return sections
