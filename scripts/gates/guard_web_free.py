"""AST, manifest, and tracked-file gate enforcing the offline, Python-only, web-free architecture.

Checks performed:
1. AST scan of every ``*.py`` file for banned imports (``weasy``+``print``,
   ``PySide6.QtWebEngineWidgets``, ``PySide6.QtWebEngineCore``, ``flask``, ``fastapi``,
   ``uvicorn``, ``http.server``).
2. Dependency manifest scan (``requirements.in``, ``requirements.txt``,
   ``requirements-dev.txt``, ``requirements.lock``, ``pyproject.toml``) for banned
   web/GTK packages.
3. Rejection of web-stack configuration and source files (``package.json``,
   ``package-lock.json``, ``tsconfig.json``, ``next.config.*``, ``next-env.d.ts``,
   ``postcss.config.*``, ``eslint.config.*``, ``drizzle.config.*``, ``*.ts``, ``*.tsx``,
   ``*.jsx``) in ``git ls-files`` and the target root.

Documented exemption:
``docs/EXECUTION_PLAN.md``, ``docs/DECISIONS.md``, ``docs/PHASE0_BRIEF.md``,
``docs/PROMPT_PHASE0_EXECUTOR.md`` and ``artifacts/**`` (as well as process markdown/YAML
documentation under ``docs/``) only quote banned words for audit/governance records and are
not scanned as executable code or dependency manifests.
"""

from __future__ import annotations

import argparse
import ast
import re
import subprocess
import sys
from pathlib import Path

DEFAULT_ROOT = Path(__file__).resolve().parents[2]
_LEGACY_HTML_PDF = "weasy" + "print"

BANNED_MODULES = frozenset(
    {
        _LEGACY_HTML_PDF,
        "PySide6.QtWebEngineWidgets",
        "PySide6.QtWebEngineCore",
        "flask",
        "fastapi",
        "uvicorn",
        "http.server",
    }
)

MANIFEST_NAMES = (
    "requirements.in",
    "requirements.txt",
    "requirements-dev.txt",
    "requirements.lock",
    "pyproject.toml",
    "setup.py",
    "setup.cfg",
)

BANNED_MANIFEST_PKGS = frozenset(
    {
        _LEGACY_HTML_PDF,
        "flask",
        "fastapi",
        "uvicorn",
        "pyside6-webengine",
        "django",
    }
)

WEB_FILE_RE = re.compile(
    r"(^|/)(package(-lock)?\.json|yarn\.lock|pnpm-lock\.yaml|tsconfig\.json|"
    r"next\.config\.[A-Za-z0-9]+|next-env\.d\.ts|postcss\.config\.[A-Za-z0-9]+|"
    r"eslint\.config\.[A-Za-z0-9]+|drizzle\.config\.[A-Za-z0-9]+)$|"
    r"\.(tsx?|jsx)$",
    re.IGNORECASE,
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
        "node_modules",
        "artifacts",
    }
)

EXEMPT_REL_PREFIXES = (
    "docs/EXECUTION_PLAN.md",
    "docs/DECISIONS.md",
    "docs/PHASE0_BRIEF.md",
    "docs/PROMPT_PHASE0_EXECUTOR.md",
    "artifacts/",
    "scripts/gates/guard_web_free.py",
    "tests/gates/",
)


def _is_exempt(rel_posix: str) -> bool:
    return any(rel_posix == p or rel_posix.startswith(p) for p in EXEMPT_REL_PREFIXES)


def _matches_banned_module(mod_name: str) -> str | None:
    for banned in BANNED_MODULES:
        if mod_name == banned or mod_name.startswith(banned + "."):
            return banned
    return None


def scan_python_ast(py_path: Path, rel_posix: str) -> list[str]:
    violations: list[str] = []
    try:
        source = py_path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=rel_posix)
    except SyntaxError as exc:
        return [f"{rel_posix}:{exc.lineno}: syntax error while scanning AST: {exc.msg}"]
    except UnicodeDecodeError as exc:
        return [f"{rel_posix}:1: utf-8 decode error: {exc}"]

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                hit = _matches_banned_module(alias.name)
                if hit:
                    violations.append(f"{rel_posix}:{node.lineno}: banned import '{alias.name}' (matches '{hit}')")
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            hit = _matches_banned_module(base)
            if hit:
                violations.append(f"{rel_posix}:{node.lineno}: banned import from '{base}' (matches '{hit}')")
                continue
            for alias in node.names:
                combined = f"{base}.{alias.name}" if base else alias.name
                sub_hit = _matches_banned_module(combined)
                if sub_hit:
                    violations.append(f"{rel_posix}:{node.lineno}: banned import '{combined}' (matches '{sub_hit}')")
        elif isinstance(node, ast.Call):
            func = node.func
            is_dyn_import = (isinstance(func, ast.Name) and func.id == "__import__") or (
                isinstance(func, ast.Attribute)
                and func.attr == "import_module"
                and isinstance(func.value, ast.Name)
                and func.value.id == "importlib"
            )
            if (
                is_dyn_import
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            ):
                hit = _matches_banned_module(node.args[0].value)
                if hit:
                    violations.append(
                        f"{rel_posix}:{node.lineno}: banned dynamic import '{node.args[0].value}' (matches '{hit}')"
                    )
    return violations


def scan_manifests(root: Path) -> list[str]:
    violations: list[str] = []
    pkg_re = re.compile(r"^\s*([A-Za-z0-9_.-]+)")
    for name in MANIFEST_NAMES:
        p = root / name
        if not p.is_file():
            continue
        for lineno, raw in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            stripped = raw.strip()
            if not stripped or stripped.startswith("#") or stripped.startswith("--hash="):
                continue
            lowered = stripped.lower()
            for banned in BANNED_MANIFEST_PKGS:
                if name == "pyproject.toml":
                    if re.search(rf'["\']{re.escape(banned)}([<>=!~;\s"\']|$)', lowered):
                        violations.append(f"{name}:{lineno}: banned package '{banned}' in manifest")
                else:
                    m = pkg_re.match(lowered)
                    if m and m.group(1).replace("_", "-") == banned.replace("_", "-"):
                        violations.append(f"{name}:{lineno}: banned package '{banned}' in manifest")
    return violations


def scan_web_files(root: Path) -> list[str]:
    violations: list[str] = []
    seen: set[str] = set()

    if (root / ".git").exists():
        proc = subprocess.run(
            ["git", "ls-files"],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode == 0:
            for line in proc.stdout.splitlines():
                rel = line.strip().replace("\\", "/")
                if not rel or _is_exempt(rel):
                    continue
                if WEB_FILE_RE.search(rel):
                    seen.add(rel)
                    violations.append(f"{rel}: banned web-stack file tracked in git")

    for path in root.rglob("*"):
        if any(part in SKIP_DIRS for part in path.relative_to(root).parts):
            continue
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if rel in seen or _is_exempt(rel) or rel.startswith("docs/"):
            continue
        if WEB_FILE_RE.search(rel):
            violations.append(f"{rel}: banned web-stack file present in workspace")

    return violations


def run_gate(root: Path) -> list[str]:
    violations: list[str] = []
    for py_path in sorted(root.rglob("*.py")):
        rel_parts = py_path.relative_to(root).parts
        if any(part in SKIP_DIRS for part in rel_parts):
            continue
        rel_posix = py_path.relative_to(root).as_posix()
        if _is_exempt(rel_posix):
            continue
        violations.extend(scan_python_ast(py_path, rel_posix))

    violations.extend(scan_manifests(root))
    violations.extend(scan_web_files(root))
    return violations


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify repository is free of banned web/GTK dependencies.")
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT, help="Repository root to scan.")
    args = parser.parse_args(argv)

    root = args.root.resolve()
    violations = run_gate(root)
    if violations:
        for v in violations:
            print(f"FAIL [guard_web_free] {v}", file=sys.stderr)
        return 1
    print(f"OK [guard_web_free] clean ({root})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
