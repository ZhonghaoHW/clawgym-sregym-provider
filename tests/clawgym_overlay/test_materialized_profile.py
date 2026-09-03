from __future__ import annotations

import json
from pathlib import Path

import pytest
from clawgym.contracts import sha256_digest
from clawgym.contracts.validation import ContractValidationError

from clawgym_overlay.materialized_profile import load_materialized_reference_profile


def _profile(**changes: object) -> dict[str, object]:
    document: dict[str, object] = {
        "schema_id": "clawgym.sregym_reference_agent_profile.v2",
        "adapter_id": "sregym.reference-agent.v1",
        "lane": "agent_validation",
        "agent": "stratus",
        "artifact_id": "network_policy_block",
        "model_id": "openai/deepseek-v4-pro",
        "api_base": "https://gateway.invalid/v1",
        "runtime_injection": "host-only-file",
        "runtime_variable": "AGENT_API_KEY",
        "sop_variant": "materialized-reference-v1",
        "command": ["python", "-m", "reference_driver_r1f"],
        "runtime_protocol": "r1i-typed-handoff-journal-v1",
        "handoff_argument_protocol": "structured-submit-tool-argument-v1",
        "bounded_execution": {
            "diagnosis_max_steps": 8,
            "mitigation_max_steps": 8,
            "container_timeout_seconds": 900,
        },
        "config_bundle_digest": "a" * 64,
        "component_bundle_digest": "b" * 64,
        "semantic_component_digest": "c" * 64,
        "materializer_runtime_reference": "d" * 40,
    }
    document.update(changes)
    payload = dict(document)
    payload.pop("profile_digest", None)
    document["profile_digest"] = sha256_digest(payload)
    return document


def _write(root: Path, document: object) -> Path:
    root.mkdir(exist_ok=True)
    path = root / "profile.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return root


def test_loads_only_explicit_regular_profile_with_matching_digest(tmp_path: Path) -> None:
    document = _profile()
    root = _write(tmp_path / "bundle", document)
    assert load_materialized_reference_profile(root, profile_digest=document["profile_digest"]) == document
    with pytest.raises(ContractValidationError, match="digest mismatch"):
        load_materialized_reference_profile(root, profile_digest="0" * 64)


@pytest.mark.parametrize(
    ("document", "message"),
    [
        ([], "schema is invalid"),
        (_profile(schema_id="other"), "schema is invalid"),
        (_profile(extra="unexpected"), "exact inventory"),
        (_profile(adapter_id="other"), "boundary"),
        (_profile(runtime_injection="inline"), "boundary"),
        (_profile(lane="other"), "identity boundary"),
        (_profile(api_base="http://gateway.invalid"), "identity boundary"),
        (_profile(command=["sh", "-c", "unsafe"]), "command"),
        (_profile(runtime_protocol="legacy"), "runtime protocol"),
        (_profile(handoff_argument_protocol="free-text"), "handoff argument"),
        (_profile(bounded_execution={"container_timeout_seconds": 1}), "timeout"),
        (_profile(bounded_execution=[]), "timeout"),
        (_profile(config_bundle_digest="short"), "component identity"),
        (_profile(materializer_runtime_reference="short"), "runtime identity"),
    ],
)
def test_rejects_semantic_boundary_drift(tmp_path: Path, document: object, message: str) -> None:
    root = _write(tmp_path / "bundle", document)
    with pytest.raises(ContractValidationError, match=message):
        load_materialized_reference_profile(root)


def test_rejects_missing_symlink_invalid_json_and_tampered_digest(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_materialized_reference_profile(tmp_path / "missing")

    invalid = tmp_path / "invalid"
    invalid.mkdir()
    (invalid / "profile.json").write_text("{", encoding="utf-8")
    with pytest.raises(ContractValidationError, match="invalid JSON"):
        load_materialized_reference_profile(invalid)

    source = _write(tmp_path / "source", _profile()) / "profile.json"
    linked = tmp_path / "linked"
    linked.mkdir()
    (linked / "profile.json").symlink_to(source)
    with pytest.raises(ContractValidationError, match="regular file"):
        load_materialized_reference_profile(linked)

    tampered = _profile()
    tampered["adapter_id"] = "other"
    root = _write(tmp_path / "tampered", tampered)
    with pytest.raises(ContractValidationError, match="digest mismatch"):
        load_materialized_reference_profile(root)


def test_rejects_forbidden_content_before_returning_profile(tmp_path: Path) -> None:
    document = _profile(api_base="https://gateway.invalid/socket.sock")
    root = _write(tmp_path / "bundle", document)
    with pytest.raises(ContractValidationError, match="forbidden"):
        load_materialized_reference_profile(root)
