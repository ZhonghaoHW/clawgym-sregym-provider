from __future__ import annotations

import os
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from clawgym.contracts import sha256_digest

from clawgym_overlay.reference_runner import (
    ReferenceAgentSecretError,
    SafeStratusRunner,
    _safe_text,
    read_agent_secret,
)


def test_reference_secret_requires_private_regular_file(tmp_path: Path) -> None:
    path = tmp_path / "agent-key"
    path.write_text("new-key")
    os.chmod(path, 0o644)
    with pytest.raises(ReferenceAgentSecretError, match="0600"):
        read_agent_secret(path)
    os.chmod(path, 0o600)
    assert read_agent_secret(path) == "new-key"


def test_reference_secret_rejects_empty_or_symlink(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.touch(mode=0o600)
    os.chmod(empty, 0o600)
    with pytest.raises(ReferenceAgentSecretError, match="empty"):
        read_agent_secret(empty)
    linked = tmp_path / "linked"
    linked.symlink_to(empty)
    with pytest.raises(ReferenceAgentSecretError, match="non-symlink"):
        read_agent_secret(linked)


def test_safe_text_redacts_model_key_material_and_host_paths() -> None:
    safe = _safe_text(
        b"Authorization: Bearer abcdefghijklmnop /var/run/example "
        b"10.20.1.27 i-t4nf7igf5ax6pg9s8jc1 client-key-data:"
    )
    assert "abcdefghijklmnop" not in safe
    assert "/var/run/example" not in safe
    assert "10.20.1.27" not in safe
    assert "i-t4nf7igf5ax6pg9s8jc1" not in safe
    assert "client-key-data:" not in safe
    assert "[REDACTED]" in safe
    assert "[HOST_PATH]" in safe


def test_runner_replaces_shell_entrypoint_with_frozen_python_command(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    secret = tmp_path / "agent-key"
    secret.write_text("not-a-real-key")
    secret.chmod(0o600)
    kubeconfig = tmp_path / "filtered-kubeconfig"
    kubeconfig.write_text("safe")
    calls: list[list[str]] = []

    def fake_run(command, **kwargs):
        calls.append(list(command))
        if command[:3] == ["docker", "image", "inspect"]:
            return SimpleNamespace(stdout="sha256:" + "a" * 64 + "\n")
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr("clawgym_overlay.reference_runner.subprocess.run", fake_run)
    runner = SafeStratusRunner(
        profile={
            "model_id": "openai/deepseek-v4-pro",
            "api_base": "https://example.invalid/v1",
            "artifact_id": "network_policy_block",
            "command": ["python", "-m", "clients.stratus.stratus_agent.driver.driver"],
        },
        secret_file=secret,
    )
    result = runner(SimpleNamespace(manifest_digest="a" * 64), str(kubeconfig))
    docker = calls[1]
    entrypoint = docker.index("--entrypoint")
    assert docker[entrypoint + 1] == "python"
    assert docker[-2:] == ["-m", "clients.stratus.stratus_agent.driver.driver"]
    assert "--user" in docker
    assert any(item.endswith(":/home/agent/.kube/config:ro") for item in docker)
    assert "KUBECONFIG=/home/agent/.kube/config" in docker
    assert "SREGYM_ARTIFACT_ID=network_policy_block" in docker
    assert "--env-file" in docker
    assert "AGENT_API_KEY=not-a-real-key" not in docker
    assert result.image_digest == "a" * 64


def test_r1b_runner_mounts_only_registered_bounded_configuration(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    secret = tmp_path / "agent-key"
    secret.write_text("not-a-real-key")
    secret.chmod(0o600)
    kubeconfig = tmp_path / "filtered-kubeconfig"
    kubeconfig.write_text("safe")
    calls: list[list[str]] = []

    def fake_run(command, **kwargs):
        calls.append(list(command))
        if command[:3] == ["docker", "image", "inspect"]:
            return SimpleNamespace(stdout="sha256:" + "b" * 64 + "\n")
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr("clawgym_overlay.reference_runner.subprocess.run", fake_run)
    from clawgym_overlay.reference_profiles import load_reference_agent_profile

    root = Path(__file__).resolve().parents[2] / "clawgym_overlay" / "manifests"
    profile = load_reference_agent_profile(
        root,
        profile_digest=sha256_digest(json.loads((root / "agent.reference-stratus-r1b.v1.json").read_text())),
    )
    result = SafeStratusRunner(profile=profile, secret_file=secret)(
        SimpleNamespace(manifest_digest="b" * 64), str(kubeconfig)
    )
    docker = calls[1]
    assert "PYTHONPATH=/opt/clawgym_overlay:/opt/sregym" in docker
    assert any("diagnosis_agent_config.yaml:ro" in item for item in docker)
    assert any("mitigation_agent_config.yaml:ro" in item for item in docker)
    assert docker[-2:] == ["-m", "reference_driver"]
    assert result.timeout_seconds == 900


def test_r1f_runner_mounts_only_registered_host_normalized_configuration(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    secret = tmp_path / "agent-key"
    secret.write_text("not-a-real-key")
    secret.chmod(0o600)
    kubeconfig = tmp_path / "filtered-kubeconfig"
    kubeconfig.write_text("safe")
    calls: list[list[str]] = []

    def fake_run(command, **kwargs):
        calls.append(list(command))
        if command[:3] == ["docker", "image", "inspect"]:
            return SimpleNamespace(stdout="sha256:" + "c" * 64 + "\n")
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr("clawgym_overlay.reference_runner.subprocess.run", fake_run)
    from clawgym_overlay.reference_profiles import load_reference_agent_profile

    root = Path(__file__).resolve().parents[2] / "clawgym_overlay" / "manifests"
    profile = load_reference_agent_profile(
        root,
        profile_digest=sha256_digest(
            json.loads((root / "agent.reference-stratus-r1f.v1.json").read_text())
        ),
    )
    result = SafeStratusRunner(profile=profile, secret_file=secret)(
        SimpleNamespace(manifest_digest="c" * 64), str(kubeconfig)
    )
    docker = calls[1]
    assert "PYTHONPATH=/opt:/opt/clawgym_overlay:/opt/sregym" in docker
    assert any("reference_driver_r1f.py:ro" in item for item in docker)
    assert any("r1f_protocol.py:ro" in item for item in docker)
    assert any("diagnosis_agent_config.yaml:ro" in item for item in docker)
    assert docker[-2:] == ["-m", "reference_driver_r1f"]
    assert result.timeout_seconds == 900


def test_materialized_runner_mounts_explicit_bundle_and_reference_driver(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    secret = tmp_path / "agent-key"
    secret.write_text("not-a-real-key")
    secret.chmod(0o600)
    kubeconfig = tmp_path / "filtered-kubeconfig"
    kubeconfig.write_text("safe")
    bundle_root = tmp_path / "materialized"
    files_root = bundle_root / "reference-materialized"
    files_root.mkdir(parents=True)
    entries = []
    for name in (
        "diagnosis_agent_config.yaml",
        "diagnosis_agent_prompts.yaml",
        "mitigation_agent_config.yaml",
        "mitigation_agent_prompts.yaml",
    ):
        content = f"{name}: safe\n".encode()
        path = files_root / name
        path.write_bytes(content)
        entries.append(
            {
                "path": f"reference-materialized/{name}",
                "container_path": f"/opt/sregym/clients/stratus/configs/{name}",
                "sha256_digest": __import__("hashlib").sha256(content).hexdigest(),
                "bytes": len(content),
            }
        )
    import hashlib

    config = {
        "schema_id": "clawgym.sregym_reference_agent_config_bundle.v2",
        "component_bundle_digest": "a" * 64,
        "semantic_component_digest": "b" * 64,
        "diagnosis_step_limit": 8,
        "mitigation_step_limit": 8,
        "components": {},
        "files": entries,
    }
    config["config_bundle_digest"] = hashlib.sha256(
        json.dumps(config, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()
    (bundle_root / "config-bundle.json").write_text(
        json.dumps(config, sort_keys=True, separators=(",", ":")), encoding="utf-8"
    )
    calls: list[list[str]] = []

    def fake_run(command, **kwargs):
        calls.append(list(command))
        if command[:3] == ["docker", "image", "inspect"]:
            return SimpleNamespace(stdout="sha256:" + "d" * 64 + "\n")
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr("clawgym_overlay.reference_runner.subprocess.run", fake_run)
    profile = {
        "model_id": "openai/deepseek-v4-pro",
        "api_base": "https://example.invalid/v1",
        "artifact_id": "network_policy_block",
        "command": ["python", "-m", "reference_driver_r1f"],
        "sop_variant": "materialized-reference-v1",
        "runtime_protocol": "r1i-typed-handoff-journal-v1",
        "handoff_argument_protocol": "structured-submit-tool-argument-v1",
        "config_bundle_digest": config["config_bundle_digest"],
        "bounded_execution": {"container_timeout_seconds": 900},
    }
    result = SafeStratusRunner(
        profile=profile, secret_file=secret, materialization_bundle=bundle_root
    )(SimpleNamespace(manifest_digest="d" * 64), str(kubeconfig))
    docker = calls[1]
    assert "PYTHONPATH=/opt:/opt/clawgym_overlay:/opt/sregym" in docker
    assert "SREGYM_RUNTIME_PROTOCOL=r1i-typed-handoff-journal-v1" in docker
    assert "SREGYM_HANDOFF_ARGUMENT_PROTOCOL=structured-submit-tool-argument-v1" in docker
    assert any("reference_driver_r1f.py:ro" in item for item in docker)
    assert any("r1f_protocol.py:ro" in item for item in docker)
    assert sum("diagnosis_agent_config.yaml:ro" in item for item in docker) == 1
    assert docker[-2:] == ["-m", "reference_driver_r1f"]
    assert result.image_digest == "d" * 64


def test_r1i_runner_mounts_typed_handoff_journal_configuration(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    secret = tmp_path / "agent-key"
    secret.write_text("not-a-real-key")
    secret.chmod(0o600)
    kubeconfig = tmp_path / "filtered-kubeconfig"
    kubeconfig.write_text("safe")
    calls: list[list[str]] = []

    def fake_run(command, **kwargs):
        calls.append(list(command))
        if command[:3] == ["docker", "image", "inspect"]:
            return SimpleNamespace(stdout="sha256:" + "d" * 64 + "\n")
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr("clawgym_overlay.reference_runner.subprocess.run", fake_run)
    from clawgym_overlay.reference_profiles import load_reference_agent_profile

    root = Path(__file__).resolve().parents[2] / "clawgym_overlay" / "manifests"
    profile = load_reference_agent_profile(
        root,
        profile_digest=sha256_digest(
            json.loads((root / "agent.reference-stratus-r1i.v1.json").read_text())
        ),
    )
    result = SafeStratusRunner(profile=profile, secret_file=secret)(
        SimpleNamespace(manifest_digest="d" * 64), str(kubeconfig)
    )
    docker = calls[1]
    assert any("reference_driver_r1f.py:ro" in item for item in docker)
    assert any("/opt/sregym/clients/stratus/configs/diagnosis_agent_config.yaml:ro" in item for item in docker)
    assert docker[-2:] == ["-m", "reference_driver_r1f"]
    assert result.timeout_seconds == 900
