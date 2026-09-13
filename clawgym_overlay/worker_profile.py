"""Reference-adapter construction after worker admission.

The composition root delegates profile selection here so current materialized
profiles and frozen compatibility profiles have one explicit, typed boundary.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from clawgym.contracts import sha256_digest
from clawgym.zeroclaw_adapter import ZeroClawAgentAdapter, ZeroClawInvocationProfile
from clawgym.zeroclaw_profile import materialize_zeroclaw_profile, verify_zeroclaw_logical_profile


@dataclass(frozen=True)
class ReferenceAdapterDeps:
    load_materialized: Callable[[str | Path], dict[str, Any]]
    load_legacy: Callable[[str | Path, str | None], dict[str, Any]]
    resolve_r0: Callable[[Mapping[str, Any], Mapping[str, Any], str | Path], dict[str, Any]]
    runner_factory: Callable[..., Any]
    adapter_factory: Callable[[str, Any], Any]


def build_reference_adapter(
    *,
    agent_release: Mapping[str, Any],
    manifest_root: str | Path,
    materialization_bundle: str | Path | None,
    compatibility_bridge: Mapping[str, Any] | None,
    secret_file: str | None,
    deps: ReferenceAdapterDeps,
) -> Any:
    """Build the only Reference adapter permitted after host admission."""

    profile_digest = agent_release.get("invocation_profile_digest")
    if materialization_bundle:
        profile = deps.load_materialized(materialization_bundle)
    else:
        profile = deps.load_legacy(manifest_root, profile_digest if isinstance(profile_digest, str) else None)
    if compatibility_bridge is not None:
        profile = deps.resolve_r0(compatibility_bridge, agent_release, manifest_root)
    if agent_release.get("adapter_id") != profile.get("adapter_id"):
        raise ValueError("AgentRelease does not identify the frozen reference adapter")
    expected_profile_digest = (
        compatibility_bridge.get("historical_profile_digest")
        if compatibility_bridge is not None
        else profile.get("profile_digest") or sha256_digest(profile)
    )
    if agent_release.get("invocation_profile_digest") != expected_profile_digest:
        raise ValueError("AgentRelease does not identify the frozen invocation profile")
    if not secret_file:
        raise ValueError("WP5 reference worker requires --agent-secret-file")
    return deps.adapter_factory(
        sha256_digest(profile),
        deps.runner_factory(
            profile=profile,
            secret_file=secret_file,
            materialization_bundle=str(materialization_bundle) if materialization_bundle else None,
        ),
    )


def _read_explicit_object(path: str | Path, label: str) -> dict[str, Any]:
    """Read one host-selected release object without following symlinks."""

    candidate = Path(path)
    if not candidate.is_absolute() or candidate.is_symlink() or not candidate.is_file():
        raise ValueError(f"{label} must be an absolute regular file")
    current = Path(candidate.anchor)
    for part in candidate.parts[1:-1]:
        current /= part
        if current.is_symlink():
            raise ValueError(f"{label} path contains a symlink")
    try:
        with candidate.open(encoding="utf-8") as handle:
            document: Any = json.load(handle)
    except (OSError, UnicodeDecodeError) as exc:
        raise ValueError(f"{label} could not be read") from exc
    if not isinstance(document, dict):
        raise ValueError(f"{label} must contain a JSON object")
    return cast(dict[str, Any], document)


def build_zeroclaw_adapter(
    *,
    agent_release: Mapping[str, Any],
    logical_profile_path: str | Path | None,
    config_bundle_path: str | Path | None,
    executable: str | Path | None,
    config_dir: str | Path | None,
    workspace_dir: str | Path | None,
    message: str | None,
) -> ZeroClawAgentAdapter:
    """Build ZeroClaw from its explicit released profile and host bindings.

    The profile and config inventory are released JSON inputs; executable and
    directories are host-owned bindings validated by ClawGym's adapter.  No
    Reference profile, lane, model name, or provider-specific discovery is
    consulted here.
    """

    required = {
        "--zeroclaw-logical-profile": logical_profile_path,
        "--zeroclaw-config-bundle": config_bundle_path,
        "--zeroclaw-executable": executable,
        "--zeroclaw-config-dir": config_dir,
        "--zeroclaw-workspace-dir": workspace_dir,
        "--zeroclaw-message": message,
    }
    missing = [name for name, value in required.items() if value is None or value == ""]
    if missing:
        raise ValueError(f"ZeroClaw adapter requires explicit inputs: {', '.join(missing)}")

    profile = _read_explicit_object(cast(str | Path, logical_profile_path), "ZeroClaw logical profile")
    config_bundle = _read_explicit_object(cast(str | Path, config_bundle_path), "ZeroClaw config bundle")
    runtime_reference = agent_release.get("runtime_reference")
    if not isinstance(runtime_reference, Mapping):
        raise ValueError("ZeroClaw AgentRelease runtime_reference is invalid")
    verify_zeroclaw_logical_profile(
        profile,
        expected_runtime_reference=dict(cast(Mapping[str, str], runtime_reference)),
    )
    materialization = materialize_zeroclaw_profile(profile, config_bundle)
    if agent_release.get("adapter_id") != "zeroclaw.agent.v1":
        raise ValueError("AgentRelease does not identify the ZeroClaw adapter")
    if agent_release.get("invocation_profile_digest") != materialization.profile_digest:
        raise ValueError("AgentRelease does not identify the ZeroClaw invocation profile")
    return ZeroClawAgentAdapter(
        ZeroClawInvocationProfile(
            materialization=materialization,
            executable=Path(cast(str | Path, executable)),
            config_dir=Path(cast(str | Path, config_dir)),
            workspace_dir=Path(cast(str | Path, workspace_dir)),
            message=cast(str, message),
        )
    )
