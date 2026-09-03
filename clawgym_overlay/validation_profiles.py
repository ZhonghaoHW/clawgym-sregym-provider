"""Immutable host profiles for the WP4 validation adapter and retained sink."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from clawgym.contracts import sha256_digest
from clawgym.contracts.validation import ContractValidationError, reject_forbidden_content


def _load(path: Path, expected: set[str], schema_id: str) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        document = json.load(handle)
    if not isinstance(document, dict):
        raise ContractValidationError(f"{path.name} has invalid fields")
    document = cast(dict[str, Any], document)
    if set(document) != expected:
        raise ContractValidationError(f"{path.name} has invalid fields")
    if document["schema_id"] != schema_id:
        raise ContractValidationError(f"{path.name} has invalid schema_id")
    reject_forbidden_content(document)
    sha256_digest(document)
    return document


def load_validation_profiles(manifest_root: str | Path) -> tuple[dict[str, Any], dict[str, Any]]:
    root = Path(manifest_root)
    adapter = _load(
        root / "agent.environment-validation.v1.json",
        {
            "schema_id",
            "adapter_id",
            "lane",
            "operation",
            "namespace",
            "resource_name",
            "model_access",
        },
        "clawgym.sregym_environment_validation_profile.v1",
    )
    sink = _load(
        root / "artifact.retained-filesystem.v1.json",
        {"schema_id", "artifact_sink_id", "write_mode", "sensitive_content_rejection"},
        "clawgym.retained_filesystem_profile.v1",
    )
    if adapter != {
        "schema_id": "clawgym.sregym_environment_validation_profile.v1",
        "adapter_id": "sregym.environment-validation.v1",
        "lane": "environment_validation",
        "operation": "delete-network-policy",
        "namespace": "hotel-reservation",
        "resource_name": "deny-all-recommendation",
        "model_access": False,
    }:
        raise ContractValidationError("environment validation profile exceeds its fixed authority")
    if sink["write_mode"] != "exclusive-atomic-json" or sink["sensitive_content_rejection"] is not True:
        raise ContractValidationError("retained sink profile is unsafe")
    return adapter, sink
