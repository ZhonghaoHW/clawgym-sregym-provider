"""Reference-adapter construction after worker admission.

The composition root delegates profile selection here so current materialized
profiles and frozen compatibility profiles have one explicit, typed boundary.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from clawgym.contracts import sha256_digest


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
