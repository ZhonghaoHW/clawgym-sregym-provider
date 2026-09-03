"""Typed loader for the current materialized Reference profile protocol.

This module is deliberately separate from the historical profile catalogue.
The worker's active materialized path imports only this loader; legacy R0-R1n
profile selection remains behind the compatibility registry.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from clawgym.contracts import sha256_digest
from clawgym.contracts.validation import ContractValidationError, reject_forbidden_content

_REQUIRED_FIELDS = {
    "schema_id",
    "adapter_id",
    "lane",
    "agent",
    "artifact_id",
    "model_id",
    "api_base",
    "command",
    "runtime_injection",
    "runtime_variable",
    "sop_variant",
    "runtime_protocol",
    "handoff_argument_protocol",
    "bounded_execution",
    "config_bundle_digest",
    "component_bundle_digest",
    "semantic_component_digest",
    "materializer_runtime_reference",
    "profile_digest",
}


def load_materialized_reference_profile(
    bundle_root: str | Path, *, profile_digest: str | None = None
) -> dict[str, Any]:
    """Load one explicit v2 materialization bundle; never discover profiles."""

    root = Path(bundle_root).resolve(strict=True)
    path = root / "profile.json"
    if not path.is_file() or path.is_symlink():
        raise ContractValidationError("materialized profile must be an explicit regular file")
    try:
        raw: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContractValidationError("materialized profile is invalid JSON") from exc
    if not isinstance(raw, dict):
        raise ContractValidationError("materialized profile schema is invalid")
    document = cast(dict[str, Any], raw)
    if document.get("schema_id") != "clawgym.sregym_reference_agent_profile.v2":
        raise ContractValidationError("materialized profile schema is invalid")
    if set(document) != _REQUIRED_FIELDS:
        raise ContractValidationError("materialized profile fields are not an exact inventory")
    declared = document.get("profile_digest")
    payload = dict(document)
    payload.pop("profile_digest", None)
    if (
        not isinstance(declared, str)
        or sha256_digest(payload) != declared
        or (profile_digest is not None and declared != profile_digest)
    ):
        raise ContractValidationError("materialized profile digest mismatch")
    if (
        document.get("adapter_id") != "sregym.reference-agent.v1"
        or document.get("runtime_injection") != "host-only-file"
    ):
        raise ContractValidationError("materialized profile boundary is invalid")
    if (
        document.get("lane") != "agent_validation"
        or document.get("agent") != "stratus"
        or document.get("artifact_id") != "network_policy_block"
        or document.get("model_id") != "openai/deepseek-v4-pro"
        or not isinstance(document.get("api_base"), str)
        or not document["api_base"].startswith("https://")
        or document.get("runtime_variable") != "AGENT_API_KEY"
        or document.get("sop_variant") != "materialized-reference-v1"
    ):
        raise ContractValidationError("materialized profile identity boundary is invalid")
    if document.get("command") != ["python", "-m", "reference_driver_r1f"]:
        raise ContractValidationError("materialized profile command is not the pinned Reference driver")
    if document.get("runtime_protocol") != "r1i-typed-handoff-journal-v1":
        raise ContractValidationError("materialized profile runtime protocol is not the pinned typed protocol")
    if document.get("handoff_argument_protocol") != "structured-submit-tool-argument-v1":
        raise ContractValidationError("materialized profile handoff argument protocol is invalid")
    bounded = document.get("bounded_execution")
    if not isinstance(bounded, dict):
        raise ContractValidationError("materialized profile timeout is not frozen")
    typed_bounds = cast(dict[str, Any], bounded)
    if set(typed_bounds) != {"diagnosis_max_steps", "mitigation_max_steps", "container_timeout_seconds"}:
        raise ContractValidationError("materialized profile timeout is not frozen")
    if typed_bounds.get("container_timeout_seconds") != 900 or not all(
        isinstance(typed_bounds.get(key), int) and 1 <= typed_bounds[key] <= 8
        for key in ("diagnosis_max_steps", "mitigation_max_steps")
    ):
        raise ContractValidationError("materialized profile timeout is not frozen")
    for field in (
        "config_bundle_digest",
        "component_bundle_digest",
        "semantic_component_digest",
    ):
        value = document.get(field)
        if not isinstance(value, str) or len(value) != 64:
            raise ContractValidationError("materialized profile component identity is invalid")
    revision = document.get("materializer_runtime_reference")
    if not isinstance(revision, str) or len(revision) not in {40, 64}:
        raise ContractValidationError("materialized profile runtime identity is invalid")
    reject_forbidden_content(document)
    return document


__all__ = ["load_materialized_reference_profile"]
