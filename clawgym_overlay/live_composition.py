"""Explicit live SREGym construction; importing this module loads SREGym services."""

from __future__ import annotations

from pathlib import Path

from clawgym_overlay.composition import build_sregym_bindings
from clawgym_overlay.live_checks import (
    SREGymLivePhaseProbe,
    build_kubernetes_telemetry_snapshotter,
    verify_filtered_kubernetes_access,
)
from clawgym_overlay.release import load_release_manifests
from sregym.conductor.conductor import Conductor, ConductorConfig


def build_live_sregym_bindings():
    """Build live bindings without registering them or discovering plugins."""

    conductor = Conductor(ConductorConfig(defer_cleanup=True))
    manifests = load_release_manifests(Path(__file__).parent / "manifests")

    return build_sregym_bindings(
        conductor=conductor,
        manifests=manifests,
        snapshotter=build_kubernetes_telemetry_snapshotter(conductor),
        phase_probe=SREGymLivePhaseProbe(conductor),
        access_verifier=verify_filtered_kubernetes_access,
    )
