"""Explicit compatibility bridge for the frozen R0 panel release.

The historical R0 AgentRelease points at the upstream Stratus profile.  The
panel control wrapper lives in the later overlay runtime.  This bridge makes
that one mapping explicit without changing the R0 release document or making
profile discovery implicit.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from clawgym.contracts import sha256_digest
from clawgym.contracts.validation import ContractValidationError

R0_RELEASE_DIGEST = "24c8522e88e50eddff370a12963afd9d644ca6ce176a8981fc8fca592f90b2aa"
R0_HISTORICAL_PROFILE_DIGEST = "ff41878ae0c027efa4d3002aafc08ab4f0704a5a460383a293344d8292060088"
R0_HISTORICAL_PROVIDER_REVISION = "cbe7b5482cecf29d7e1cf73add81e7e146531d62"
R0_PANEL_PROFILE_DIGEST = "8ad9cdcf605bbde2e0ce19212d716301a102eddb489fc9d2091f93744aa1c257"
HISTORICAL_ENVIRONMENT_OVERLAY_REVISION = "bb4c708aed965b3bc2d537e75abcba00928007f9"


def load_r0_panel_bridge(path: str | Path) -> dict[str, Any]:
    candidate = Path(path).resolve(strict=True)
    if candidate.is_symlink() or not candidate.is_file():
        raise ContractValidationError("R0 panel bridge must be an explicit regular file")
    try:
        document = json.loads(candidate.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContractValidationError("R0 panel bridge is invalid JSON") from exc
    if not isinstance(document, dict):
        raise ContractValidationError("R0 panel bridge must be a JSON object")
    document = cast(dict[str, Any], document)
    declared = document.get("bridge_digest")
    payload = dict(document)
    payload.pop("bridge_digest", None)
    if not isinstance(declared, str) or sha256_digest(payload) != declared:
        raise ContractValidationError("R0 panel bridge digest mismatch")
    expected = {
        "schema_id": "clawgym.r0_panel_compatibility_bridge.v1",
        "bridge_id": "r0-panel-host-terminal-v1",
        "scope": "reference_panel_control_only",
        "r0_agent_release_digest": R0_RELEASE_DIGEST,
        "historical_profile_digest": R0_HISTORICAL_PROFILE_DIGEST,
        "historical_provider_revision": R0_HISTORICAL_PROVIDER_REVISION,
        "effective_profile_digest": R0_PANEL_PROFILE_DIGEST,
        "historical_environment_overlay_revision": HISTORICAL_ENVIRONMENT_OVERLAY_REVISION,
    }
    for key, value in expected.items():
        if document.get(key) != value:
            raise ContractValidationError(f"R0 panel bridge field {key} is invalid")
    if set(document) != set(expected) | {"bridge_digest"}:
        raise ContractValidationError("R0 panel bridge has unexpected fields")
    return document


def resolve_r0_panel_profile(
    bridge: dict[str, Any], *, agent_release: dict[str, Any], manifest_root: str | Path
) -> dict[str, Any]:
    if agent_release.get("agent_release_digest") != R0_RELEASE_DIGEST:
        raise ContractValidationError("R0 panel bridge may only select the frozen R0 release")
    if agent_release.get("invocation_profile_digest") != R0_HISTORICAL_PROFILE_DIGEST:
        raise ContractValidationError("R0 panel bridge historical profile mismatch")
    from clawgym_overlay.reference_profiles import load_reference_agent_profile

    return load_reference_agent_profile(manifest_root, profile_digest=bridge["effective_profile_digest"])
