"""Immutable, non-secret configuration for the WP5 Stratus control lane."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from clawgym.contracts import sha256_digest
from clawgym.contracts.validation import ContractValidationError, reject_forbidden_content


_EXPECTED = {
    "schema_id",
    "adapter_id",
    "lane",
    "agent",
    "model_id",
    "api_base",
    "command",
    "runtime_injection",
    "runtime_variable",
}


def load_reference_agent_profile(manifest_root: str | Path) -> dict[str, Any]:
    path = Path(manifest_root) / "agent.reference-stratus.v1.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict) or set(document) != _EXPECTED:
        raise ContractValidationError("reference agent profile has invalid fields")
    reject_forbidden_content(document)
    if document["schema_id"] != "clawgym.sregym_reference_agent_profile.v1":
        raise ContractValidationError("reference agent profile has invalid schema_id")
    if document["adapter_id"] != "sregym.reference-agent.v1":
        raise ContractValidationError("reference agent profile has invalid adapter ID")
    if document["lane"] != "agent_validation" or document["agent"] != "stratus":
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
    sha256_digest(document)
    return document
