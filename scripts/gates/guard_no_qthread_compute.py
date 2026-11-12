"""AST gate enforcing R-1 / SHL-5: zero compute inside ``QThread`` / ``QRunnable`` (active from Gate G1).

Violations flagged:
1. Any import or subclassing of ``QThread``, ``QRunnable``, or ``QThreadPool`` inside
   ``energybaseline/gui/pages_setup.py`` or ``energybaseline/core/**``.
2. Any ``QThread`` / ``QRunnable`` subclass anywhere in ``energybaseline/**`` that imports or invokes
   compute symbols/modules (``run_analysis``, ``analyze_target``, ``evaluate_candidate``, ``run_mining``,
   ``pipeline``, ``models``, ``nn``, ``mining``, ``optuna``, ``torch``, ``sklearn``, ``xgboost``, ``lightgbm``).
3. Any ``FuncWorker(...)`` instantiation whose target callable references a compute symbol/module.
"""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

DEFAULT_ROOT = Path(__file__).resolve().parents[2]

QT_THREAD_SYMBOLS = frozenset({"QThread", "QRunnable", "QThreadPool"})
NO_QTHREAD_FILES = frozenset({"energybaseline/gui/pages_setup.py"})
NO_QTHREAD_PREFIXES = ("energybaseline/core/",)

COMPUTE_IDENTIFIERS = frozenset(
    {
        "run_analysis",
        "analyze_target",
        "evaluate_candidate",
        "run_mining",
        "hpo",
        "SkMLP",
        "TorchSeqRegressor",
        "optuna",
        "torch",
        "sklearn",
        "xgboost",
        "lightgbm",
        "statsmodels",
        "pipeline",
        "mining",
        "nn",
    }
)

SKIP_DIRS = frozenset(
    {
        ".git",
        ".venv",
        "venv",
        "__pycache__",
        ".pytest_cache",
        ".ruff_cache",
        ".mypy_cache",
        "build",
        "dist",
        "_internal",
        "artifacts",
    }
)

EXEMPT_REL_PREFIXES = (
    "scripts/gates/guard_no_qthread_compute.py",
    "tests/gates/",
)


def _dotted_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        left = _dotted_name(node.value)
        return f"{left}.{node.attr}" if left else node.attr
    return ""


def _references_compute(node: ast.AST) -> str | None:
    for sub in ast.walk(node):
        if isinstance(sub, ast.Name) and sub.id in COMPUTE_IDENTIFIERS:
            return sub.id
        if isinstance(sub, ast.Attribute) and sub.attr in COMPUTE_IDENTIFIERS:
            return sub.attr
        if isinstance(sub, ast.Import):
            for alias in sub.names:
                top = alias.name.split(".")[0]
                if top in COMPUTE_IDENTIFIERS:
                    return alias.name
        if isinstance(sub, ast.ImportFrom) and sub.module:
            parts = set(sub.module.split("."))
            hit = parts & COMPUTE_IDENTIFIERS
            if hit:
                return sorted(hit)[0]
    return None


def _is_qthread_forbidden_path(rel_posix: str) -> bool:
    if rel_posix in NO_QTHREAD_FILES:
        return True
    return any(rel_posix.startswith(p) for p in NO_QTHREAD_PREFIXES)


def scan_file(py_path: Path, rel_posix: str) -> list[str]:
    try:
        source = py_path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=rel_posix)
    except SyntaxError as exc:
        return [f"{rel_posix}:{exc.lineno}: syntax error while scanning AST: {exc.msg}"]
    except UnicodeDecodeError as exc:
        return [f"{rel_posix}:1: utf-8 decode error: {exc}"]

    violations: list[str] = []
    strict_no_qthread = _is_qthread_forbidden_path(rel_posix)

    for node in ast.walk(tree):
        # 1. Strict ban on QThread/QRunnable imports or usage in pages_setup.py and core/**
        if strict_no_qthread:
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    if alias.name in QT_THREAD_SYMBOLS:
                        violations.append(
                            f"{rel_posix}:{node.lineno}: forbidden {alias.name} import in compute/setup module"
                        )
            elif isinstance(node, ast.Name) and node.id in QT_THREAD_SYMBOLS:
                violations.append(f"{rel_posix}:{node.lineno}: forbidden {node.id} reference in compute/setup module")

        # 2. Inspect QThread / QRunnable subclasses anywhere in the codebase
        if isinstance(node, ast.ClassDef):
            base_names = {_dotted_name(b).split(".")[-1] for b in node.bases}
            if base_names & QT_THREAD_SYMBOLS:
                comp = _references_compute(node)
                if comp:
                    violations.append(
                        f"{rel_posix}:{node.lineno}: QThread/QRunnable subclass '{node.name}' invokes compute symbol '{comp}'"
                    )

        # 3. Inspect FuncWorker(...) calls anywhere in the codebase
        if isinstance(node, ast.Call):
            fn_name = _dotted_name(node.func).split(".")[-1]
            if fn_name == "FuncWorker" and node.args:
                comp = _references_compute(node.args[0])
                if comp:
                    violations.append(
                        f"{rel_posix}:{node.lineno}: FuncWorker dispatches compute callable '{comp}' on a QThread"
                    )

    return violations


def run_gate(root: Path) -> list[str]:
    violations: list[str] = []
    pkg_dir = root / "energybaseline"
    if not pkg_dir.is_dir():
        return ["energybaseline/: package directory not found"]

    for py_path in sorted(pkg_dir.rglob("*.py")):
        rel_parts = py_path.relative_to(root).parts
        if any(part in SKIP_DIRS for part in rel_parts):
            continue
        rel_posix = py_path.relative_to(root).as_posix()
        if any(rel_posix == p or rel_posix.startswith(p) for p in EXEMPT_REL_PREFIXES):
            continue
        violations.extend(scan_file(py_path, rel_posix))
    return violations


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify zero compute inside QThread / QRunnable (R-1).")
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT, help="Repository root to scan.")
    args = parser.parse_args(argv)

    root = args.root.resolve()
    violations = run_gate(root)
    if violations:
        for v in violations:
            print(f"FAIL [guard_no_qthread_compute] {v}", file=sys.stderr)
        return 1
    print(f"OK [guard_no_qthread_compute] clean ({root})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
