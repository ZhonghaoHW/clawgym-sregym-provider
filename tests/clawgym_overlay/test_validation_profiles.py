from __future__ import annotations

import json
from pathlib import Path

import pytest
from clawgym.contracts import ContractValidationError, sha256_digest

from clawgym_overlay.reference_profiles import load_reference_agent_profile
from clawgym_overlay.validation_profiles import load_validation_profiles

ROOT = Path(__file__).resolve().parents[2]
MANIFESTS = ROOT / "clawgym_overlay" / "manifests"


def test_validation_profiles_fix_minimal_no_model_authority() -> None:
    adapter, sink = load_validation_profiles(MANIFESTS)
    assert adapter["lane"] == "environment_validation"
    assert adapter["model_access"] is False
    assert adapter["operation"] == "delete-network-policy"
    assert sink["write_mode"] == "exclusive-atomic-json"
    assert len(sha256_digest(adapter)) == 64
    assert len(sha256_digest(sink)) == 64


def test_validation_profile_cannot_expand_authority(tmp_path: Path) -> None:
    for source in MANIFESTS.glob("*.json"):
        (tmp_path / source.name).write_bytes(source.read_bytes())
    path = tmp_path / "agent.environment-validation.v1.json"
    document = json.loads(path.read_text())
    document["model_access"] = True
    path.write_text(json.dumps(document))
    with pytest.raises(ContractValidationError, match="fixed authority"):
        load_validation_profiles(tmp_path)


def test_reference_agent_profile_freezes_gateway_model_and_credential_policy() -> None:
    profile = load_reference_agent_profile(MANIFESTS)
    assert profile["lane"] == "agent_validation"
    assert profile["model_id"] == "openai/deepseek-v4-pro"
    assert profile["runtime_injection"] == "host-only-file"
    assert "api_key" not in profile


def test_reference_r1_profile_is_explicitly_registered() -> None:
    profile = load_reference_agent_profile(
        MANIFESTS,
        profile_digest=sha256_digest(json.loads((MANIFESTS / "agent.reference-stratus-r1.v1.json").read_text())),
    )
    assert profile["sop_variant"] == "r1-evidence-first"
    assert profile["model_id"] == "openai/deepseek-v4-pro"


def test_reference_r1b_profile_is_bounded_and_explicitly_registered() -> None:
    profile = load_reference_agent_profile(
        MANIFESTS,
        profile_digest=sha256_digest(json.loads((MANIFESTS / "agent.reference-stratus-r1b.v1.json").read_text())),
    )
    assert profile["sop_variant"] == "r1-evidence-first-bounded-v1"
    assert profile["bounded_execution"]["diagnosis_max_steps"] == 8
    assert profile["bounded_execution"]["container_timeout_seconds"] == 900


def test_reference_r1c_profiles_are_explicit_and_model_pinned() -> None:
    for filename, variant, model in (
        (
            "agent.reference-stratus-r1c.v1.json",
            "r1c-structured-attribution-v1",
            "openai/glm-5.3-flash",
        ),
        (
            "agent.reference-stratus-r1c-deepseek.v1.json",
            "r1c-structured-attribution-deepseek-v1",
            "openai/deepseek-v4-pro",
        ),
    ):
        profile = load_reference_agent_profile(
            MANIFESTS,
            profile_digest=sha256_digest(json.loads((MANIFESTS / filename).read_text())),
        )
        assert profile["sop_variant"] == variant
        assert profile["model_id"] == model
        assert profile["bounded_execution"] == {
            "diagnosis_max_steps": 8,
            "mitigation_max_steps": 8,
            "container_timeout_seconds": 900,
        }


def test_reference_r1d_profile_is_protocol_bounded_and_explicitly_registered() -> None:
    path = MANIFESTS / "agent.reference-stratus-r1d.v1.json"
    profile = load_reference_agent_profile(MANIFESTS, profile_digest=sha256_digest(json.loads(path.read_text())))
    assert profile["sop_variant"] == "r1d-typed-remediation-v1"
    assert profile["command"] == ["python", "-m", "reference_driver_r1d"]
    assert profile["model_id"] == "openai/deepseek-v4-pro"


def test_reference_r1f_profile_is_explicit_and_host_normalized() -> None:
    path = MANIFESTS / "agent.reference-stratus-r1f.v1.json"
    profile = load_reference_agent_profile(MANIFESTS, profile_digest=sha256_digest(json.loads(path.read_text())))
    assert profile["sop_variant"] == "r1f-host-normalized-remediation-v1"
    assert profile["command"] == ["python", "-m", "reference_driver_r1f"]
    assert profile["bounded_execution"]["mitigation_max_steps"] == 8


def test_reference_r1i_profile_is_explicit_and_journaled() -> None:
    path = MANIFESTS / "agent.reference-stratus-r1i.v1.json"
    profile = load_reference_agent_profile(MANIFESTS, profile_digest=sha256_digest(json.loads(path.read_text())))
    assert profile["sop_variant"] == "r1i-typed-handoff-journal-v1"
    assert profile["command"] == ["python", "-m", "reference_driver_r1f"]


def test_reference_r0_panel_profile_preserves_model_and_adds_only_terminal_bridge() -> None:
    path = MANIFESTS / "agent.reference-stratus-r0-panel.v1.json"
    profile = load_reference_agent_profile(MANIFESTS, profile_digest=sha256_digest(json.loads(path.read_text())))
    assert profile["sop_variant"] == "r0-panel-host-terminal-v1"
    assert profile["model_id"] == "openai/deepseek-v4-pro"
    assert profile["command"] == ["python", "-m", "reference_driver"]
    assert profile["config_bundle_digest"] == "0" * 64


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("schema_id", "wrong", "schema_id"),
        ("adapter_id", "wrong", "adapter ID"),
        ("lane", "wrong", "lane or agent"),
        ("model_id", "openai/other", "model is not registered"),
        ("api_base", "https://other.invalid/v1", "endpoint must remain frozen"),
        ("runtime_injection", "env", "runtime injection policy"),
        ("command", ["python", "-m", "other"], "command is invalid"),
        ("bounded_execution", {"diagnosis_max_steps": 1}, "bounded execution policy"),
        ("config_bundle_digest", "bad", "config bundle digest"),
    ],
)
def test_reference_r1b_profile_rejects_immutable_or_malformed_fields(
    tmp_path: Path, field: str, value: object, message: str
) -> None:
    """Every profile boundary is exercised using an isolated explicit file."""
    source = MANIFESTS / "agent.reference-stratus-r1b.v1.json"
    document = json.loads(source.read_text(encoding="utf-8"))
    document[field] = value
    target = tmp_path / source.name
    target.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ContractValidationError, match=message):
        load_reference_agent_profile(tmp_path, profile_digest=sha256_digest(document))


def test_reference_profile_registry_and_variant_rejections(tmp_path: Path) -> None:
    source = MANIFESTS / "agent.reference-stratus-r1b.v1.json"
    original = json.loads(source.read_text(encoding="utf-8"))

    def reject(changes: dict[str, object], message: str) -> None:
        document = {**original, **changes}
        (tmp_path / source.name).write_text(json.dumps(document), encoding="utf-8")
        with pytest.raises(ContractValidationError, match=message):
            load_reference_agent_profile(tmp_path, profile_digest=sha256_digest(document))

    reject({"extra_field": True}, "invalid fields")
    reject({"sop_variant": "unknown"}, "invalid fields")
    reject(
        {"sop_variant": "r1-evidence-first-bounded-v1", "model_id": "openai/glm-5.3-flash"}, "model must remain frozen"
    )
    reject({"sop_variant": "r1-evidence-first-bounded-v1", "command": ["python"]}, "command is invalid")
    reject(
        {
            "sop_variant": "r1-evidence-first-bounded-v1",
            "bounded_execution": {"diagnosis_max_steps": 8, "mitigation_max_steps": 8, "container_timeout_seconds": 1},
        },
        "bounded execution policy",
    )
    reject({"sop_variant": "r1-evidence-first-bounded-v1", "config_bundle_digest": "bad"}, "config bundle digest")


@pytest.mark.parametrize(
    "filename",
    [
        "agent.reference-stratus-r1c.v1.json",
        "agent.reference-stratus-r1c-deepseek.v1.json",
        "agent.reference-stratus-r1d.v1.json",
        "agent.reference-stratus-r1e.v1.json",
        "agent.reference-stratus-r1f.v1.json",
        "agent.reference-stratus-r1i.v1.json",
        "agent.reference-stratus-r0-panel.v1.json",
    ],
)
def test_each_registered_variant_rejects_runtime_boundary_changes(tmp_path: Path, filename: str) -> None:
    source = MANIFESTS / filename
    original = json.loads(source.read_text(encoding="utf-8"))
    for field, value in (
        ("model_id", "openai/invalid"),
        ("command", ["python"]),
        ("bounded_execution", {"diagnosis_max_steps": 1}),
        ("config_bundle_digest", "bad"),
    ):
        document = {**original, field: value}
        target = tmp_path / filename
        target.write_text(json.dumps(document), encoding="utf-8")
        with pytest.raises(ContractValidationError):
            load_reference_agent_profile(tmp_path, profile_digest=sha256_digest(document))


@pytest.mark.parametrize(
    ("filename", "field", "value", "message"),
    [
        ("agent.reference-stratus-r1c.v1.json", "bounded_execution", {"diagnosis_max_steps": 1}, "bounded"),
        (
            "agent.reference-stratus-r1c-deepseek.v1.json",
            "bounded_execution",
            {"diagnosis_max_steps": 1},
            "bounds",
        ),
        ("agent.reference-stratus-r1d.v1.json", "bounded_execution", {"diagnosis_max_steps": 1}, "bounded"),
        ("agent.reference-stratus-r1e.v1.json", "bounded_execution", {"diagnosis_max_steps": 1}, "bounded"),
        ("agent.reference-stratus-r1f.v1.json", "bounded_execution", {"diagnosis_max_steps": 1}, "bounded"),
        ("agent.reference-stratus-r1i.v1.json", "bounded_execution", {"diagnosis_max_steps": 1}, "bounded"),
        ("agent.reference-stratus-r0-panel.v1.json", "bounded_execution", {"diagnosis_max_steps": 1}, "bounded"),
        ("agent.reference-stratus-r1c.v1.json", "command", ["python"], "command"),
        ("agent.reference-stratus-r1c-deepseek.v1.json", "command", ["python"], "invalid fields"),
        ("agent.reference-stratus-r1d.v1.json", "command", ["python"], "command"),
        ("agent.reference-stratus-r1e.v1.json", "command", ["python"], "command"),
        ("agent.reference-stratus-r1f.v1.json", "command", ["python"], "command"),
        ("agent.reference-stratus-r1i.v1.json", "command", ["python"], "command"),
        ("agent.reference-stratus-r0-panel.v1.json", "command", ["python"], "command"),
    ],
)
def test_each_registered_variant_enforces_its_own_policy_branch(
    tmp_path: Path, filename: str, field: str, value: object, message: str
) -> None:
    """Variant-specific checks are exercised without changing the registry."""
    source = MANIFESTS / filename
    document = json.loads(source.read_text(encoding="utf-8"))
    document[field] = value
    target = tmp_path / filename
    target.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ContractValidationError, match=message):
        load_reference_agent_profile(tmp_path, profile_digest=sha256_digest(document))


@pytest.mark.parametrize(
    ("filename", "field", "value"),
    [
        # These values remain globally registered but violate the selected
        # variant's stricter policy.  This exercises the per-variant guards,
        # rather than the earlier global model check.
        ("agent.reference-stratus-r1c.v1.json", "model_id", "openai/deepseek-v4-pro"),
        ("agent.reference-stratus-r1c-deepseek.v1.json", "model_id", "openai/glm-5.3-flash"),
        ("agent.reference-stratus-r1b.v1.json", "command", ["python", "-m", "reference_driver_r1c"]),
        ("agent.reference-stratus-r1c.v1.json", "command", ["python", "-m", "reference_driver"]),
        ("agent.reference-stratus-r1d.v1.json", "command", ["python", "-m", "reference_driver_r1e"]),
        ("agent.reference-stratus-r1e.v1.json", "command", ["python", "-m", "reference_driver_r1d"]),
        ("agent.reference-stratus-r1f.v1.json", "command", ["python", "-m", "reference_driver_r1d"]),
        ("agent.reference-stratus-r1i.v1.json", "command", ["python", "-m", "reference_driver_r1d"]),
        ("agent.reference-stratus-r0-panel.v1.json", "command", ["python", "-m", "reference_driver_r1d"]),
    ],
)
def test_registered_variant_specific_guards_reject_other_registered_values(
    tmp_path: Path, filename: str, field: str, value: object
) -> None:
    source = MANIFESTS / filename
    document = json.loads(source.read_text(encoding="utf-8"))
    document[field] = value
    target = tmp_path / filename
    target.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ContractValidationError):
        load_reference_agent_profile(tmp_path, profile_digest=sha256_digest(document))


@pytest.mark.parametrize(
    ("filename", "field", "value", "message"),
    [
        ("agent.reference-stratus.v1.json", "adapter_id", "other.adapter", "adapter ID"),
        ("agent.reference-stratus-r1.v1.json", "lane", "environment_validation", "lane or agent"),
        ("agent.reference-stratus-r1b.v1.json", "agent", "other-agent", "lane or agent"),
        ("agent.reference-stratus-r1c.v1.json", "artifact_id", "other-artifact", "lane or agent"),
        ("agent.reference-stratus-r1c-deepseek.v1.json", "api_base", "https://other.invalid/v1", "endpoint"),
        ("agent.reference-stratus-r1d.v1.json", "runtime_injection", "environment", "runtime injection"),
        ("agent.reference-stratus-r1e.v1.json", "runtime_variable", "OTHER_KEY", "runtime injection"),
        ("agent.reference-stratus-r1f.v1.json", "sop_variant", "unregistered", "invalid fields"),
        ("agent.reference-stratus-r1i.v1.json", "model_id", "openai/other", "model is not registered"),
        ("agent.reference-stratus-r0-panel.v1.json", "adapter_id", "other.adapter", "adapter ID"),
    ],
)
def test_each_registered_profile_rejects_global_boundary_drift(
    tmp_path: Path, filename: str, field: str, value: object, message: str
) -> None:
    """Global identity and credential boundaries are enforced for every lane."""
    source = MANIFESTS / filename
    document = json.loads(source.read_text(encoding="utf-8"))
    document[field] = value
    target = tmp_path / filename
    target.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ContractValidationError, match=message):
        load_reference_agent_profile(tmp_path, profile_digest=sha256_digest(document))


def test_profile_registry_rejects_missing_or_unregistered_digest(tmp_path: Path) -> None:
    with pytest.raises(ContractValidationError, match="digest is not registered"):
        load_reference_agent_profile(tmp_path)
    source = MANIFESTS / "agent.reference-stratus-r1b.v1.json"
    (tmp_path / source.name).write_bytes(source.read_bytes())
    with pytest.raises(ContractValidationError, match="digest is not registered"):
        load_reference_agent_profile(tmp_path, profile_digest="f" * 64)
