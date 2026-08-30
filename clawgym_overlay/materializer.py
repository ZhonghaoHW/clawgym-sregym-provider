"""Offline, declarative Reference profile materializer for WP7.1.

The materializer consumes only explicit JSON files.  It never discovers files,
loads Python supplied by a candidate, contacts a model/cluster, or overwrites
an existing output directory.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import unicodedata
from pathlib import Path
from typing import Any
import yaml

from clawgym.contracts import sha256_digest

_HEX64 = re.compile(r"^[a-f0-9]{64}$")
_REV = re.compile(r"^[a-f0-9]{40,64}$")
_SECRET_LIKE = re.compile(r"-----BEGIN|(?:api[_-]?key|secret)\s*[:=]|\bsk-[A-Za-z0-9]{8,}|(?:^|\s)/(?:[A-Za-z0-9._-]+/)+", re.IGNORECASE)
_ALLOWED = {
    "diagnosis": {"app_name", "app_description", "app_namespace"},
    "mitigation": {"app_name", "app_description", "app_namespace", "faults_info", "max_step"},
    "retry": {"last_result", "reflection"},
}
_FIXED = ("adapter_id", "lane", "agent", "artifact_id", "model_id", "api_base", "command", "runtime_injection", "runtime_variable")


class MaterializationError(ValueError):
    pass


def _read(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise MaterializationError("input must be a regular file")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise MaterializationError("invalid JSON input") from exc
    if not isinstance(value, dict):
        raise MaterializationError("JSON input must be an object")
    return value


def _digest(document: dict[str, Any], field: str) -> str:
    value = document.get(field)
    if not isinstance(value, str) or not _HEX64.fullmatch(value):
        raise MaterializationError(f"{field} is not a SHA-256 digest")
    payload = dict(document); payload.pop(field, None)
    if sha256_digest(payload) != value:
        raise MaterializationError(f"{field} digest mismatch")
    return value


def _text(value: Any, name: str, group: str) -> str:
    if not isinstance(value, str) or not value or len(value.encode()) > 8192:
        raise MaterializationError(f"{name} must be non-empty and <= 8 KiB")
    if "\x00" in value or "\r" in value or unicodedata.normalize("NFC", value) != value:
        raise MaterializationError(f"{name} has invalid Unicode/newlines")
    if _SECRET_LIKE.search(value):
        raise MaterializationError(f"{name} contains secret-like or path content")
    if not set(re.findall(r"\{([a-z_]+)\}", value)).issubset(_ALLOWED[group]):
        raise MaterializationError(f"{name} contains an unknown template variable")
    return value


def _component_digest(bundle: dict[str, Any]) -> str:
    components = bundle["components"]
    return sha256_digest({"components": components, "diagnosis_step_limit": bundle["diagnosis_step_limit"], "mitigation_step_limit": bundle["mitigation_step_limit"]})


def materialize_reference_profile(*, proposal_path: str | Path, component_bundle_path: str | Path, parent_profile_path: str | Path, output_dir: str | Path, runtime_reference: str) -> dict[str, Any]:
    """Materialize a profile/config bundle and return its immutable receipt."""
    if not _REV.fullmatch(runtime_reference):
        raise MaterializationError("runtime reference must be a commit digest")
    proposal = _read(Path(proposal_path)); bundle = _read(Path(component_bundle_path)); parent = _read(Path(parent_profile_path))
    if proposal.get("schema_id") != "agent_evolution.agent_candidate_proposal.v1":
        raise MaterializationError("proposal schema is not supported")
    _digest(proposal, "proposal_digest")
    if bundle.get("schema_id") != "agent_evolution.reference_agent_component_bundle.v1":
        raise MaterializationError("component bundle schema is not supported")
    _digest(bundle, "component_bundle_digest")
    if proposal.get("component_bundle_digest") != bundle["component_bundle_digest"] or proposal.get("base_agent_release_digest") != parent.get("agent_release_digest", proposal.get("base_agent_release_digest")):
        raise MaterializationError("proposal/component/parent identity mismatch")
    if parent.get("schema_id") not in {"clawgym.sregym_reference_agent_profile.v1", "clawgym.sregym_reference_agent_profile.v2"}:
        raise MaterializationError("parent profile schema is not a Reference profile")
    if set(parent) - {"schema_id", "adapter_id", "lane", "agent", "artifact_id", "model_id", "api_base", "command", "runtime_injection", "runtime_variable", "sop_variant", "bounded_execution", "config_bundle_digest", "agent_release_digest", "tool_policy_profile_bundle_digest", "profile_digest", "runtime_protocol", "handoff_argument_protocol"}:
        raise MaterializationError("parent profile contains unsupported fields")
    for field in _FIXED:
        if field not in parent:
            raise MaterializationError("parent profile is incomplete")
    if parent["adapter_id"] != "sregym.reference-agent.v1" or parent["api_base"].startswith("http") is False:
        raise MaterializationError("parent profile boundary is invalid")
    for name, group in (("system", "diagnosis"), ("user", "diagnosis"), ("summary", "diagnosis")):
        _text(bundle["components"]["diagnosis"][name], f"diagnosis.{name}", group)
    for name, group in (("system", "mitigation"), ("user", "mitigation")):
        _text(bundle["components"]["mitigation"][name], f"mitigation.{name}", group)
    _text(bundle["components"]["mitigation"]["retry_user"], "mitigation.retry_user", "retry")
    if not all(isinstance(bundle.get(k), int) and not isinstance(bundle.get(k), bool) and 1 <= bundle[k] <= 8 for k in ("diagnosis_step_limit", "mitigation_step_limit")):
        raise MaterializationError("step limit outside 1..8")
    target = Path(output_dir)
    if target.exists() or target.is_symlink():
        raise MaterializationError("output directory already exists")
    component_digest = _component_digest(bundle)
    components = bundle["components"]
    rendered = {
        "reference-materialized/diagnosis_agent_config.yaml": yaml.safe_dump({"max_step": bundle["diagnosis_step_limit"], "sync_tools": None, "async_tools": [{"name": "get_traces"}, {"name": "get_services"}, {"name": "get_operations"}, {"name": "get_dependency_graph"}, {"name": "get_metrics"}, {"name": "exec_read_only_kubectl_cmd"}, {"name": "submit_tool"}], "prompts_path": "diagnosis_agent_prompts.yaml", "handoff_protocol": "clawgym.sregym_diagnosis_handoff.v2", "handoff_argument_protocol": "structured-submit-tool-argument-v1"}, sort_keys=True, allow_unicode=True),
        "reference-materialized/mitigation_agent_config.yaml": yaml.safe_dump({"max_step": bundle["mitigation_step_limit"], "max_retry_attempts": 1, "retry_mode": "validate", "sync_tools": [{"name": "wait_tool"}], "async_tools": [{"name": "get_traces"}, {"name": "get_services"}, {"name": "get_operations"}, {"name": "get_dependency_graph"}, {"name": "get_metrics"}, {"name": "exec_kubectl_cmd_safely"}, {"name": "f_submit_tool"}], "prompts_path": "mitigation_agent_prompts.yaml", "transaction_protocol": "clawgym.sregym_remediation_transaction.v2"}, sort_keys=True, allow_unicode=True),
        "reference-materialized/diagnosis_agent_prompts.yaml": yaml.safe_dump({"system": components["diagnosis"]["system"], "user": components["diagnosis"]["user"], "diagnosis_summary_prompt": components["diagnosis"]["summary"]}, sort_keys=True, allow_unicode=True),
        "reference-materialized/mitigation_agent_prompts.yaml": yaml.safe_dump({"system": components["mitigation"]["system"], "user": components["mitigation"]["user"], "retry_user": components["mitigation"]["retry_user"]}, sort_keys=True, allow_unicode=True),
    }
    file_summary = [{"path": path, "container_path": "/opt/sregym/clients/stratus/configs/" + Path(path).name, "sha256_digest": hashlib.sha256(content.encode()).hexdigest(), "bytes": len(content.encode())} for path, content in sorted(rendered.items())]
    config = {
        "schema_id": "clawgym.sregym_reference_agent_config_bundle.v2",
        "component_bundle_digest": bundle["component_bundle_digest"],
        "semantic_component_digest": component_digest,
        "diagnosis_step_limit": bundle["diagnosis_step_limit"],
        "mitigation_step_limit": bundle["mitigation_step_limit"],
        "components": bundle["components"],
        "files": file_summary,
    }
    config["config_bundle_digest"] = sha256_digest(config)
    profile = {key: parent[key] for key in _FIXED}
    profile.update({"schema_id": "clawgym.sregym_reference_agent_profile.v2", "sop_variant": "materialized-reference-v1", "runtime_protocol": "r1i-typed-handoff-journal-v1", "handoff_argument_protocol": "structured-submit-tool-argument-v1", "bounded_execution": {"diagnosis_max_steps": bundle["diagnosis_step_limit"], "mitigation_max_steps": bundle["mitigation_step_limit"], "container_timeout_seconds": 900}, "config_bundle_digest": config["config_bundle_digest"], "component_bundle_digest": bundle["component_bundle_digest"], "semantic_component_digest": component_digest, "materializer_runtime_reference": runtime_reference})
    profile["profile_digest"] = sha256_digest(profile)
    receipt = {"schema_id": "clawgym.agent_materialization_receipt.v1", "proposal_digest": proposal["proposal_digest"], "component_bundle_digest": bundle["component_bundle_digest"], "parent_agent_release_digest": proposal["base_agent_release_digest"], "environment_release_digest": proposal["environment_release_digest"], "adapter_id": profile["adapter_id"], "materializer_id": "clawgym.reference-profile-materializer", "materializer_revision": runtime_reference, "profile_digest": profile["profile_digest"], "config_bundle_digest": config["config_bundle_digest"], "semantic_component_digest": component_digest, "tool_policy_profile_bundle_digest": parent.get("tool_policy_profile_bundle_digest", ""), "file_summary": file_summary, "change_class": proposal["change_class"]}
    receipt["receipt_digest"] = sha256_digest(receipt)
    target.mkdir(parents=True, exist_ok=False)
    for relative, content in rendered.items():
        path = target / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as handle:
            handle.write(content.encode("utf-8"))
    for filename, payload in (("profile.json", profile), ("config-bundle.json", config), ("materialization-receipt.json", receipt)):
        with (target / filename).open("xb") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode())
    return receipt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="provider materialize-reference-profile")
    parser.add_argument("--proposal", required=True); parser.add_argument("--component-bundle", required=True); parser.add_argument("--parent-profile", required=True); parser.add_argument("--output", required=True); parser.add_argument("--runtime-reference", required=True)
    args = parser.parse_args(argv)
    receipt = materialize_reference_profile(proposal_path=args.proposal, component_bundle_path=args.component_bundle, parent_profile_path=args.parent_profile, output_dir=args.output, runtime_reference=args.runtime_reference)
    print(json.dumps(receipt, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
