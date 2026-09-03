"""Provenance for first-party source distributions excluded from PyPI audit.

The provider ships a small, pinned ``geni-lib-xlab`` fork as a local tarball.
This module does not treat installability as a security claim: it creates a
deterministic source inventory and binds it to the declared upstream, license,
and transitive third-party dependency graph.  The resulting attestation is
safe to retain outside Git and can be checked in CI before dependency audit.
"""

from __future__ import annotations

import hashlib
import io
import os
import re
import tarfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any, cast

from clawgym.contracts import canonical_json_bytes, sha256_digest


class DependencyProvenanceError(ValueError):
    """Raised when a first-party source archive cannot be attested safely."""


_NAME = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+!-]{0,63}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")


def _safe_member_name(name: str) -> str:
    path = Path(name)
    if not name or path.is_absolute() or ".." in path.parts:
        raise DependencyProvenanceError("archive contains an unsafe path")
    normalized = path.as_posix()
    if normalized != name or normalized.startswith("./"):
        raise DependencyProvenanceError("archive path is not normalized")
    return normalized


def build_first_party_dependency_attestation(
    *,
    archive: str | Path,
    package_name: str,
    version: str,
    upstream_url: str,
    upstream_ref: str,
    license_id: str,
    transitive_dependencies: Sequence[str],
) -> dict[str, Any]:
    """Build a canonical, deterministic attestation for a source tarball."""

    source = Path(archive)
    if source.is_symlink() or not source.is_file():
        raise DependencyProvenanceError("source archive must be a regular file")
    if not _NAME.fullmatch(package_name) or not _VERSION.fullmatch(version):
        raise DependencyProvenanceError("package identity is invalid")
    # Provenance is consumed by an offline audit.  Plain HTTP would make the
    # recorded source identity downgradeable, so only HTTPS URLs are accepted.
    if not upstream_url.startswith("https://") or any(c in upstream_url for c in "\r\n"):
        raise DependencyProvenanceError("upstream URL is invalid")
    if not upstream_ref or any(c in upstream_ref for c in "\r\n"):
        raise DependencyProvenanceError("upstream ref is invalid")
    if not license_id or any(c in license_id for c in "\r\n"):
        raise DependencyProvenanceError("license identifier is invalid")
    raw_dependencies = list(transitive_dependencies)
    if len(set(raw_dependencies)) != len(raw_dependencies):
        raise DependencyProvenanceError("transitive dependency list contains duplicates")
    dependencies = sorted(raw_dependencies)
    if any(not _NAME.fullmatch(item) for item in dependencies):
        raise DependencyProvenanceError("transitive dependency names are invalid")

    raw = source.read_bytes()
    entries: list[dict[str, Any]] = []
    names: set[str] = set()
    try:
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:*") as archive_file:
            for member in archive_file.getmembers():
                name = _safe_member_name(member.name)
                if name in names:
                    raise DependencyProvenanceError("archive contains duplicate paths")
                names.add(name)
                if member.isdir():
                    continue
                if not member.isreg():
                    raise DependencyProvenanceError("archive may contain regular files only")
                handle = archive_file.extractfile(member)
                if handle is None:
                    raise DependencyProvenanceError("archive member is unreadable")
                payload = handle.read()
                entries.append(
                    {"path": name, "size": len(payload), "sha256_digest": hashlib.sha256(payload).hexdigest()}
                )
    except (OSError, tarfile.TarError) as exc:
        raise DependencyProvenanceError("source archive is unreadable") from exc
    if not entries:
        raise DependencyProvenanceError("source archive is empty")
    entries.sort(key=lambda item: item["path"])
    inventory_digest = sha256_digest(entries)
    value: dict[str, Any] = {
        "schema_id": "clawgym.first_party_dependency_attestation.v1",
        "package_name": package_name,
        "version": version,
        "first_party": True,
        "archive_sha256_digest": hashlib.sha256(raw).hexdigest(),
        "source_inventory_digest": inventory_digest,
        "source_entries": entries,
        "upstream_url": upstream_url,
        "upstream_ref": upstream_ref,
        "license_id": license_id,
        "transitive_dependencies": dependencies,
    }
    value["attestation_digest"] = sha256_digest(value)
    return value


def verify_first_party_dependency_attestation(document: dict[str, Any]) -> str:
    """Verify a previously emitted attestation without reading external state."""

    if (
        document.get("schema_id") != "clawgym.first_party_dependency_attestation.v1"
        or document.get("first_party") is not True
    ):
        raise DependencyProvenanceError("attestation identity is invalid")
    expected_fields = {
        "schema_id",
        "package_name",
        "version",
        "first_party",
        "archive_sha256_digest",
        "source_inventory_digest",
        "source_entries",
        "upstream_url",
        "upstream_ref",
        "license_id",
        "transitive_dependencies",
        "attestation_digest",
    }
    if set(document) != expected_fields:
        raise DependencyProvenanceError("attestation fields are incomplete")
    digest = document.get("attestation_digest")
    if (
        not isinstance(digest, str)
        or sha256_digest({key: value for key, value in document.items() if key != "attestation_digest"}) != digest
    ):
        raise DependencyProvenanceError("attestation digest mismatch")
    if not _NAME.fullmatch(str(document["package_name"])) or not _VERSION.fullmatch(str(document["version"])):
        raise DependencyProvenanceError("package identity is invalid")
    for field in ("archive_sha256_digest", "source_inventory_digest", "attestation_digest"):
        if not isinstance(document.get(field), str) or not _DIGEST.fullmatch(document[field]):
            raise DependencyProvenanceError(f"{field} is invalid")
    if not isinstance(document.get("upstream_url"), str) or not document["upstream_url"].startswith("https://"):
        raise DependencyProvenanceError("upstream URL is invalid")
    dependencies_value = document.get("transitive_dependencies")
    if not isinstance(dependencies_value, list):
        raise DependencyProvenanceError("transitive dependency list is invalid")
    raw_dependencies = cast(list[Any], dependencies_value)
    if any(not isinstance(item, str) for item in raw_dependencies):
        raise DependencyProvenanceError("transitive dependency list is invalid")
    dependencies = cast(list[str], raw_dependencies)
    if (
        dependencies != sorted(dependencies)
        or len(set(dependencies)) != len(dependencies)
        or any(not _NAME.fullmatch(item) for item in dependencies)
    ):
        raise DependencyProvenanceError("transitive dependency list is not canonical")
    entries_value = document.get("source_entries")
    if not isinstance(entries_value, list) or not entries_value:
        raise DependencyProvenanceError("source inventory is missing")
    entries = cast(list[Any], entries_value)
    normalized_entries: list[dict[str, Any]] = []
    names: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict) or set(cast(dict[str, Any], entry)) != {"path", "size", "sha256_digest"}:
            raise DependencyProvenanceError("source inventory entry is invalid")
        typed_entry = cast(dict[str, Any], entry)
        name = typed_entry.get("path")
        size = typed_entry.get("size")
        digest_value = typed_entry.get("sha256_digest")
        if not isinstance(name, str) or name in names:
            raise DependencyProvenanceError("source inventory path is invalid")
        _safe_member_name(name)
        if (
            type(size) is not int
            or size < 0
            or not isinstance(digest_value, str)
            or not _DIGEST.fullmatch(digest_value)
        ):
            raise DependencyProvenanceError("source inventory entry is invalid")
        names.add(name)
        normalized_entries.append({"path": name, "size": size, "sha256_digest": digest_value})
    if normalized_entries != sorted(normalized_entries, key=lambda item: item["path"]):
        raise DependencyProvenanceError("source inventory is not canonical")
    if sha256_digest(normalized_entries) != document.get("source_inventory_digest"):
        raise DependencyProvenanceError("source inventory digest mismatch")
    return digest


def write_attestation_exclusive(document: dict[str, Any], output: str | Path) -> Path:
    """Write one canonical 0600 attestation and never overwrite it."""

    verify_first_party_dependency_attestation(document)
    path = Path(output)
    if path.exists() or path.is_symlink():
        raise DependencyProvenanceError("attestation output already exists")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(canonical_json_bytes(document))
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return path


def build_first_party_sbom_component(document: dict[str, Any]) -> dict[str, Any]:
    """Build a deterministic CycloneDX component for the attested source."""

    verify_first_party_dependency_attestation(document)
    return {
        "type": "library",
        "bom-ref": f"first-party:{document['package_name']}@{document['version']}",
        "name": document["package_name"],
        "version": document["version"],
        "scope": "required",
        "licenses": [{"license": {"id": document["license_id"]}}],
        "properties": [
            {"name": "clawgym:first-party", "value": "true"},
            {"name": "clawgym:archive-sha256", "value": document["archive_sha256_digest"]},
            {"name": "clawgym:source-inventory-sha256", "value": document["source_inventory_digest"]},
            {"name": "clawgym:upstream-ref", "value": document["upstream_ref"]},
        ],
        "externalReferences": [{"type": "vcs", "url": document["upstream_url"]}],
    }


def write_sbom_exclusive(document: dict[str, Any], output: str | Path) -> Path:
    """Write a minimal deterministic CycloneDX SBOM containing one component."""

    component = build_first_party_sbom_component(document)
    sbom = {"bomFormat": "CycloneDX", "specVersion": "1.5", "version": 1, "components": [component]}
    path = Path(output)
    if path.exists() or path.is_symlink():
        raise DependencyProvenanceError("SBOM output already exists")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(canonical_json_bytes(sbom))
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return path
