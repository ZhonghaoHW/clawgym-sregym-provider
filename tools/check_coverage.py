from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def _percent(covered: int, total: int) -> float:
    return 100.0 if total == 0 else covered * 100.0 / total


def _load_scope(scope_path: Path | None) -> dict[str, Any] | None:
    if scope_path is None:
        return None
    scope = json.loads(scope_path.read_text(encoding="utf-8"))
    if not isinstance(scope, dict) or scope.get("schema_id") != "agent_evolution.quality_scope.v1":
        raise ValueError("quality scope schema identity mismatch")
    return scope


def _scope_files(report: dict[str, Any], scope: dict[str, Any] | None) -> dict[str, Any]:
    """Select active files from an explicit, reviewable quality scope."""
    files = report.get("files")
    if not isinstance(files, dict):
        raise ValueError("coverage files section is invalid")
    if scope is None:
        return files
    active = scope.get("active_files")
    if not isinstance(active, list) or not all(isinstance(item, str) for item in active):
        raise ValueError("quality scope active_files must be a string array")
    declared = set(active)
    unknown = sorted(declared - {str(key) for key in files})
    if unknown:
        raise ValueError(f"quality scope names unknown coverage files: {unknown}")
    selected = {key: value for key, value in files.items() if str(key) in declared}
    if len(selected) != len(declared):
        raise ValueError("quality scope contains duplicate or missing active files")
    return selected


def _critical_files(scope: dict[str, Any] | None, supplied: list[str]) -> list[str]:
    """Use the versioned scope as the sole critical-module authority."""

    if scope is None:
        return supplied
    declared = scope.get("critical_files")
    if not isinstance(declared, list) or not all(isinstance(item, str) for item in declared):
        raise ValueError("quality scope critical_files must be a string array")
    if len(set(declared)) != len(declared):
        raise ValueError("quality scope contains duplicate critical files")
    if supplied and set(supplied) != set(declared):
        raise ValueError("--critical must exactly match quality scope critical_files")
    return declared


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Enforce owned-source line and branch coverage thresholds.")
    parser.add_argument("--json", dest="json_path", required=True, type=Path)
    parser.add_argument("--line-min", type=float, default=85.0)
    parser.add_argument("--branch-min", type=float, default=80.0)
    parser.add_argument("--critical", action="append", default=[])
    parser.add_argument("--critical-line-min", type=float, default=95.0)
    parser.add_argument("--critical-branch-min", type=float, default=95.0)
    parser.add_argument("--scope", type=Path, help="versioned quality_scope.v1 active-file manifest")
    args = parser.parse_args(argv)
    try:
        report = json.loads(args.json_path.read_text(encoding="utf-8"))
        if not isinstance(report, dict):
            raise ValueError("coverage report must be an object")
        scope = _load_scope(args.scope)
        files = _scope_files(report, scope)
        critical_files = _critical_files(scope, args.critical)
        summaries = [value["summary"] for value in files.values() if isinstance(value, dict)]
        if len(summaries) != len(files) or not all(isinstance(value, dict) for value in summaries):
            raise ValueError("coverage file summary is invalid")
        covered_lines = sum(int(value["covered_lines"]) for value in summaries)
        statements = sum(int(value["num_statements"]) for value in summaries)
        covered_branches = sum(int(value.get("covered_branches", 0)) for value in summaries)
        branches = sum(int(value.get("num_branches", 0)) for value in summaries)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        print(f"coverage report or scope is invalid: {exc}", file=sys.stderr)
        return 2
    line = _percent(covered_lines, statements)
    branch = _percent(covered_branches, branches)
    failures: list[str] = []
    if line < args.line_min:
        failures.append(f"line {line:.2f}% < {args.line_min:.2f}%")
    if branch < args.branch_min:
        failures.append(f"branch {branch:.2f}% < {args.branch_min:.2f}%")
    for critical in critical_files:
        matching = [value for key, value in files.items() if str(key).endswith(critical)]
        if len(matching) != 1 or not isinstance(matching[0], dict):
            failures.append(f"critical module missing: {critical}")
            continue
        summary = matching[0].get("summary", {})
        c_line = _percent(int(summary.get("covered_lines", 0)), int(summary.get("num_statements", 0)))
        c_branch = _percent(int(summary.get("covered_branches", 0)), int(summary.get("num_branches", 0)))
        if c_line < args.critical_line_min:
            failures.append(f"{critical} line {c_line:.2f}% < {args.critical_line_min:.2f}%")
        if c_branch < args.critical_branch_min:
            failures.append(f"{critical} branch {c_branch:.2f}% < {args.critical_branch_min:.2f}%")
    result = {
        "line_percent": round(line, 2),
        "branch_percent": round(branch, 2),
        "critical_files": critical_files,
        "status": "passed" if not failures else "blocked",
    }
    print(json.dumps(result, sort_keys=True))
    if failures:
        for failure in failures:
            print(failure, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
