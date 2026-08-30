"""Executable WP4 composition root; no provider discovery or dynamic imports."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import threading
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
    capture_oracle_attribution,
)
from clawgym_overlay.deployment_lock import deployment_lock_digest, load_deployment_lock
from clawgym_overlay.locked_runtime import LockedRuntime
from clawgym_overlay.providers import SREGymEnvironmentValidationAdapter, SREGymReferenceAgentAdapter
from clawgym_overlay.reference_profiles import load_reference_agent_profile, load_materialized_reference_profile
from clawgym_overlay.reference_runner import SafeStratusRunner
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


def verify_formal_kind_topology(provider_root: Path, execution_profile: dict) -> Path:
    topology = provider_root / "clawgym_overlay" / "kind.wp4.formal.yaml"
    actual = hashlib.sha256(topology.read_bytes()).hexdigest()
    if actual != execution_profile.get("kind_topology_sha256"):
        raise ValueError("formal Kind topology does not match the execution profile")
    return topology


def verify_release_revisions(
    agent_document: dict, environment_document: dict, provider_revision: str
) -> None:
    environment_revision = environment_document.get("overlay_revision")
    if not isinstance(environment_revision, str) or len(environment_revision) != 40:
        raise ValueError("EnvironmentRelease does not identify an immutable overlay revision")
    runtime = agent_document.get("runtime_reference", {})
    if runtime != {"kind": "source_revision", "reference": provider_revision}:
        raise ValueError("validation AgentRelease does not identify the provider checkout")


def execute(args: argparse.Namespace) -> None:
    provider_root = Path(args.provider_checkout).resolve(strict=True)
    clawgym_root = Path(args.clawgym_checkout).resolve(strict=True)
    verify_source_checkout(provider_root, args.provider_revision)
    verify_source_checkout(clawgym_root, args.clawgym_revision)

    run_document = _read_json(args.run_manifest)
    agent_document = _read_json(args.agent_release)
    environment_document = _read_json(args.environment_release)
    approval_document = _read_json(args.approval) if getattr(args, "approval", None) else None
    matrix_document = _read_json(args.matrix) if getattr(args, "matrix", None) else None
    trial_document = _read_json(args.trial) if getattr(args, "trial", None) else None
    request_document = _read_json(args.validation_request) if getattr(args, "validation_request", None) else None
    candidate_document = _read_json(args.candidate) if getattr(args, "candidate", None) else None
    receipt_document = _read_json(args.materialization_receipt) if getattr(args, "materialization_receipt", None) else None
    parent_document = _read_json(args.parent_agent_release) if getattr(args, "parent_agent_release", None) else None
    verify_release_revisions(agent_document, environment_document, args.provider_revision)

    from sregym.conductor.conductor import Conductor, ConductorConfig
    from sregym.conductor.conductor_api import request_shutdown, run_api

    deployment_lock = load_deployment_lock(
        provider_root / "clawgym_overlay" / "deployment.wp4.lock.json"
    )
    execution_profile = load_release_manifests(
        provider_root / "clawgym_overlay" / "manifests"
    )["execution"]
    if execution_profile["deployment_lock_digest"] != deployment_lock_digest(deployment_lock):
        raise ValueError("execution profile does not identify the deployment lock")
    verify_formal_kind_topology(provider_root, execution_profile)
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
    os.environ["API_BIND_HOST"] = "0.0.0.0"
    os.environ["API_PORT"] = "8000"
    api_thread = threading.Thread(
        target=run_api,
        args=(conductor,),
        name="clawgym-wp5-conductor-api",
        daemon=True,
    )
    api_thread.start()
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
        attribution_capture=lambda phase: capture_oracle_attribution(conductor, phase),
    )
    if run_document.get("lane") == "agent_validation":
        if getattr(args, "materialization_bundle", None):
            profile = load_materialized_reference_profile(args.materialization_bundle, profile_digest=agent_document.get("invocation_profile_digest"))
        else:
            profile = load_reference_agent_profile(manifest_root, profile_digest=agent_document.get("invocation_profile_digest"))
        if agent_document.get("adapter_id") != profile["adapter_id"]:
            raise ValueError("AgentRelease does not identify the frozen reference adapter")
        if agent_document.get("invocation_profile_digest") != profile.get("profile_digest"):
            raise ValueError("AgentRelease does not identify the frozen invocation profile")
        if not args.agent_secret_file:
            raise ValueError("WP5 reference worker requires --agent-secret-file")
            adapter = SREGymReferenceAgentAdapter(
                sha256_digest(profile),
                SafeStratusRunner(
                    profile=profile,
                    secret_file=args.agent_secret_file,
                    materialization_bundle=args.materialization_bundle,
                ),
            )
    else:
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
    if run.lane not in {"environment_validation", "agent_validation"}:
        raise ValueError("live worker requires a reviewed validation lane")
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
    try:
        if run_document.get("lane") == "agent_validation" and args.materialization_bundle:
            from clawgym.execution_bridge import execute_approved_trial
            required = (request_document, candidate_document, receipt_document, parent_document, approval_document, matrix_document, trial_document)
            if any(document is None for document in required):
                raise ValueError("materialized candidate execution requires the complete approved artifact chain")
            result = execute_approved_trial(
                episode_id=args.episode_id,
                request_document=request_document,
                candidate_document=candidate_document,
                materialization_receipt=receipt_document,
                parent_agent_release_document=parent_document,
                run_document=run_document,
                agent_release_document=agent_document,
                environment_release_document=environment_document,
                registry=registry,
                expected_materializer_revision=args.provider_revision,
                expected_seed=run.seed,
                approval_document=approval_document,
                matrix_document=matrix_document,
                trial_document=trial_document,
            )
        else:
            result = execute_worker(
                episode_id=args.episode_id,
                run_document=run_document,
                agent_release_document=agent_document,
                environment_release_document=environment_document,
                registry=registry,
            )
        print(json.dumps({"bundle_digest": result.bundle_digest, "episode_digest": result.episode_digest}))
    finally:
        request_shutdown()
        api_thread.join(timeout=15)


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
    command.add_argument("--agent-secret-file")
    command.add_argument("--materialization-bundle")
    command.add_argument("--validation-request")
    command.add_argument("--candidate")
    command.add_argument("--materialization-receipt")
    command.add_argument("--parent-agent-release")
    command.add_argument("--approval")
    command.add_argument("--matrix")
    command.add_argument("--trial")
    command.set_defaults(handler=execute)
    return result


def main() -> None:
    arguments = parser().parse_args()
    arguments.handler(arguments)


if __name__ == "__main__":
    main()
