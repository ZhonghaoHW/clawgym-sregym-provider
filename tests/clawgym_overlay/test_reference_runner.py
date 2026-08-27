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
