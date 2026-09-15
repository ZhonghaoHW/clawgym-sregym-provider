from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from clawgym.contracts import sha256_digest

import clawgym_overlay.worker_profile as worker_profile
from clawgym_overlay.worker_profile import ReferenceAdapterDeps, build_reference_adapter


def _profile() -> dict[str, str]:
    return {"adapter_id": "sregym.reference-agent.v1", "profile_digest": "p" * 64}


def _deps(calls: list[object], profile: dict[str, str]) -> ReferenceAdapterDeps:
    return ReferenceAdapterDeps(
        load_materialized=lambda bundle: calls.append(("materialized", bundle)) or profile,
        load_legacy=lambda root, digest: calls.append(("legacy", root, digest)) or profile,
        resolve_r0=lambda bridge, release, root: calls.append(("r0", bridge, release, root)) or profile,
        runner_factory=lambda **kwargs: calls.append(("runner", kwargs)) or SimpleNamespace(**kwargs),
        adapter_factory=lambda digest, runner: calls.append(("adapter", digest, runner)) or (digest, runner),
    )


def test_builds_materialized_adapter_only_after_identity_matches(tmp_path: Path) -> None:
    calls: list[object] = []
    profile = _profile()
    secret = tmp_path / "secret"
    secret.write_text("test-only", encoding="utf-8")
    secret.chmod(0o600)
    result = build_reference_adapter(
        agent_release={"adapter_id": profile["adapter_id"], "invocation_profile_digest": profile["profile_digest"]},
        manifest_root=tmp_path,
        materialization_bundle=tmp_path / "bundle",
        compatibility_bridge=None,
        secret_file=secret,
        deps=_deps(calls, profile),
    )
    assert calls[0] == ("materialized", tmp_path / "bundle")
    assert result[0] == sha256_digest(profile)


def test_builds_legacy_and_r0_compatibility_adapter(tmp_path: Path) -> None:
    calls: list[object] = []
    profile = _profile()
    bridge = {"historical_profile_digest": profile["profile_digest"]}
    secret = tmp_path / "secret"
    secret.write_text("test-only", encoding="utf-8")
    secret.chmod(0o600)
    result = build_reference_adapter(
        agent_release={"adapter_id": profile["adapter_id"], "invocation_profile_digest": profile["profile_digest"]},
        manifest_root=tmp_path,
        materialization_bundle=None,
        compatibility_bridge=bridge,
        secret_file=secret,
        deps=_deps(calls, profile),
    )
    assert calls[0] == ("legacy", tmp_path, profile["profile_digest"])
    assert calls[1][0] == "r0"
    assert result[0] == sha256_digest(profile)


@pytest.mark.parametrize(
    ("agent", "secret", "message"),
    [
        ({"adapter_id": "wrong", "invocation_profile_digest": "p" * 64}, "secret", "adapter"),
        ({"adapter_id": "sregym.reference-agent.v1", "invocation_profile_digest": "wrong"}, "secret", "invocation"),
        ({"adapter_id": "sregym.reference-agent.v1", "invocation_profile_digest": "p" * 64}, None, "secret"),
    ],
)
def test_rejects_identity_or_secret_before_runner(
    tmp_path: Path, agent: dict[str, str], secret: str | None, message: str
) -> None:
    calls: list[object] = []
    with pytest.raises(ValueError, match=message):
        build_reference_adapter(
            agent_release=agent,
            manifest_root=tmp_path,
            materialization_bundle=tmp_path / "bundle",
            compatibility_bridge=None,
            secret_file=secret,
            deps=_deps(calls, _profile()),
        )
    assert not any(isinstance(item, tuple) and item[0] == "runner" for item in calls)


def test_rejects_missing_secret_before_runner_construction(tmp_path: Path) -> None:
    calls: list[object] = []
    profile = _profile()
    with pytest.raises(ValueError, match="unavailable"):
        build_reference_adapter(
            agent_release={"adapter_id": profile["adapter_id"], "invocation_profile_digest": profile["profile_digest"]},
            manifest_root=tmp_path,
            materialization_bundle=tmp_path / "bundle",
            compatibility_bridge=None,
            secret_file=tmp_path / "missing-secret",
            deps=_deps(calls, profile),
        )
    assert not any(isinstance(item, tuple) and item[0] in {"runner", "adapter"} for item in calls)


def test_explicit_object_reader_rejects_symlink_invalid_encoding_and_non_object(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    valid = target / "profile.json"
    valid.write_text(json.dumps({"adapter_id": "zeroclaw.agent.v1"}), encoding="utf-8")

    link = tmp_path / "link"
    link.symlink_to(target, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        worker_profile._read_explicit_object(link / "profile.json", "profile")

    invalid_encoding = tmp_path / "invalid.json"
    invalid_encoding.write_bytes(b"\xff")
    with pytest.raises(ValueError, match="could not be read"):
        worker_profile._read_explicit_object(invalid_encoding, "profile")

    non_object = tmp_path / "list.json"
    non_object.write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError, match="JSON object"):
        worker_profile._read_explicit_object(non_object, "profile")


def test_zeroclaw_builder_requires_explicit_inputs_before_file_reads(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="explicit inputs"):
        worker_profile.build_zeroclaw_adapter(
            agent_release={},
            logical_profile_path=None,
            config_bundle_path=tmp_path / "config.json",
            executable=tmp_path / "zeroclaw",
            config_dir=tmp_path / "config",
            workspace_dir=tmp_path / "workspace",
            message="run",
        )


def test_zeroclaw_builder_checks_release_identity_before_construction(tmp_path: Path, monkeypatch) -> None:
    profile_path = tmp_path / "profile.json"
    config_path = tmp_path / "config.json"
    profile_path.write_text("{}", encoding="utf-8")
    config_path.write_text("{}", encoding="utf-8")
    executable = tmp_path / "zeroclaw"
    executable.write_text("binary-placeholder", encoding="utf-8")
    config_dir = tmp_path / "config"
    workspace_dir = tmp_path / "workspace"
    config_dir.mkdir()
    workspace_dir.mkdir()
    materialization = SimpleNamespace(profile_digest="p" * 64)
    monkeypatch.setattr(worker_profile, "verify_zeroclaw_logical_profile", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(worker_profile, "materialize_zeroclaw_profile", lambda *_args, **_kwargs: materialization)

    with pytest.raises(ValueError, match="runtime_reference"):
        worker_profile.build_zeroclaw_adapter(
            agent_release={},
            logical_profile_path=profile_path,
            config_bundle_path=config_path,
            executable=executable,
            config_dir=config_dir,
            workspace_dir=workspace_dir,
            message="run",
        )

    common = {
        "logical_profile_path": profile_path,
        "config_bundle_path": config_path,
        "executable": executable,
        "config_dir": config_dir,
        "workspace_dir": workspace_dir,
        "message": "run",
    }
    with pytest.raises(ValueError, match="ZeroClaw adapter"):
        worker_profile.build_zeroclaw_adapter(
            agent_release={"adapter_id": "reference.agent.v1", "runtime_reference": {}}, **common
        )
    with pytest.raises(ValueError, match="invocation profile"):
        worker_profile.build_zeroclaw_adapter(
            agent_release={
                "adapter_id": "zeroclaw.agent.v1",
                "runtime_reference": {},
                "invocation_profile_digest": "q" * 64,
            },
            **common,
        )
