from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.verify_audit_coverage import verify_audit_coverage


def _write(path: Path, value: str) -> Path:
    path.write_text(value, encoding="utf-8")
    return path


def test_retained_audit_must_cover_exact_current_versions(tmp_path: Path) -> None:
    requirements = _write(tmp_path / "requirements.txt", "requests==2.32.4 \\\n     --hash=sha256:abc\n")
    audit = tmp_path / "audit.json"
    audit.write_text(json.dumps({"dependencies": [{"name": "requests", "version": "2.32.4", "vulns": []}]}))
    result = verify_audit_coverage(requirements, audit)
    assert result["status"] == "passed"
    assert result["package_count"] == 1


@pytest.mark.parametrize(
    "entry",
    [
        {"name": "requests", "version": "2.32.3", "vulns": []},
        {"name": "requests", "version": "2.32.4", "vulns": [{"id": "CVE-test"}]},
        {"name": "urllib3", "version": "2.0.0", "vulns": []},
    ],
)
def test_retained_audit_rejects_stale_missing_or_vulnerable_packages(tmp_path: Path, entry: dict[str, object]) -> None:
    requirements = _write(tmp_path / "requirements.txt", "requests==2.32.4\n")
    audit = tmp_path / "audit.json"
    audit.write_text(json.dumps({"dependencies": [entry]}))
    with pytest.raises(ValueError, match="audit coverage failed"):
        verify_audit_coverage(requirements, audit)


def test_platform_marker_supplement_closes_missing_rows_without_relaxing_vulns(tmp_path: Path) -> None:
    requirements = _write(tmp_path / "requirements.txt", "jeepney==0.9.0\n")
    audit = tmp_path / "audit.json"
    audit.write_text(json.dumps({"dependencies": []}))
    supplement = tmp_path / "supplement.json"
    supplement.write_text(
        json.dumps(
            {
                "schema_id": "clawgym.third_party_osv_marker_audit.v1",
                "packages": [{"name": "jeepney", "version": "0.9.0", "vulnerabilities": []}],
            }
        )
    )
    result = verify_audit_coverage(requirements, audit, supplement)
    assert result["status"] == "passed"
    assert result["supplemented_package_count"] == 1
