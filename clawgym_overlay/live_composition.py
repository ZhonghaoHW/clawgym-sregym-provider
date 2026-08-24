"""Explicit live SREGym construction; importing this module loads SREGym services."""

from __future__ import annotations

from pathlib import Path

from sregym.conductor.conductor import Conductor, ConductorConfig

from clawgym_overlay.composition import build_sregym_bindings
from clawgym_overlay.release import load_release_manifests


def build_live_sregym_bindings():
    """Build live bindings without registering them or discovering plugins."""

    conductor = Conductor(ConductorConfig(defer_cleanup=True))
    manifests = load_release_manifests(Path(__file__).parent / "manifests")

    def configured_telemetry():
        return {
            "prometheus": {"configured": conductor.prometheus is not None},
            "loki": {"configured": conductor.loki is not None},
            "jaeger": {"configured": conductor.jaeger is not None},
        }

    return build_sregym_bindings(
        conductor=conductor,
        manifests=manifests,
        snapshotter=configured_telemetry,
    )
