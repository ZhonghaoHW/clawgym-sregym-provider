"""Typed, explicit bridge to frozen historical provider protocols.

The active runtime imports this module only.  Historical implementations are
loaded lazily so they remain immutable compatibility assets rather than part
of the current execution graph.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any


def validate_legacy_handoff(
    document: Mapping[str, Any], *, run_manifest_digest: str, agent_release_digest: str
) -> None:
    from clawgym_overlay.r1d_protocol import validate_handoff

    validate_handoff(document, run_manifest_digest=run_manifest_digest, agent_release_digest=agent_release_digest)


def target_resource() -> dict[str, str]:
    from clawgym_overlay.r1e_protocol import TARGET

    return dict(TARGET)


def load_r0_bridge(path: str | Path) -> dict[str, Any]:
    from clawgym_overlay.r0_panel_bridge import load_r0_panel_bridge

    return load_r0_panel_bridge(path)


def resolve_r0_profile(
    bridge: Mapping[str, Any], *, agent_release: Mapping[str, Any], manifest_root: str | Path
) -> dict[str, Any]:
    from clawgym_overlay.r0_panel_bridge import resolve_r0_panel_profile

    return resolve_r0_panel_profile(dict(bridge), agent_release=dict(agent_release), manifest_root=manifest_root)


def load_legacy_reference_profile(manifest_root: str | Path, *, profile_digest: str | None = None) -> dict[str, Any]:
    """Load a frozen R0--R1n profile only through the compatibility boundary."""

    from clawgym_overlay.reference_profiles import load_reference_agent_profile

    return load_reference_agent_profile(manifest_root, profile_digest=profile_digest)
