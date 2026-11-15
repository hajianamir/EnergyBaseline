"""Reproduce review observations without importing the application or opening a database.

Run from the repository root with Python 3.11 and sqlglot==30.31.0.
Only selected, unchanged source definitions are executed. All credential values
are synthetic canaries. These are review probes, not full integration tests.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent


def tree(relative):
    return ast.parse((ROOT / relative).read_text(encoding="utf-8"))


def definitions(relative, names, namespace=None):
    selected = []
    for node in tree(relative).body:
        targets = [t.id for t in node.targets if isinstance(t, ast.Name)] if isinstance(node, ast.Assign) else []
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            targets = [node.target.id]
        if getattr(node, "name", None) in names or set(targets) & set(names):
            selected.append(node)
    module = ast.Module(
        body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), *selected],
        type_ignores=[],
    )
    ns = dict(namespace or {})
    exec(compile(ast.fix_missing_locations(module), relative, "exec"), ns)
    return ns


def inventory():
    broad, bare, silent, functions, domain, dataclasses = [], [], [], [], [], {}
    modules = sorted((ROOT / "energybaseline").rglob("*.py"))
    for path in modules:
        rel = path.relative_to(ROOT).as_posix()
        source = path.read_text(encoding="utf-8")
        t = ast.parse(source)
        count = len(re.findall(r"ASHRAE|IPMVP", source, re.I))
        if count:
            domain.append({"file": rel, "occurrences": count})
        for node in ast.walk(t):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                functions.append(
                    {
                        "file": rel,
                        "name": node.name,
                        "line": node.lineno,
                        "physical_lines": node.end_lineno - node.lineno + 1,
                    }
                )
            if isinstance(node, ast.ExceptHandler):
                if node.type is None:
                    bare.append({"file": rel, "line": node.lineno})
                if isinstance(node.type, ast.Name) and node.type.id == "Exception":
                    broad.append({"file": rel, "line": node.lineno})
                    if len(node.body) == 1 and isinstance(node.body[0], ast.Pass):
                        silent.append({"file": rel, "line": node.lineno})
    pipeline = tree("energybaseline/core/pipeline.py")
    for node in pipeline.body:
        if isinstance(node, ast.ClassDef) and node.name in ("TargetResult", "RunResult"):
            fields = [f for f in node.body if isinstance(f, ast.AnnAssign)]
            dataclasses[node.name] = {
                "fields": len(fields),
                "direct_dict_fields": sum(
                    isinstance(f.annotation, ast.Name) and f.annotation.id == "dict" for f in fields
                ),
            }
    requirements = {}
    for name in ("requirements.in", "requirements.txt", "requirements.lock"):
        requirements[name] = {}
        for line in (ROOT / name).read_text().splitlines():
            match = re.match(r"^([\w.-]+)==([^\s;\\]+)", line)
            if match:
                requirements[name][match[1].lower().replace("_", "-")] = match[2]
    differences = {
        name: {
            key: {"declared": value, "locked": requirements["requirements.lock"].get(key)}
            for key, value in entries.items()
            if requirements["requirements.lock"].get(key) != value
        }
        for name, entries in requirements.items()
        if name != "requirements.lock"
    }
    unit_definitions = next(
        node
        for node in tree("energybaseline/core/ingest.py").body
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == "_RAW_UNITS"
    )
    return {
        "requirement_name_version_counts": {name: len(entries) for name, entries in requirements.items()},
        "requirement_name_version_differences_from_lock": differences,
        "unit_alias_count": len(unit_definitions.value.elts),
        "source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "python": sys.version.split()[0],
        "python_modules": len(modules),
        "except_exception_count": len(broad),
        "bare_except_count": len(bare),
        "except_exception_pass_only_count": len(silent),
        "except_exception_pass_only_locations": silent,
        "domain_token_scope": "Case-insensitive ASHRAE|IPMVP occurrence count in energybaseline/**/*.py, including comments/docstrings/identifiers",
        "domain_token_occurrences": sum(x["occurrences"] for x in domain),
        "domain_token_files": domain,
        "dataclasses": dataclasses,
        "largest_functions": sorted(functions, key=lambda f: f["physical_lines"], reverse=True)[:12],
        "run_analysis": next(
            f for f in functions if f["file"] == "energybaseline/core/pipeline.py" and f["name"] == "run_analysis"
        ),
        "module_line_counts": {
            r: len((ROOT / r).read_text().splitlines())
            for r in [
                "energybaseline/core/models.py",
                "energybaseline/core/pipeline.py",
                "energybaseline/core/scoring.py",
                "energybaseline/core/mining.py",
                "energybaseline/core/report.py",
                "energybaseline/gui/pages_setup.py",
            ]
        },
    }


def probes():
    result = {}
    mask = definitions("energybaseline/config/settings.py", {"_mask"})["_mask"]
    canary = "REVIEW_CANARY_NOT_A_REAL_SECRET"
    cases = {
        "userinfo": f"postgresql://review:{canary}@localhost/review",
        "query_password": f"postgresql://localhost/review?password={canary}",
        "odbc": f"DRIVER={{Review}};UID=review;PWD={canary};DATABASE=review",
        "encoded_odbc": f"mssql+pyodbc:///?odbc_connect=DRIVER%3DReview%3BPWD%3D{canary}",
    }
    result["current_mask"] = {
        name: {"synthetic_input": value, "output": mask(value), "secret_survives": canary in mask(value)}
        for name, value in cases.items()
    }
    temperature = definitions("energybaseline/core/features.py", {"TEMP_RE", "find_temperature_col"}, {"re": re})[
        "find_temperature_col"
    ]
    proposed = re.compile(
        r"\b(temp|temperature|oat|oa_temp|tdb|dry[_ ]?bulb|outdoor[_ ]?t|t_?out|tamb|ambient)\b", re.I
    )
    names = [
        "attempt_count",
        "template_id",
        "temporary",
        "tempo",
        "boat_count",
        "temperature_C",
        "outdoor_temp_C",
        "oa_temp",
        "dry_bulb",
        "supply_temperature",
    ]
    result["temperature_detection"] = [
        {"column": name, "current_detected": temperature([name]), "proposed_regex_matches": bool(proposed.search(name))}
        for name in names
    ]

    old = definitions("energybaseline/core/scoring.py", {"WEIGHTS"})["WEIGHTS"]
    new = definitions("energybaseline/scoring/composite.py", {"CRITERIA_13", "DEFAULT_WEIGHTS_13"})[
        "DEFAULT_WEIGHTS_13"
    ]
    result["scoring_schemas"] = {
        "legacy_profile_sums": {k: sum(v.values()) for k, v in old.items()},
        "new_default_sum": sum(new.values()),
        "shared_keys": sorted(set(old["balanced"]) & set(new)),
        "legacy_only": sorted(set(old["balanced"]) - set(new)),
        "new_only": sorted(set(new) - set(old["balanced"])),
    }
    # Execute the unchanged max-lag/max-roll assignment with FeatureSpec's actual
    # declared list[int] representation. No complete analysis run is simulated.
    assignment = next(
        n
        for n in ast.walk(tree("energybaseline/core/pipeline.py"))
        if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "max_lag_used" for t in n.targets)
    )

    class Spec:
        lags = [1, 7]

    try:
        exec(
            compile(
                ast.fix_missing_locations(ast.Module(body=[assignment], type_ignores=[])),
                "energybaseline/core/pipeline.py",
                "exec",
            ),
            {"spec": Spec()},
        )
    except TypeError as exc:
        result["integer_lag_contract"] = {
            "observed_exception": type(exc).__name__,
            "message": str(exc),
            "input_lags": Spec.lags,
        }
    else:
        raise AssertionError("Expected the reviewed integer-lag contract mismatch")

    import sqlglot
    from sqlglot import exp
    from sqlglot.errors import SqlglotError

    sql = definitions(
        "energybaseline/core/sqlsource.py",
        {
            "SourceError",
            "_strip_leading_trailing",
            "_ALLOWED_ROOT_EXPS",
            "_FORBIDDEN_AST_EXPS",
            "_FORBIDDEN_FUNCTIONS",
            "validate_select",
            "_IDENT",
            "build_query",
        },
        {"re": re, "sqlglot": sqlglot, "exp": exp, "SqlglotError": SqlglotError},
    )
    result["sqlglot_version"] = sqlglot.__version__
    result["query_builder"] = {
        "invalid_timestamp_accepted": sql["build_query"]("meters", ["ts", "reading"], "ts", "not-a-datetime"),
        "table_fragment_accepted": sql["build_query"]("meters CROSS JOIN other_readable_table", ["reading"]),
        "source_builder_returns": type(sql["build_query"]("meters", ["ts"], "ts", "2026-01-01")).__name__,
    }
    for payload in ["SELECT 1; DELETE FROM meters", "DELETE FROM meters"]:
        try:
            sql["validate_select"](payload)
        except sql["SourceError"]:
            continue
        raise AssertionError("Existing AST protection unexpectedly failed")
    result["readonly_ast_guards"] = "Both multiple-statement and DELETE probes rejected"
    return result


def main():
    OUT.mkdir(exist_ok=True)
    inv, findings = inventory(), probes()
    (OUT / "inventory.json").write_text(json.dumps(inv, indent=2) + "\n", encoding="utf-8")
    (OUT / "probe_results.json").write_text(json.dumps(findings, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "source_commit": inv["source_commit"],
                "status": "review probes completed; not a full test-suite run",
                "python_modules": inv["python_modules"],
                "except_exception": inv["except_exception_count"],
                "current_mask_secret_survives": {k: v["secret_survives"] for k, v in findings["current_mask"].items()},
                "source_files_unchanged": True,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
