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


def load_reference_agent_profile(
    manifest_root: str | Path, *, profile_digest: str | None = None
) -> dict[str, Any]:
    root = Path(manifest_root)
    candidates = [root / "agent.reference-stratus.v1.json"]
    r1 = root / "agent.reference-stratus-r1.v1.json"
    if r1.is_file():
        candidates.append(r1)
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
    if extra and extra != {"sop_variant"}:
        raise ContractValidationError("reference agent profile has invalid fields")
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
    if document["model_id"] != "openai/deepseek-v4-pro":
        raise ContractValidationError("reference agent model must remain frozen")
    if document["api_base"] != "https://st8tp3ajl0df3n8b8l8qu.apigateway-cn-beijing.volceapi.com/v1":
        raise ContractValidationError("reference agent endpoint must remain frozen")
    if document["command"] != ["python", "-m", "clients.stratus.stratus_agent.driver.driver"]:
        raise ContractValidationError("reference agent command must remain frozen")
    if (
        document["runtime_injection"] != "host-only-file"
        or document["runtime_variable"] != "AGENT_API_KEY"
    ):
        raise ContractValidationError("reference agent runtime injection policy is invalid")
    if "sop_variant" in document and document["sop_variant"] != "r1-evidence-first":
        raise ContractValidationError("reference agent profile variant is invalid")
    return document
