from __future__ import annotations

import json
from pathlib import Path

import pytest
from clawgym.zeroclaw_profile import build_zeroclaw_config_bundle, build_zeroclaw_logical_profile

from clawgym_overlay.agent_composition import AgentAdapterBuildContext, AgentAdapterComposition
from clawgym_overlay.worker_profile import build_zeroclaw_adapter


def test_composition_uses_exact_agent_release_id_and_rejects_unknown() -> None:
    composition = AgentAdapterComposition()
    calls: list[str] = []

    class FakeAdapter:
        provider_id = "example.agent.v1"
        provider_type = "agent_adapter"
        immutable_configuration_digest = "a" * 64

    composition.register("example.agent.v1", lambda _context: calls.append("built") or FakeAdapter())
    context = AgentAdapterBuildContext(
        agent_release={"adapter_id": "example.agent.v1"},
        manifest_root=Path("/tmp/manifest-root"),
        materialization_bundle=None,
        compatibility_bridge=None,
        secret_file=None,
    )
    assert composition.build(context).provider_id == "example.agent.v1"
    assert calls == ["built"]

    with pytest.raises(ValueError, match="not explicitly composed"):
        composition.build(
            AgentAdapterBuildContext(
                agent_release={"adapter_id": "untrusted.agent.v1"},
                manifest_root=context.manifest_root,
                materialization_bundle=None,
                compatibility_bridge=None,
                secret_file=None,
            )
        )

    with pytest.raises(ValueError, match="already registered"):
        composition.register("example.agent.v1", lambda _context: FakeAdapter())


def test_zeroclaw_builder_binds_profile_runtime_and_config_before_adapter_creation(tmp_path: Path) -> None:
    runtime_reference = {"kind": "source_revision", "reference": "z" * 40}
    config_dir = tmp_path / "config"
    workspace_dir = tmp_path / "workspace"
    config_dir.mkdir()
    workspace_dir.mkdir()
    config_content = '[gateway]\nkind = "custom"\n'
    (config_dir / "config.toml").write_text(config_content, encoding="utf-8")
    bundle = build_zeroclaw_config_bundle({"config.toml": config_content})
    profile = build_zeroclaw_logical_profile(
        profile_id="zeroclaw-wave5",
        runtime_reference=runtime_reference,
        config_bundle_digest=bundle["config_bundle_digest"],
        workspace_policy_digest="a" * 64,
        environment_allowlist=("AGENT_API_KEY",),
    )
    profile_path = tmp_path / "profile.json"
    bundle_path = tmp_path / "bundle.json"
    profile_path.write_text(json.dumps(profile), encoding="utf-8")
    bundle_path.write_text(json.dumps(bundle), encoding="utf-8")
    executable = tmp_path / "zeroclaw"
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o700)
    release = {
        "adapter_id": "zeroclaw.agent.v1",
        "runtime_reference": runtime_reference,
        "invocation_profile_digest": profile["profile_digest"],
    }

    adapter = build_zeroclaw_adapter(
        agent_release=release,
        logical_profile_path=profile_path,
        config_bundle_path=bundle_path,
        executable=executable,
        config_dir=config_dir,
        workspace_dir=workspace_dir,
        message="run the local task",
    )
    assert adapter.provider_id == "zeroclaw.agent.v1"
    assert adapter.profile.logical_profile_digest == profile["profile_digest"]
    assert adapter.profile.materialization.config_bundle_digest == bundle["config_bundle_digest"]

    with pytest.raises(ValueError, match="runtime mismatch"):
        build_zeroclaw_adapter(
            agent_release={**release, "runtime_reference": {"kind": "source_revision", "reference": "x" * 40}},
            logical_profile_path=profile_path,
            config_bundle_path=bundle_path,
            executable=executable,
            config_dir=config_dir,
            workspace_dir=workspace_dir,
            message="run the local task",
        )


def test_zeroclaw_builder_rejects_symlinked_release_object(tmp_path: Path) -> None:
    profile_path = tmp_path / "profile.json"
    target = tmp_path / "target.json"
    target.write_text(json.dumps({}), encoding="utf-8")
    profile_path.symlink_to(target)
    with pytest.raises(ValueError, match="regular file"):
        build_zeroclaw_adapter(
            agent_release={},
            logical_profile_path=profile_path,
            config_bundle_path=tmp_path / "bundle.json",
            executable=tmp_path / "zeroclaw",
            config_dir=tmp_path / "config",
            workspace_dir=tmp_path / "workspace",
            message="task",
        )
