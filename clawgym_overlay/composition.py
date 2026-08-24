"""Explicit composition root for the five SREGym environment providers."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from clawgym.providers import ProviderBinding, ProviderDefinition, ProviderRegistry
from clawgym_overlay.providers import (
    SREGymEnvironmentProvider,
    SREGymExecutionBackend,
    SREGymObservationProvider,
    SREGymOracleProvider,
    SREGymToolAccessProvider,
)
from clawgym_overlay.release import provider_configuration_digests


def build_sregym_bindings(
    *,
    conductor,
    manifests: Mapping[str, Mapping[str, Any]],
    snapshotter: Callable[[], Mapping[str, Any]],
    phase_probe: Callable[[str], Mapping[str, Any]] | None = None,
    access_verifier: Callable[[str], Mapping[str, Any]] | None = None,
) -> tuple[ProviderBinding, ...]:
    digests = provider_configuration_digests(manifests)
    problem = manifests["problem"]
    oracle = manifests["oracle"]
    tool = manifests["tool"]
    observation = manifests["observation"]
    execution = manifests["execution"]
    implementations = (
        SREGymEnvironmentProvider(
            conductor=conductor,
            immutable_configuration_digest=digests["sregym.environment.v1"],
            problem_id=problem["problem_id"],
            task_stages=tuple(problem["task_stages"]),
            phase_probe=phase_probe,
        ),
        SREGymOracleProvider(
            conductor=conductor,
            immutable_configuration_digest=digests["sregym.oracle.v1"],
            required_stages=tuple(oracle["required_stages"]),
        ),
        SREGymToolAccessProvider(
            conductor=conductor,
            immutable_configuration_digest=digests["sregym.filtered-tools.v1"],
            interfaces=tuple(tool["interfaces"]),
            capabilities=tuple(tool["capabilities"]),
            denied_namespaces=tuple(tool["denied_namespaces"]),
            access_verifier=access_verifier,
        ),
        SREGymObservationProvider(
            immutable_configuration_digest=digests["sregym.observation.v1"],
            sources=tuple(observation["sources"]),
            snapshotter=snapshotter,
        ),
        SREGymExecutionBackend(
            immutable_configuration_digest=digests["sregym.container-execution.v1"],
            timeout_seconds=execution["timeout_seconds"],
        ),
    )
    return tuple(
        ProviderBinding(
            ProviderDefinition(
                implementation.provider_id,
                implementation.provider_type,
                implementation.immutable_configuration_digest,
            ),
            implementation,
        )
        for implementation in implementations
    )


def register_sregym_providers(
    registry: ProviderRegistry,
    *,
    conductor,
    manifests: Mapping[str, Mapping[str, Any]],
    snapshotter: Callable[[], Mapping[str, Any]],
    phase_probe: Callable[[str], Mapping[str, Any]] | None = None,
    access_verifier: Callable[[str], Mapping[str, Any]] | None = None,
) -> tuple[ProviderBinding, ...]:
    bindings = build_sregym_bindings(
        conductor=conductor,
        manifests=manifests,
        snapshotter=snapshotter,
        phase_probe=phase_probe,
        access_verifier=access_verifier,
    )
    for binding in bindings:
        registry.register_binding(binding)
    return bindings
