from __future__ import annotations

import io
import tarfile
from pathlib import Path

import pytest
from clawgym.contracts import sha256_digest

from clawgym_overlay.first_party_dependency import (
    DependencyProvenanceError,
    build_first_party_dependency_attestation,
    build_first_party_sbom_component,
    verify_first_party_dependency_attestation,
    write_attestation_exclusive,
    write_sbom_exclusive,
)


def _archive(path: Path, *, unsafe: str | None = None) -> None:
    with tarfile.open(path, "w:gz") as archive:
        item = tarfile.TarInfo(unsafe or "geni_lib/__init__.py")
        payload = b"# pinned source\n"
        item.size = len(payload)
        archive.addfile(item, io.BytesIO(payload))


def _kwargs(path: Path) -> dict[str, object]:
    return {
        "archive": path,
        "package_name": "geni-lib-xlab",
        "version": "1.0.0",
        "upstream_url": "https://gitlab.flux.utah.edu/emulab/geni-lib",
        "upstream_ref": "1.0.0-xlab",
        "license_id": "MPL-2.0",
        "transitive_dependencies": ["cryptography", "requests"],
    }


def test_first_party_attestation_is_deterministic_and_verifiable(tmp_path: Path) -> None:
    archive = tmp_path / "source.tar.gz"
    _archive(archive)
    first = build_first_party_dependency_attestation(**_kwargs(archive))
    second = build_first_party_dependency_attestation(**_kwargs(archive))
    assert first == second
    assert verify_first_party_dependency_attestation(first) == first["attestation_digest"]
    output = tmp_path / "attestation.json"
    write_attestation_exclusive(first, output)
    with pytest.raises(DependencyProvenanceError, match="already exists"):
        write_attestation_exclusive(first, output)
    component = build_first_party_sbom_component(first)
    assert component["properties"][0] == {"name": "clawgym:first-party", "value": "true"}
    sbom = tmp_path / "sbom.json"
    write_sbom_exclusive(first, sbom)
    assert '"bomFormat":"CycloneDX"' in sbom.read_text(encoding="utf-8")


@pytest.mark.parametrize("name", ["../escape", "/absolute", "./dot"])
def test_first_party_attestation_rejects_unsafe_archive_paths(tmp_path: Path, name: str) -> None:
    archive = tmp_path / "source.tar.gz"
    _archive(archive, unsafe=name)
    with pytest.raises(DependencyProvenanceError, match="unsafe|normalized"):
        build_first_party_dependency_attestation(**_kwargs(archive))


def test_first_party_attestation_rejects_tamper(tmp_path: Path) -> None:
    archive = tmp_path / "source.tar.gz"
    _archive(archive)
    document = build_first_party_dependency_attestation(**_kwargs(archive))
    document["upstream_ref"] = "other"
    with pytest.raises(DependencyProvenanceError, match="digest"):
        verify_first_party_dependency_attestation(document)


def test_first_party_attestation_rejects_http_source(tmp_path: Path) -> None:
    archive = tmp_path / "source.tar.gz"
    _archive(archive)
    values = _kwargs(archive)
    values["upstream_url"] = "http://example.invalid/source"
    with pytest.raises(DependencyProvenanceError, match="URL"):
        build_first_party_dependency_attestation(**values)


def test_first_party_attestation_rejects_duplicate_or_invalid_dependencies(tmp_path: Path) -> None:
    archive = tmp_path / "source.tar.gz"
    _archive(archive)
    duplicate = _kwargs(archive)
    duplicate["transitive_dependencies"] = ["requests", "requests"]
    with pytest.raises(DependencyProvenanceError, match="duplicates"):
        build_first_party_dependency_attestation(**duplicate)
    invalid = _kwargs(archive)
    invalid["transitive_dependencies"] = ["requests>=2"]
    with pytest.raises(DependencyProvenanceError, match="names"):
        build_first_party_dependency_attestation(**invalid)


def test_first_party_attestation_rejects_noncanonical_dependency_inventory(tmp_path: Path) -> None:
    archive = tmp_path / "source.tar.gz"
    _archive(archive)
    document = build_first_party_dependency_attestation(**_kwargs(archive))
    document["transitive_dependencies"] = ["requests", "cryptography"]
    document["attestation_digest"] = sha256_digest(
        {key: value for key, value in document.items() if key != "attestation_digest"}
    )
    with pytest.raises(DependencyProvenanceError, match="canonical"):
        verify_first_party_dependency_attestation(document)


def test_first_party_attestation_verifier_rejects_inventory_shape(tmp_path: Path) -> None:
    archive = tmp_path / "source.tar.gz"
    _archive(archive)
    document = build_first_party_dependency_attestation(**_kwargs(archive))
    document["source_entries"] = [{"path": "../escape", "size": 1, "sha256_digest": "0" * 64}]
    document["attestation_digest"] = "0" * 64
    with pytest.raises(DependencyProvenanceError, match="inventory|digest"):
        verify_first_party_dependency_attestation(document)


def test_first_party_attestation_verifier_rejects_extra_field(tmp_path: Path) -> None:
    archive = tmp_path / "source.tar.gz"
    _archive(archive)
    document = build_first_party_dependency_attestation(**_kwargs(archive))
    document["unexpected"] = True
    with pytest.raises(DependencyProvenanceError, match="fields"):
        verify_first_party_dependency_attestation(document)
