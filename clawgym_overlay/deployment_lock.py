"""Strict immutable dependency lock for the WP4 live execution slice."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Final, cast

from clawgym.contracts import sha256_digest
from clawgym.contracts.validation import ContractValidationError, reject_forbidden_content

LOCK_SCHEMA_ID: Final = "clawgym.sregym_deployment_lock.v1"
REQUIRED_ARTIFACTS: Final = frozenset(
    {
        "docker-engine",
        "kind",
        "kubectl",
        "helm",
        "uv",
        "kind-node",
        "calico-manifest",
        "metrics-server-manifest",
        "openebs-manifest",
        "prometheus-chart",
        "loki-chart",
        "promtail-chart",
        "runtime-image.mcp-server",
        "runtime-image.workload",
        "runtime-image.probe",
        "kind-bundled-image.coredns",
        "kind-bundled-image.etcd",
        "kind-bundled-image.kube-apiserver",
        "kind-bundled-image.kube-controller-manager",
        "kind-bundled-image.kube-scheduler",
        "kind-bundled-image.local-path-provisioner",
    }
)
ARTIFACT_KINDS: Final = frozenset({"package", "binary", "manifest", "image", "chart"})
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+:-]*$")


def validate_deployment_lock(document: Mapping[str, Any]) -> None:
    """Reject incomplete or mutable infrastructure identities."""

    if set(document) != {"schema_id", "platform", "artifacts"}:
        raise ContractValidationError("deployment lock has unexpected or missing fields")
    if document["schema_id"] != LOCK_SCHEMA_ID:
        raise ContractValidationError("deployment lock schema_id is invalid")
    if document["platform"] != "linux-amd64":
        raise ContractValidationError("deployment lock supports only linux-amd64")
    reject_forbidden_content(document)
    artifacts_value = document["artifacts"]
    if not isinstance(artifacts_value, list) or not artifacts_value:
        raise ContractValidationError("deployment lock artifacts must be non-empty")
    artifacts = cast(list[Any], artifacts_value)
    names: set[str] = set()
    runtime_images = 0
    for index, artifact in enumerate(artifacts):
        location = f"artifacts[{index}]"
        if not isinstance(artifact, Mapping):
            raise ContractValidationError(f"{location} has invalid fields")
        artifact = cast(Mapping[str, Any], artifact)
        name = artifact.get("name")
        expected_fields = {
            "name",
            "kind",
            "version",
            "source",
            "integrity",
            "target",
        }
        if isinstance(name, str) and name.startswith("runtime-image."):
            expected_fields.add("platform_integrity")
        if set(artifact) != expected_fields:
            raise ContractValidationError(f"{location} has invalid fields")
        kind = artifact["kind"]
        version = artifact["version"]
        source = artifact["source"]
        integrity = artifact["integrity"]
        target = artifact["target"]
        if not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9._-]+", name):
            raise ContractValidationError(f"{location}.name is invalid")
        if name in names:
            raise ContractValidationError(f"{location}.name is duplicated")
        names.add(name)
        if kind not in ARTIFACT_KINDS:
            raise ContractValidationError(f"{location}.kind is invalid")
        if not isinstance(version, str) or not _VERSION.fullmatch(version):
            raise ContractValidationError(f"{location}.version is invalid")
        if version.lower() == "latest" or "latest" in version.lower().split("-"):
            raise ContractValidationError(f"{location}.version must be immutable")
        if not isinstance(target, str) or not target or target.startswith(("/", "~")):
            raise ContractValidationError(f"{location}.target is invalid")
        if not _DIGEST.fullmatch(integrity if isinstance(integrity, str) else ""):
            raise ContractValidationError(f"{location}.integrity must be a SHA-256 digest")
        if kind == "image":
            if not isinstance(source, str) or not source.startswith("oci://"):
                raise ContractValidationError(f"{location} image source must be an OCI identity")
            if "@sha256:" not in source or not source.endswith(integrity):
                raise ContractValidationError(f"{location} image source must use its digest")
            if name.startswith("runtime-image."):
                if not _DIGEST.fullmatch(str(artifact["platform_integrity"])):
                    raise ContractValidationError(f"{location}.platform_integrity must be a SHA-256 digest")
                runtime_images += 1
        elif not isinstance(source, str) or not source.startswith("https://"):
            raise ContractValidationError(f"{location}.source must be an HTTPS identity")
    missing = REQUIRED_ARTIFACTS - names
    if missing:
        raise ContractValidationError(f"deployment lock is missing artifacts: {sorted(missing)}")
    if runtime_images == 0:
        raise ContractValidationError("deployment lock requires discovered runtime images")
    sha256_digest(document)


def load_deployment_lock(path: str | Path) -> dict[str, Any]:
    with Path(path).open(encoding="utf-8") as handle:
        document = json.load(handle)
    if not isinstance(document, dict):
        raise ContractValidationError("deployment lock must be an object")
    typed_document = cast(dict[str, Any], document)
    validate_deployment_lock(typed_document)
    return typed_document


def deployment_lock_digest(document: Mapping[str, Any]) -> str:
    validate_deployment_lock(document)
    return sha256_digest(document)
