"""Immutable, non-secret configuration for the WP5 Stratus control lane."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from clawgym.contracts import sha256_digest
from clawgym.contracts.validation import ContractValidationError, reject_forbidden_content


_BASE_EXPECTED = {
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
}

_R1B_EXTRA = {"sop_variant", "bounded_execution", "config_bundle_digest"}
_R1C_EXTRA = _R1B_EXTRA
_R1D_EXTRA = _R1B_EXTRA


def load_reference_agent_profile(
    manifest_root: str | Path, *, profile_digest: str | None = None
) -> dict[str, Any]:
    root = Path(manifest_root)
    candidates = [
        root / "agent.reference-stratus.v1.json",
        root / "agent.reference-stratus-r1.v1.json",
        root / "agent.reference-stratus-r1b.v1.json",
        root / "agent.reference-stratus-r1c.v1.json",
        root / "agent.reference-stratus-r1c-deepseek.v1.json",
        root / "agent.reference-stratus-r1d.v1.json",
    ]
    candidates = [candidate for candidate in candidates if candidate.is_file()]
    path = next(
        (candidate for candidate in candidates if profile_digest is None or sha256_digest(json.loads(candidate.read_text(encoding="utf-8"))) == profile_digest),
        None,
    )
    if path is None:
        raise ContractValidationError("reference agent profile digest is not registered")
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict) or not _BASE_EXPECTED.issubset(document):
        raise ContractValidationError("reference agent profile has invalid fields")
    extra = set(document) - _BASE_EXPECTED
    reject_forbidden_content(document)
    if document["schema_id"] != "clawgym.sregym_reference_agent_profile.v1":
        raise ContractValidationError("reference agent profile has invalid schema_id")
    if document["adapter_id"] != "sregym.reference-agent.v1":
        raise ContractValidationError("reference agent profile has invalid adapter ID")
    if (
        document["lane"] != "agent_validation"
        or document["agent"] != "stratus"
        or document["artifact_id"] != "network_policy_block"
    ):
        raise ContractValidationError("reference agent profile has invalid lane or agent")
    if document["model_id"] not in {"openai/deepseek-v4-pro", "openai/glm-5.3-flash"}:
        raise ContractValidationError("reference agent model is not registered")
    if document["api_base"] != "https://st8tp3ajl0df3n8b8l8qu.apigateway-cn-beijing.volceapi.com/v1":
        raise ContractValidationError("reference agent endpoint must remain frozen")
    if (
        document["runtime_injection"] != "host-only-file"
        or document["runtime_variable"] != "AGENT_API_KEY"
    ):
        raise ContractValidationError("reference agent runtime injection policy is invalid")
    variant = document.get("sop_variant", "r0-baseline")
    if variant == "r1-evidence-first-bounded-v1":
        if document["model_id"] != "openai/deepseek-v4-pro":
            raise ContractValidationError("R1b reference model must remain frozen")
        if extra != _R1B_EXTRA:
            raise ContractValidationError("R1b reference profile has invalid fields")
        if document["command"] != ["python", "-m", "reference_driver"]:
            raise ContractValidationError("R1b reference command is invalid")
        bounded = document["bounded_execution"]
        if bounded != {
            "diagnosis_max_steps": 8,
            "mitigation_max_steps": 8,
            "container_timeout_seconds": 900,
        }:
            raise ContractValidationError("R1b bounded execution policy is invalid")
        digest = document["config_bundle_digest"]
        if not isinstance(digest, str) or len(digest) != 64:
            raise ContractValidationError("R1b config bundle digest is invalid")
    elif variant == "r1c-structured-attribution-v1":
        if document["model_id"] != "openai/glm-5.3-flash":
            raise ContractValidationError("R1c reference model is invalid")
        if extra != _R1C_EXTRA:
            raise ContractValidationError("R1c reference profile has invalid fields")
        if document["command"] != ["python", "-m", "reference_driver_r1c"]:
            raise ContractValidationError("R1c reference command is invalid")
        bounded = document["bounded_execution"]
        if bounded != {"diagnosis_max_steps": 8, "mitigation_max_steps": 8, "container_timeout_seconds": 900}:
            raise ContractValidationError("R1c bounded execution policy is invalid")
        digest = document["config_bundle_digest"]
        if not isinstance(digest, str) or len(digest) != 64:
            raise ContractValidationError("R1c config bundle digest is invalid")
    elif variant == "r1c-structured-attribution-deepseek-v1":
        if document["model_id"] != "openai/deepseek-v4-pro":
            raise ContractValidationError("R1c fallback model is invalid")
        if extra != _R1C_EXTRA or document["command"] != ["python", "-m", "reference_driver_r1c"]:
            raise ContractValidationError("R1c fallback profile has invalid fields")
        if document["bounded_execution"] != {"diagnosis_max_steps": 8, "mitigation_max_steps": 8, "container_timeout_seconds": 900}:
            raise ContractValidationError("R1c fallback bounds are invalid")
        if not isinstance(document["config_bundle_digest"], str) or len(document["config_bundle_digest"]) != 64:
            raise ContractValidationError("R1c fallback config bundle digest is invalid")
    elif variant == "r1d-typed-remediation-v1":
        if document["model_id"] != "openai/deepseek-v4-pro":
            raise ContractValidationError("R1d reference model must remain frozen")
        if extra != _R1D_EXTRA:
            raise ContractValidationError("R1d reference profile has invalid fields")
        if document["command"] != ["python", "-m", "reference_driver_r1d"]:
            raise ContractValidationError("R1d reference command is invalid")
        if document["bounded_execution"] != {"diagnosis_max_steps": 8, "mitigation_max_steps": 8, "container_timeout_seconds": 900}:
            raise ContractValidationError("R1d bounded execution policy is invalid")
        if not isinstance(document["config_bundle_digest"], str) or len(document["config_bundle_digest"]) != 64:
            raise ContractValidationError("R1d config bundle digest is invalid")
    else:
        if extra and extra != {"sop_variant"}:
            raise ContractValidationError("reference agent profile has invalid fields")
        if document["command"] != ["python", "-m", "clients.stratus.stratus_agent.driver.driver"]:
            raise ContractValidationError("reference agent command must remain frozen")
        if variant not in {"r0-baseline", "r1-evidence-first"}:
            raise ContractValidationError("reference agent profile variant is invalid")
    return document
