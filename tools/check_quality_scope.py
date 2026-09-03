"""Validate the explicit active/frozen source inventory used by P3."""

from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path


def _module_name(path: str) -> str:
    return path.removesuffix(".py").replace("/", ".")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scope", required=True, type=Path)
    parser.add_argument("--root", required=True, type=Path)
    args = parser.parse_args(argv)
    scope = json.loads(args.scope.read_text(encoding="utf-8"))
    if scope.get("schema_id") != "agent_evolution.quality_scope.v1":
        raise SystemExit("quality scope schema identity mismatch")
    names = ("active_files", "frozen_compatibility_files", "research_only_files")
    groups = {key: set(scope.get(key, [])) for key in names}
    if any(not isinstance(scope.get(key, []), list) for key in names):
        raise SystemExit("quality scope groups must be arrays")
    if any(groups[a] & groups[b] for i, a in enumerate(names) for b in names[i + 1 :]):
        raise SystemExit("quality scope groups overlap")
    package = args.root / next(iter(groups["active_files"])).split("/", 1)[0]
    source_files = {str(path.relative_to(args.root)) for path in package.rglob("*.py")}
    declared = set().union(*groups.values())
    missing = sorted(source_files - declared)
    extra = sorted(declared - source_files)
    if missing or extra:
        raise SystemExit(f"scope inventory mismatch missing={missing} extra={extra}")
    frozen_modules = {_module_name(path) for path in groups["frozen_compatibility_files"] if path.endswith(".py")}
    violations: list[str] = []
    for path in sorted(groups["active_files"]):
        # The registry is the reviewed, lazy compatibility boundary.
        if path.endswith("/compatibility_registry.py"):
            continue
        if not path.endswith(".py"):
            continue
        tree = ast.parse((args.root / path).read_text(encoding="utf-8"), filename=path)
        for node in ast.walk(tree):
            imported = None
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imported = alias.name
                    if imported in frozen_modules or any(imported.startswith(f"{m}.") for m in frozen_modules):
                        violations.append(f"{path}:{node.lineno} imports frozen module {imported}")
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported = node.module
                if imported in frozen_modules or any(imported.startswith(f"{m}.") for m in frozen_modules):
                    violations.append(f"{path}:{node.lineno} imports frozen module {imported}")
    if violations:
        raise SystemExit("active-to-frozen imports are not allowed:\n" + "\n".join(violations))
    print(
        json.dumps(
            {
                "status": "passed",
                "active_files": len(groups["active_files"]),
                "frozen_files": len(groups["frozen_compatibility_files"]),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
