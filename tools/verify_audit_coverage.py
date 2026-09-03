"""Verify that a retained pip-audit report covers the current locked graph.

This is an offline freshness/coverage check, not a replacement for querying a
vulnerability service.  It is useful when the advisory service is temporarily
unreachable: every current third-party name/version must be present in a
retained report and every covered package must have an empty vulnerability
list.  First-party packages must already have been removed by the explicit
requirements filter and are checked through their source attestation instead.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any, cast

_REQUIREMENT = re.compile(r"^([A-Za-z0-9_.-]+)==([^\s\\]+)")


def _requirements(path: Path) -> dict[str, str]:
    packages: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        match = _REQUIREMENT.match(raw.strip())
        if match:
            name = match.group(1).lower().replace("_", "-")
            version = match.group(2)
            if name in packages and packages[name] != version:
                raise ValueError(f"duplicate requirement with conflicting version: {name}")
            packages[name] = version
    if not packages:
        raise ValueError("requirements contain no exact pins")
    return packages


def verify_audit_coverage(
    requirements_path: Path,
    audit_path: Path,
    supplement_path: Path | None = None,
) -> dict[str, Any]:
    requirements = _requirements(requirements_path)
    document = json.loads(audit_path.read_text(encoding="utf-8"))
    if not isinstance(document, dict) or not isinstance(document.get("dependencies"), list):
        raise ValueError("pip-audit report dependencies are invalid")
    covered: dict[str, tuple[str, list[Any]]] = {}
    for raw in cast(list[Any], document["dependencies"]):
        if not isinstance(raw, dict) or not isinstance(raw.get("name"), str) or not isinstance(raw.get("version"), str):
            raise ValueError("pip-audit dependency entry is invalid")
        name = raw["name"].lower().replace("_", "-")
        vulns = raw.get("vulns", [])
        if not isinstance(vulns, list):
            raise ValueError(f"pip-audit vulnerabilities are invalid: {name}")
        if name in covered:
            raise ValueError(f"pip-audit report contains duplicate package: {name}")
        covered[name] = (raw["version"], vulns)
    supplemented = 0
    if supplement_path is not None:
        supplement = json.loads(supplement_path.read_text(encoding="utf-8"))
        if not isinstance(supplement, dict) or supplement.get("schema_id") != "clawgym.third_party_osv_marker_audit.v1":
            raise ValueError("supplement schema identity is invalid")
        rows = supplement.get("packages")
        if not isinstance(rows, list):
            raise ValueError("supplement package inventory is invalid")
        for raw in cast(list[Any], rows):
            if (
                not isinstance(raw, dict)
                or not isinstance(raw.get("name"), str)
                or not isinstance(raw.get("version"), str)
            ):
                raise ValueError("supplement package entry is invalid")
            name = raw["name"].lower().replace("_", "-")
            vulnerabilities = raw.get("vulnerabilities")
            if not isinstance(vulnerabilities, list) or name in covered:
                raise ValueError(f"supplement package entry is invalid or duplicated: {name}")
            covered[name] = (raw["version"], vulnerabilities)
            supplemented += 1
    missing = sorted(set(requirements) - set(covered))
    mismatched = sorted(name for name in requirements if name in covered and requirements[name] != covered[name][0])
    vulnerable = sorted(name for name in requirements if name in covered and covered[name][1])
    if missing or mismatched or vulnerable:
        raise ValueError(f"audit coverage failed: missing={missing}, mismatched={mismatched}, vulnerable={vulnerable}")
    return {
        "schema_id": "clawgym.third_party_audit_coverage.v1",
        "requirements_sha256": hashlib.sha256(requirements_path.read_bytes()).hexdigest(),
        "audit_sha256": hashlib.sha256(audit_path.read_bytes()).hexdigest(),
        "supplement_sha256": hashlib.sha256(supplement_path.read_bytes()).hexdigest()
        if supplement_path is not None
        else None,
        "supplemented_package_count": supplemented,
        "package_count": len(requirements),
        "status": "passed",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify retained pip-audit coverage of exact requirements")
    parser.add_argument("--requirements", required=True, type=Path)
    parser.add_argument("--audit", required=True, type=Path)
    parser.add_argument("--supplement", type=Path)
    args = parser.parse_args()
    result = verify_audit_coverage(args.requirements, args.audit, args.supplement)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
