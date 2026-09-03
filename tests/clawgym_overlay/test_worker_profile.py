from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from clawgym.contracts import sha256_digest

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
    result = build_reference_adapter(
        agent_release={"adapter_id": profile["adapter_id"], "invocation_profile_digest": profile["profile_digest"]},
        manifest_root=tmp_path,
        materialization_bundle=tmp_path / "bundle",
        compatibility_bridge=None,
        secret_file="secret",
        deps=_deps(calls, profile),
    )
    assert calls[0] == ("materialized", tmp_path / "bundle")
    assert result[0] == sha256_digest(profile)


def test_builds_legacy_and_r0_compatibility_adapter(tmp_path: Path) -> None:
    calls: list[object] = []
    profile = _profile()
    bridge = {"historical_profile_digest": profile["profile_digest"]}
    result = build_reference_adapter(
        agent_release={"adapter_id": profile["adapter_id"], "invocation_profile_digest": profile["profile_digest"]},
        manifest_root=tmp_path,
        materialization_bundle=None,
        compatibility_bridge=bridge,
        secret_file="secret",
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
