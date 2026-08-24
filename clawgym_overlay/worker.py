"""Executable WP4 composition root; no provider discovery or dynamic imports."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from clawgym.artifacts import RetainedArtifactSink
from clawgym.contracts import RunManifest, canonical_json_bytes, sha256_digest
from clawgym.providers import ProviderBinding, ProviderDefinition, ProviderRegistry
from clawgym.worker import execute_worker, verify_source_checkout
from clawgym_overlay.composition import register_sregym_providers
from clawgym_overlay.live_checks import (
    SREGymCausalTelemetryRecorder,
    SREGymLivePhaseProbe,
    build_kubernetes_telemetry_snapshotter,
    delete_validation_network_policy,
    verify_filtered_kubernetes_access,
)
from clawgym_overlay.deployment_lock import deployment_lock_digest, load_deployment_lock
from clawgym_overlay.locked_runtime import LockedRuntime
from clawgym_overlay.providers import SREGymEnvironmentValidationAdapter
from clawgym_overlay.release import load_release_manifests
from clawgym_overlay.validation_profiles import load_validation_profiles


def _read_json(path: str | Path):
    with Path(path).open(encoding="utf-8") as handle:
        document = json.load(handle)
    if not isinstance(document, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return document


def _binding(implementation) -> ProviderBinding:
    return ProviderBinding(
        ProviderDefinition(
            implementation.provider_id,
            implementation.provider_type,
            implementation.immutable_configuration_digest,
        ),
        implementation,
    )


def execute(args: argparse.Namespace) -> None:
    provider_root = Path(args.provider_checkout).resolve(strict=True)
    clawgym_root = Path(args.clawgym_checkout).resolve(strict=True)
    verify_source_checkout(provider_root, args.provider_revision)
    verify_source_checkout(clawgym_root, args.clawgym_revision)

    run_document = _read_json(args.run_manifest)
    agent_document = _read_json(args.agent_release)
    environment_document = _read_json(args.environment_release)
    if environment_document.get("overlay_revision") != args.provider_revision:
        raise ValueError("EnvironmentRelease does not identify the provider checkout")
    runtime = agent_document.get("runtime_reference", {})
    if runtime != {"kind": "source_revision", "value": args.provider_revision}:
        raise ValueError("validation AgentRelease does not identify the provider checkout")

    from sregym.conductor.conductor import Conductor, ConductorConfig

    deployment_lock = load_deployment_lock(
        provider_root / "clawgym_overlay" / "deployment.wp4.lock.json"
    )
    execution_profile = load_release_manifests(
        provider_root / "clawgym_overlay" / "manifests"
    )["execution"]
    if execution_profile["deployment_lock_digest"] != deployment_lock_digest(deployment_lock):
        raise ValueError("execution profile does not identify the deployment lock")
    locked_runtime = LockedRuntime(deployment_lock, args.deployment_cache)
    conductor_config = ConductorConfig(
            deploy_loki=True,
            enable_noise=False,
            defer_cleanup=True,
            task_stages=("mitigation",),
    )
    locked_runtime.configure_conductor(conductor_config)
    conductor = Conductor(conductor_config)
    locked_runtime.configure_services(conductor)
    manifest_root = provider_root / "clawgym_overlay" / "manifests"
    manifests = load_release_manifests(manifest_root)
    adapter_profile, sink_profile = load_validation_profiles(manifest_root)
    registry = ProviderRegistry()
    telemetry = SREGymCausalTelemetryRecorder(build_kubernetes_telemetry_snapshotter(conductor))
    register_sregym_providers(
        registry,
        conductor=conductor,
        manifests=manifests,
        snapshotter=telemetry,
        phase_probe=SREGymLivePhaseProbe(
            conductor,
            telemetry_capture=telemetry.capture,
            runtime_image_inventory=lambda: locked_runtime.cluster_image_inventory(conductor),
            baseline_window_seconds=manifests["fault"]["steady_state"][
                "baseline_window_seconds"
            ],
            max_experiment_duration_seconds=manifests["fault"][
                "max_experiment_duration_seconds"
            ],
        ),
        access_verifier=verify_filtered_kubernetes_access,
    )
    adapter = SREGymEnvironmentValidationAdapter(
        sha256_digest(adapter_profile),
        delete_validation_network_policy,
        namespace=adapter_profile["namespace"],
        policy_name=adapter_profile["resource_name"],
        steady_state_probe=lambda: bool(
            conductor.current_problem.mitigation_oracle._run_recommendation_probe()
        ),
        telemetry_capture=telemetry.capture,
    )
    registry.register_binding(_binding(adapter))
    run_id = run_document.get("run_id")
    sink = RetainedArtifactSink(
        args.evidence_root,
        run_id,
        provider_id=sink_profile["artifact_sink_id"],
        immutable_configuration_digest=sha256_digest(sink_profile),
    )
    registry.register_binding(_binding(sink))
    run = RunManifest.from_dict(run_document, registry=registry)
    if run.lane != "environment_validation":
        raise ValueError("WP4 live worker requires environment_validation lane")
    sink.write_bytes(
        "host/source-checkouts.json",
        canonical_json_bytes(
            {
                "schema_id": "clawgym.worker_source_checkouts.v1",
                "clawgym_revision": args.clawgym_revision,
                "provider_revision": args.provider_revision,
            }
        ),
        media_type="application/json",
    )
    sink.write_bytes(
        "host/deployment-cache.json",
        canonical_json_bytes(locked_runtime.cache_summary()),
        media_type="application/json",
    )
    result = execute_worker(
        episode_id=args.episode_id,
        run_document=run_document,
        agent_release_document=agent_document,
        environment_release_document=environment_document,
        registry=registry,
    )
    print(json.dumps({"bundle_digest": result.bundle_digest, "episode_digest": result.episode_digest}))


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="clawgym-provider-worker")
    subcommands = result.add_subparsers(dest="command", required=True)
    command = subcommands.add_parser("execute")
    command.add_argument("--run-manifest", required=True)
    command.add_argument("--agent-release", required=True)
    command.add_argument("--environment-release", required=True)
    command.add_argument("--evidence-root", required=True)
    command.add_argument("--episode-id", required=True)
    command.add_argument("--clawgym-checkout", required=True)
    command.add_argument("--clawgym-revision", required=True)
    command.add_argument("--provider-checkout", required=True)
    command.add_argument("--provider-revision", required=True)
    command.add_argument("--deployment-cache", required=True)
    command.set_defaults(handler=execute)
    return result


def main() -> None:
    arguments = parser().parse_args()
    arguments.handler(arguments)


if __name__ == "__main__":
    main()
