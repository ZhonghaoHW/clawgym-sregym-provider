"""Executable WP4 composition root; no provider discovery or dynamic imports."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import threading
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, cast

from clawgym.artifacts import RetainedArtifactSink
from clawgym.contracts import RunManifest, canonical_json_bytes, sha256_digest
from clawgym.providers import ProviderBinding, ProviderDefinition, ProviderRegistry
from clawgym.worker import execute_worker, verify_source_checkout

from clawgym_overlay.compatibility_registry import load_legacy_reference_profile, load_r0_bridge, resolve_r0_profile
from clawgym_overlay.composition import register_sregym_providers
from clawgym_overlay.deployment_lock import deployment_lock_digest, load_deployment_lock
from clawgym_overlay.live_checks import (
    SREGymCausalTelemetryRecorder,
    SREGymLivePhaseProbe,
    build_kubernetes_telemetry_snapshotter,
    capture_oracle_attribution,
    delete_validation_network_policy,
    verify_filtered_kubernetes_access,
)
from clawgym_overlay.locked_runtime import LockedRuntime
from clawgym_overlay.materialized_profile import load_materialized_reference_profile
from clawgym_overlay.providers import SREGymEnvironmentValidationAdapter, SREGymReferenceAgentAdapter
from clawgym_overlay.reference_runner import SafeStratusRunner
from clawgym_overlay.release import load_release_manifests
from clawgym_overlay.validation_profiles import load_validation_profiles
from clawgym_overlay.worker_admission import (
    ExecutionDocuments,
    MaterializedExecutionAdmission,
    validate_campaign_execution,
    validate_worker_admission,
)
from clawgym_overlay.worker_profile import ReferenceAdapterDeps, build_reference_adapter
from clawgym_overlay.worker_runtime import WorkerHostSession, WorkerRuntimeDeps, execute_admitted_trial


def _read_json(path: str | Path) -> dict[str, Any]:
    with Path(path).open(encoding="utf-8") as handle:
        document: Any = json.load(handle)
    if not isinstance(document, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return cast(dict[str, Any], document)


def load_execution_documents(args: argparse.Namespace) -> ExecutionDocuments:
    """Load only explicitly supplied documents; no workspace discovery."""

    def optional(name: str) -> Mapping[str, Any] | None:
        value = getattr(args, name, None)
        return _read_json(value) if value else None

    run_document = _read_json(args.run_manifest)
    run_id = run_document.get("run_id")
    if not isinstance(run_id, str) or not run_id:
        raise ValueError("run manifest must contain a run_id")
    return ExecutionDocuments(
        run=run_document,
        agent=_read_json(args.agent_release),
        environment=_read_json(args.environment_release),
        approval=optional("approval"),
        matrix=optional("matrix"),
        trial=optional("trial"),
        attempt_request=optional("attempt_request"),
        attempt_ledger=optional("attempt_ledger"),
        environment_lease=optional("environment_lease"),
        validation_request=optional("validation_request"),
        candidate=optional("candidate"),
        materialization_receipt=optional("materialization_receipt"),
        parent_agent_release=optional("parent_agent_release"),
        campaign_authorization=optional("campaign_authorization"),
        campaign=optional("campaign"),
        campaign_plan=optional("campaign_plan"),
        readiness_control_set=optional("readiness_control_set"),
    )


def _binding(implementation: Any) -> ProviderBinding:
    return ProviderBinding(
        ProviderDefinition(
            implementation.provider_id,
            implementation.provider_type,
            implementation.immutable_configuration_digest,
        ),
        implementation,
    )


def verify_formal_kind_topology(provider_root: Path, execution_profile: Mapping[str, Any]) -> Path:
    topology = provider_root / "clawgym_overlay" / "kind.wp4.formal.yaml"
    actual = hashlib.sha256(topology.read_bytes()).hexdigest()
    if actual != execution_profile.get("kind_topology_sha256"):
        raise ValueError("formal Kind topology does not match the execution profile")
    return topology


def verify_release_revisions(
    agent_document: Mapping[str, Any],
    environment_document: Mapping[str, Any],
    provider_revision: str,
    compatibility_bridge: Mapping[str, Any] | None = None,
) -> None:
    environment_revision = environment_document.get("overlay_revision")
    if not isinstance(environment_revision, str) or len(environment_revision) != 40:
        raise ValueError("EnvironmentRelease does not identify an immutable overlay revision")
    runtime = agent_document.get("runtime_reference", {})
    if compatibility_bridge is not None:
        # The bridge is the sole, explicit exception for the frozen historical
        # R0 release.  The current checkout still remains content addressed by
        # provider_revision; only the old release/runtime pair is tolerated.
        if agent_document.get("agent_release_digest") != compatibility_bridge["r0_agent_release_digest"]:
            raise ValueError("compatibility bridge is not scoped to this AgentRelease")
        if runtime != {"kind": "source_revision", "reference": compatibility_bridge["historical_provider_revision"]}:
            raise ValueError("R0 historical runtime does not match compatibility bridge")
        if environment_revision != compatibility_bridge["historical_environment_overlay_revision"]:
            raise ValueError("R0 environment overlay does not match compatibility bridge")
        if provider_revision == compatibility_bridge["historical_provider_revision"]:
            raise ValueError("compatibility bridge requires the current executable provider checkout")
        return
    if runtime != {"kind": "source_revision", "reference": provider_revision}:
        raise ValueError("validation AgentRelease does not identify the provider checkout")


def prepare_runtime_workdir(path: str | Path) -> Path:
    """Create a per-attempt private temp root before importing SREGym."""

    root = Path(path)
    if root.exists():
        if root.is_symlink() or not root.is_dir() or any(root.iterdir()):
            raise ValueError("runtime workdir must be a new empty directory")
    else:
        root.mkdir(parents=True, mode=0o700)
    os.chmod(root, 0o700)
    mode = stat.S_IMODE(root.stat().st_mode)
    if mode != 0o700 or not os.access(root, os.W_OK | os.X_OK):
        raise ValueError("runtime workdir is not private and writable")
    os.environ["TMPDIR"] = str(root)
    # tempfile caches the result on first use; set it explicitly as a guard
    # against imports performed before the conductor is constructed.
    import tempfile

    tempfile.tempdir = str(root)
    return root


def execute(args: argparse.Namespace) -> None:
    provider_root = Path(args.provider_checkout).resolve(strict=True)
    clawgym_root = Path(args.clawgym_checkout).resolve(strict=True)
    verify_source_checkout(provider_root, args.provider_revision)
    verify_source_checkout(clawgym_root, args.clawgym_revision)

    documents = load_execution_documents(args)
    run_document = documents.run
    admission = validate_worker_admission(
        documents,
        materialization_bundle=getattr(args, "materialization_bundle", None),
        attempt_claim_root=getattr(args, "attempt_claim_root", None),
        environment_lease_root=getattr(args, "environment_lease_root", None),
    )
    run_id = admission.identity.run_id
    runtime_workdir = args.runtime_workdir or str(Path(args.evidence_root).resolve().parent / f".runtime-{run_id}")
    prepare_runtime_workdir(runtime_workdir)
    agent_document = documents.agent
    environment_document = documents.environment
    approval_document = documents.approval
    matrix_document = documents.matrix
    trial_document = documents.trial
    attempt_request_document = documents.attempt_request
    attempt_ledger_document = documents.attempt_ledger
    environment_lease_document = documents.environment_lease
    request_document = documents.validation_request
    candidate_document = documents.candidate
    receipt_document = documents.materialization_receipt
    parent_document = documents.parent_agent_release
    readiness_attestation_document = (
        _read_json(args.readiness_attestation) if getattr(args, "readiness_attestation", None) else None
    )
    e0_agent_release_document = _read_json(args.e0_agent_release) if getattr(args, "e0_agent_release", None) else None
    compatibility_bridge_document = (
        _read_json(args.r0_compatibility_bridge) if getattr(args, "r0_compatibility_bridge", None) else None
    )
    if compatibility_bridge_document is not None:
        # Validate the on-disk bridge again at the point of use.  The argument
        # is a path, never a caller-supplied digest or profile selector.
        compatibility_bridge_document = load_r0_bridge(args.r0_compatibility_bridge)
    campaign_preflight = validate_campaign_execution(
        documents,
        environment_release_digest=environment_document.get("environment_release_digest"),
        clawgym_revision=args.clawgym_revision,
        provider_revision=args.provider_revision,
    )
    campaign_document = campaign_preflight.campaign
    campaign_admission = campaign_preflight.admission
    verify_release_revisions(
        agent_document, environment_document, args.provider_revision, compatibility_bridge_document
    )

    from sregym.conductor import conductor_api
    from sregym.conductor.conductor import Conductor, ConductorConfig

    run_api_callable: Callable[[Any], None] = cast(Callable[[Any], None], conductor_api.run_api)  # pyright: ignore[reportUnknownMemberType] -- upstream SREGym exposes no typed API stub
    request_shutdown_callable: Callable[[], Any] = cast(Callable[[], Any], conductor_api.request_shutdown)

    deployment_lock = load_deployment_lock(provider_root / "clawgym_overlay" / "deployment.wp4.lock.json")
    if (
        campaign_admission is not None
        and campaign_document is not None
        and campaign_document.get("deployment_lock_digest") != deployment_lock_digest(deployment_lock)
    ):
        raise ValueError("campaign deployment lock does not match provider lock")
    execution_profile = load_release_manifests(provider_root / "clawgym_overlay" / "manifests")["execution"]
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
    conductor: Any = Conductor(conductor_config)
    locked_runtime.configure_services(conductor)
    os.environ["API_BIND_HOST"] = "0.0.0.0"
    os.environ["API_PORT"] = "8000"
    api_thread = threading.Thread(
        target=run_api_callable,
        args=(conductor,),
        name="clawgym-wp5-conductor-api",
        daemon=True,
    )
    session = WorkerHostSession(
        start_api=api_thread.start,
        request_shutdown=request_shutdown_callable,
        join_api=lambda: api_thread.join(timeout=15),
    )
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
            baseline_window_seconds=manifests["fault"]["steady_state"]["baseline_window_seconds"],
            max_experiment_duration_seconds=manifests["fault"]["max_experiment_duration_seconds"],
        ),
        access_verifier=verify_filtered_kubernetes_access,
        attribution_capture=lambda phase: capture_oracle_attribution(conductor, phase),
    )
    if run_document.get("lane") == "agent_validation":
        adapter = build_reference_adapter(
            agent_release=agent_document,
            manifest_root=manifest_root,
            materialization_bundle=args.materialization_bundle,
            compatibility_bridge=compatibility_bridge_document,
            secret_file=args.agent_secret_file,
            deps=ReferenceAdapterDeps(
                load_materialized=lambda bundle: load_materialized_reference_profile(
                    bundle, profile_digest=agent_document.get("invocation_profile_digest")
                ),
                load_legacy=lambda root, digest: load_legacy_reference_profile(root, profile_digest=digest),
                resolve_r0=lambda bridge, release, root: resolve_r0_profile(
                    bridge, agent_release=release, manifest_root=root
                ),
                runner_factory=SafeStratusRunner,
                adapter_factory=SREGymReferenceAgentAdapter,
            ),
        )
    else:
        adapter = SREGymEnvironmentValidationAdapter(
            sha256_digest(adapter_profile),
            delete_validation_network_policy,
            namespace=adapter_profile["namespace"],
            policy_name=adapter_profile["resource_name"],
            steady_state_probe=lambda: bool(conductor.current_problem.mitigation_oracle._run_recommendation_probe()),
            telemetry_capture=telemetry.capture,
        )
    registry.register_binding(_binding(adapter))
    sink = RetainedArtifactSink(
        args.evidence_root,
        run_id,
        provider_id=sink_profile["artifact_sink_id"],
        immutable_configuration_digest=sha256_digest(sink_profile),
    )
    registry.register_binding(_binding(sink))
    run = RunManifest.from_dict(run_document, registry=registry)
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
    if compatibility_bridge_document is not None:
        sink.write_bytes(
            "host/r0-compatibility-bridge.json",
            canonical_json_bytes(
                {
                    "schema_id": "clawgym.r0_panel_compatibility_receipt.v1",
                    "bridge_digest": compatibility_bridge_document["bridge_digest"],
                    "r0_agent_release_digest": compatibility_bridge_document["r0_agent_release_digest"],
                    "historical_profile_digest": compatibility_bridge_document["historical_profile_digest"],
                    "effective_profile_digest": compatibility_bridge_document["effective_profile_digest"],
                    "provider_revision": args.provider_revision,
                }
            ),
            media_type="application/json",
        )
    if campaign_admission is not None:
        sink.write_bytes(
            "host/campaign-admission.json",
            canonical_json_bytes(
                {
                    "schema_id": "clawgym.campaign_admission_receipt.v1",
                    "campaign_digest": campaign_admission[0],
                    "plan_digest": campaign_admission[1],
                    "provider_revision": args.provider_revision,
                    "clawgym_revision": args.clawgym_revision,
                }
            ),
            media_type="application/json",
        )
    with session:
        if isinstance(admission, MaterializedExecutionAdmission):
            materialized = True
            attempt_claim_root: str | Path = admission.attempt_claim_root
            environment_lease_root: str | Path = admission.environment_lease_root
        else:
            materialized = False
            attempt_claim_root = ""
            environment_lease_root = ""

        def unavailable_approved_bridge(_payload: Mapping[str, Any]) -> Any:
            raise RuntimeError("approved bridge is unavailable for legacy execution")

        execute_approved_trial: Callable[..., Any] = unavailable_approved_bridge
        if materialized:
            from clawgym.execution_bridge import execute_approved_trial as approved_bridge

            execute_approved_trial = approved_bridge

        def claim_attempt(request: Mapping[str, Any]) -> Mapping[str, Any]:
            from clawgym.lifecycle_v2 import claim_attempt_v2

            return claim_attempt_v2(
                root=str(attempt_claim_root),
                approval_digest=cast(Mapping[str, Any], approval_document)["approval_record_digest"],
                trial_digest=cast(Mapping[str, Any], trial_document)["trial_digest"],
                attempt_request_digest=request["attempt_request_digest"],
                attempt_number=request["attempt_number"],
                attempt_id=request["execution_attempt_id"],
                runtime_revision=args.provider_revision,
            )

        def approved_executor(payload: Mapping[str, Any]) -> Any:
            claim = payload["attempt_claim"]
            return execute_approved_trial(
                episode_id=args.episode_id,
                request_document=cast(Mapping[str, Any], payload["validation_request"]),
                candidate_document=cast(Mapping[str, Any], payload["candidate"]),
                materialization_receipt=cast(Mapping[str, Any], payload["materialization_receipt"]),
                parent_agent_release_document=cast(Mapping[str, Any], payload["parent_agent_release"]),
                run_document=run_document,
                agent_release_document=agent_document,
                environment_release_document=environment_document,
                registry=registry,
                expected_materializer_revision=args.provider_revision,
                expected_seed=run.seed,
                approval_document=cast(Mapping[str, Any], payload["approval"]),
                matrix_document=cast(Mapping[str, Any], payload["matrix"]),
                trial_document=cast(Mapping[str, Any], payload["trial"]),
                attempt_request_document=cast(Mapping[str, Any], attempt_request_document),
                attempt_ledger_document=cast(Mapping[str, Any], attempt_ledger_document),
                attempt_claim_document=cast(Mapping[str, Any], claim),
                environment_lease_document=cast(Mapping[str, Any], environment_lease_document),
                readiness_attestation_document=readiness_attestation_document,
                e0_agent_release_document=e0_agent_release_document,
                expected_clawgym_revision=args.clawgym_revision,
                attempt_claim_root=str(attempt_claim_root),
                environment_lease_root=str(environment_lease_root),
            )

        def write_claim(claim: Mapping[str, Any]) -> None:
            sink.write_bytes(
                "host/execution-attempt-claim.json", canonical_json_bytes(claim), media_type="application/json"
            )

        deps = WorkerRuntimeDeps(
            claim_attempt=claim_attempt,
            approved_executor=approved_executor,
            legacy_executor=lambda _documents: execute_worker(
                episode_id=args.episode_id,
                run_document=run_document,
                agent_release_document=agent_document,
                environment_release_document=environment_document,
                registry=registry,
            ),
            write_claim=write_claim,
        )
        outcome = execute_admitted_trial(
            materialized=materialized,
            documents={
                "validation_request": request_document,
                "candidate": candidate_document,
                "materialization_receipt": receipt_document,
                "parent_agent_release": parent_document,
                "approval": approval_document,
                "matrix": matrix_document,
                "trial": trial_document,
                "attempt_request": attempt_request_document,
            },
            deps=deps,
        )
        print(json.dumps({"bundle_digest": outcome.bundle_digest, "episode_digest": outcome.episode_digest}))


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
    command.add_argument("--attempt-request")
    command.add_argument("--attempt-ledger")
    command.add_argument("--attempt-claim-root")
    command.add_argument("--environment-lease")
    command.add_argument("--environment-lease-root")
    command.add_argument("--runtime-workdir")
    command.add_argument("--readiness-attestation")
    command.add_argument("--e0-agent-release")
    command.add_argument("--campaign-authorization")
    command.add_argument("--campaign")
    command.add_argument("--campaign-plan")
    command.add_argument("--readiness-control-set")
    command.add_argument("--r0-compatibility-bridge")
    command.set_defaults(handler=execute)
    return result


def main() -> None:
    arguments = parser().parse_args()
    arguments.handler(arguments)


if __name__ == "__main__":
    main()
