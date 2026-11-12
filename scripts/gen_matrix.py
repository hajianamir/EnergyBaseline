"""Generate and validate ``docs/requirements_matrix.md`` from ``docs/requirements.yaml`` (D0.5).

Enforced invariants (``--check`` and generation):
1. Every mandatory requirement ID across all specification families is present with no duplicates:
   AC-01..AC-35, DB-1..11, SHL-1..9, IPC-1..7, LIF-1..5, SCR-1..7, DAT-1..8, VSG-1..9,
   AUTO-1..6, TCP-1..14, T1..T14, TC-1..8, CI-1..4, F1..F10, VAL-1..7, SC-1..10, UQ-1..5,
   PM-1..7, DR-1..5, MT-1..5, PER-1..6, REP-1..5, RPT-1..7, UX-1..12, DOM-1..3, PKG-1..6,
   and D0.1..D0.10.
2. Every row has ``id``, ``priority`` in ``{M, S, O}``, non-empty ``phase``, ``code_refs`` list,
   ``test_ids`` list, and ``status`` in ``{planned, in_progress, done, deferred}``.
3. No ``AC-*`` row lacks a ``phase``.
4. No row with ``status: done`` has an empty ``test_ids`` list, and when validating against the
   repository root, every ``done`` row's ``code_refs`` and ``test_ids`` resolve to real files/tests.
5. In ``--check`` mode, ``docs/requirements_matrix.md`` must exist, carry the
   ``generated — do not edit`` header, and match the generated output byte-for-byte.
"""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

import yaml

DEFAULT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_YAML = "docs/requirements.yaml"
DEFAULT_MD = "docs/requirements_matrix.md"

VALID_PRIORITIES = frozenset({"M", "S", "O"})
VALID_STATUSES = frozenset({"planned", "in_progress", "done", "deferred"})


def expected_mandatory_ids() -> list[str]:
    ids: list[str] = []
    ids.extend(f"D0.{i}" for i in range(1, 11))
    ids.extend(f"AC-{i:02d}" for i in range(1, 36))
    families = [
        ("DB-", 1, 11),
        ("SHL-", 1, 9),
        ("IPC-", 1, 7),
        ("LIF-", 1, 5),
        ("SCR-", 1, 7),
        ("DAT-", 1, 8),
        ("VSG-", 1, 9),
        ("AUTO-", 1, 6),
        ("TCP-", 1, 14),
        ("T", 1, 14),
        ("TC-", 1, 8),
        ("CI-", 1, 4),
        ("F", 1, 10),
        ("VAL-", 1, 7),
        ("SC-", 1, 10),
        ("UQ-", 1, 5),
        ("PM-", 1, 7),
        ("DR-", 1, 5),
        ("MT-", 1, 5),
        ("PER-", 1, 6),
        ("REP-", 1, 5),
        ("RPT-", 1, 7),
        ("UX-", 1, 12),
        ("DOM-", 1, 3),
        ("PKG-", 1, 6),
    ]
    for prefix, start, end in families:
        ids.extend(f"{prefix}{i}" for i in range(start, end + 1))
    return ids


def _test_exists(root: Path, node_id: str) -> bool:
    parts = node_id.split("::")
    rel_file = parts[0].strip()
    fpath = root / rel_file
    if not fpath.is_file():
        return False
    if len(parts) < 2:
        return False
    func_name = parts[-1].split("[", 1)[0].strip()
    try:
        tree = ast.parse(fpath.read_text(encoding="utf-8"), filename=rel_file)
    except SyntaxError:
        return False
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == func_name:
            return True
    return False


def validate_rows(rows: list[dict], root: Path | None = None) -> list[str]:
    errors: list[str] = []
    if not isinstance(rows, list) or not rows:
        return ["requirements.yaml must contain a non-empty 'requirements' list"]

    seen: set[str] = set()
    for idx, row in enumerate(rows):
        if not isinstance(row, dict):
            errors.append(f"row #{idx}: entry must be a mapping")
            continue
        rid = str(row.get("id", "")).strip()
        if not rid:
            errors.append(f"row #{idx}: missing 'id'")
            continue
        if rid in seen:
            errors.append(f"{rid}: duplicate requirement ID")
        seen.add(rid)

        prio = row.get("priority")
        if prio not in VALID_PRIORITIES:
            errors.append(f"{rid}: invalid priority '{prio}' (expected M, S, or O)")

        phase = row.get("phase")
        phase_str = "" if phase is None else str(phase).strip()
        if not phase_str:
            if rid.startswith("AC-"):
                errors.append(f"{rid}: AC requirement is missing mandatory 'phase'")
            else:
                errors.append(f"{rid}: missing 'phase'")

        status = row.get("status")
        if status not in VALID_STATUSES:
            errors.append(f"{rid}: invalid status '{status}' (expected {sorted(VALID_STATUSES)})")

        code_refs = row.get("code_refs")
        if not isinstance(code_refs, list):
            errors.append(f"{rid}: 'code_refs' must be a list")
            code_refs = []

        test_ids = row.get("test_ids")
        if not isinstance(test_ids, list):
            errors.append(f"{rid}: 'test_ids' must be a list")
            test_ids = []

        if status == "done" and len(test_ids) == 0:
            errors.append(f"{rid}: status is 'done' with empty 'test_ids' (forbidden)")

        if status == "done" and root is not None:
            for cref in code_refs:
                cpath = root / str(cref).split("::", 1)[0]
                if not cpath.exists():
                    errors.append(f"{rid}: code_ref '{cref}' does not exist under {root}")
            for tid in test_ids:
                if not _test_exists(root, str(tid)):
                    errors.append(f"{rid}: test_id '{tid}' does not resolve to a real test function")

    mandatory = expected_mandatory_ids()
    missing = [m for m in mandatory if m not in seen]
    if missing:
        errors.append(f"missing {len(missing)} mandatory IDs: {', '.join(missing[:15])}")

    return errors


def _fmt_list(items: list[str]) -> str:
    if not items:
        return "—"
    return ", ".join(f"`{x}`" for x in items)


def render_markdown(rows: list[dict]) -> str:
    counts = {s: sum(1 for r in rows if r["status"] == s) for s in ("done", "in_progress", "planned", "deferred")}
    prio_counts = {p: sum(1 for r in rows if r["priority"] == p) for p in ("M", "S", "O")}

    lines = [
        "<!-- generated — do not edit (source: docs/requirements.yaml; regenerate with: python scripts/gen_matrix.py) -->",
        "# EnergyBaseline v6 — Requirements Traceability Matrix",
        "",
        "> **generated — do not edit** — single source of truth: `docs/requirements.yaml`. Run `python scripts/gen_matrix.py` to update and `python scripts/gen_matrix.py --check` to validate.",
        "",
        "## Summary",
        "",
        f"- **Total tracked IDs:** {len(rows)} (Priorities: `[M]`={prio_counts['M']}, `[S]`={prio_counts['S']}, `[O]`={prio_counts['O']})",
        f"- **Status breakdown:** `done`={counts['done']} · `in_progress`={counts['in_progress']} · `planned`={counts['planned']} · `deferred`={counts['deferred']}",
        "",
        "## Matrix",
        "",
        "| ID | Priority | Phase | Status | Description | Code Refs | Test IDs |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        desc = str(r.get("title", "")).replace("|", "\\|").strip()
        lines.append(
            f"| `{r['id']}` | `{r['priority']}` | `{r['phase']}` | `{r['status']}` | {desc} | "
            f"{_fmt_list(r['code_refs'])} | {_fmt_list(r['test_ids'])} |"
        )
    lines.append("")
    return "\n".join(lines)


def load_yaml(yaml_path: Path) -> list[dict]:
    data = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    if isinstance(data, dict) and "requirements" in data:
        return data["requirements"]
    if isinstance(data, list):
        return data
    raise ValueError(f"{yaml_path}: expected top-level 'requirements' key or list")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate or validate docs/requirements_matrix.md.")
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT, help="Repository root.")
    parser.add_argument("--yaml", type=Path, default=None, help="Path to requirements.yaml.")
    parser.add_argument("--out", type=Path, default=None, help="Path to output markdown matrix.")
    parser.add_argument("--check", action="store_true", help="Validate YAML and verify markdown is up to date.")
    parser.add_argument(
        "--skip-fs-refs", action="store_true", help="Skip filesystem existence check on code_refs/test_ids."
    )
    args = parser.parse_args(argv)

    root = args.root.resolve()
    yaml_path = (args.yaml or (root / DEFAULT_YAML)).resolve()
    out_path = (args.out or (root / DEFAULT_MD)).resolve()

    if not yaml_path.is_file():
        print(f"FAIL [gen_matrix] missing YAML source: {yaml_path}", file=sys.stderr)
        return 1

    try:
        rows = load_yaml(yaml_path)
    except Exception as exc:
        print(f"FAIL [gen_matrix] invalid YAML: {exc}", file=sys.stderr)
        return 1

    errors = validate_rows(rows, None if args.skip_fs_refs else root)
    if errors:
        for err in errors:
            print(f"FAIL [gen_matrix] {err}", file=sys.stderr)
        return 1

    rendered = render_markdown(rows)
    if args.check:
        if not out_path.is_file():
            print(f"FAIL [gen_matrix] missing generated markdown: {out_path}", file=sys.stderr)
            return 1
        actual = out_path.read_text(encoding="utf-8")
        if "generated — do not edit" not in actual.splitlines()[0]:
            print(f"FAIL [gen_matrix] {out_path} lacks 'generated — do not edit' header", file=sys.stderr)
            return 1
        if actual != rendered:
            print(
                f"FAIL [gen_matrix] {out_path} is out of date with {yaml_path}; run python scripts/gen_matrix.py",
                file=sys.stderr,
            )
            return 1
        print(
            f"OK [gen_matrix] verified {len(rows)} requirement IDs ({sum(1 for r in rows if r['status'] == 'done')} done)"
        )
        return 0

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(rendered, encoding="utf-8")
    print(f"Wrote {out_path} ({len(rows)} requirement IDs)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
