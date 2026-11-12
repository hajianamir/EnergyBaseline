"""AST gate ensuring only ``energybaseline/config/settings.py`` reads database URL env vars.

Protected environment variables:
``DATABASE_URL``, ``POSTGRES_URL``, ``DB_CONNECTION_STRING``, ``DB_URI``, ``EB_DATABASE_URL``.

Only ``energybaseline/config/settings.py`` may read these variables (via ``os.environ[...]``,
``os.environ.get(...)``, ``os.environ.setdefault(...)``, ``os.getenv(...)``, or membership checks).

Documented exemption:
``docs/EXECUTION_PLAN.md``, ``docs/DECISIONS.md``, ``docs/PHASE0_BRIEF.md``,
``docs/PROMPT_PHASE0_EXECUTOR.md`` and ``artifacts/**`` only quote variable names in prose/audit
evidence and are not scanned.
"""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

DEFAULT_ROOT = Path(__file__).resolve().parents[2]
ALLOWED_READER = "energybaseline/config/settings.py"
PROTECTED_ENV_VARS = frozenset(
    {
        "DATABASE_URL",
        "POSTGRES_URL",
        "DB_CONNECTION_STRING",
        "DB_URI",
        "EB_DATABASE_URL",
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
    ALLOWED_READER,
    "scripts/gates/guard_env_readers.py",
    "tests/gates/",
)


def _is_exempt(rel_posix: str) -> bool:
    return any(rel_posix == p or rel_posix.startswith(p) for p in EXEMPT_REL_PREFIXES)


def _is_os_environ(node: ast.AST) -> bool:
    if isinstance(node, ast.Name) and node.id == "environ":
        return True
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "environ"
        and isinstance(node.value, ast.Name)
        and node.value.id == "os"
    )


def _extract_protected_strings(node: ast.AST) -> set[str]:
    found: set[str] = set()
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        if node.value in PROTECTED_ENV_VARS:
            found.add(node.value)
    elif isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        for elt in node.elts:
            found |= _extract_protected_strings(elt)
    return found


def _collect_tainted_names(tree: ast.AST) -> dict[str, set[str]]:
    tainted: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            vals = _extract_protected_strings(node.value)
            if vals:
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        tainted.setdefault(target.id, set()).update(vals)
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            vals = _extract_protected_strings(node.value)
            if vals and isinstance(node.target, ast.Name):
                tainted.setdefault(node.target.id, set()).update(vals)
        elif isinstance(node, ast.For):
            vals = _extract_protected_strings(node.iter)
            if not vals and isinstance(node.iter, ast.Name):
                vals = set(tainted.get(node.iter.id, ()))
            if vals and isinstance(node.target, ast.Name):
                tainted.setdefault(node.target.id, set()).update(vals)
    return tainted


def _resolve_env_arg(node: ast.AST, tainted: dict[str, set[str]]) -> set[str]:
    direct = _extract_protected_strings(node)
    if direct:
        return direct
    if isinstance(node, ast.Name) and node.id in tainted:
        return set(tainted[node.id])
    return set()


def scan_file(py_path: Path, rel_posix: str) -> list[str]:
    try:
        source = py_path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=rel_posix)
    except SyntaxError as exc:
        return [f"{rel_posix}:{exc.lineno}: syntax error while scanning AST: {exc.msg}"]
    except UnicodeDecodeError as exc:
        return [f"{rel_posix}:1: utf-8 decode error: {exc}"]

    tainted = _collect_tainted_names(tree)
    violations: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Load) and _is_os_environ(node.value):
            keys = _resolve_env_arg(node.slice, tainted)
            for k in sorted(keys):
                violations.append(f"{rel_posix}:{node.lineno}: unauthorized read of os.environ['{k}']")
        elif isinstance(node, ast.Call):
            func = node.func
            is_getenv = (isinstance(func, ast.Name) and func.id == "getenv") or (
                isinstance(func, ast.Attribute)
                and func.attr == "getenv"
                and isinstance(func.value, ast.Name)
                and func.value.id == "os"
            )
            is_env_method = (
                isinstance(func, ast.Attribute) and func.attr in {"get", "setdefault"} and _is_os_environ(func.value)
            )
            if (is_getenv or is_env_method) and node.args:
                keys = _resolve_env_arg(node.args[0], tainted)
                for k in sorted(keys):
                    violations.append(f"{rel_posix}:{node.lineno}: unauthorized read of '{k}' via env accessor")
        elif isinstance(node, ast.Compare):
            for op, comp in zip(node.ops, node.comparators):
                if isinstance(op, (ast.In, ast.NotIn)) and _is_os_environ(comp):
                    keys = _resolve_env_arg(node.left, tainted)
                    for k in sorted(keys):
                        violations.append(
                            f"{rel_posix}:{node.lineno}: unauthorized membership check of '{k}' in os.environ"
                        )

    return violations


def run_gate(root: Path) -> list[str]:
    violations: list[str] = []
    allowed = root / ALLOWED_READER
    if not allowed.is_file():
        violations.append(f"{ALLOWED_READER}: expected canonical env reader does not exist")

    for py_path in sorted(root.rglob("*.py")):
        rel_parts = py_path.relative_to(root).parts
        if any(part in SKIP_DIRS for part in rel_parts):
            continue
        rel_posix = py_path.relative_to(root).as_posix()
        if _is_exempt(rel_posix):
            continue
        violations.extend(scan_file(py_path, rel_posix))
    return violations


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify only config/settings.py reads DB environment variables.")
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT, help="Repository root to scan.")
    args = parser.parse_args(argv)

    root = args.root.resolve()
    violations = run_gate(root)
    if violations:
        for v in violations:
            print(f"FAIL [guard_env_readers] {v}", file=sys.stderr)
        return 1
    print(f"OK [guard_env_readers] clean ({root})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
