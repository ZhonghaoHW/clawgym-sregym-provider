"""Executable WP4 composition root; no provider discovery or dynamic imports."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import threading
from pathlib import Path

from clawgym.artifacts import RetainedArtifactSink
from clawgym.contracts import RunManifest, canonical_json_bytes, sha256_digest
from clawgym.providers import ProviderBinding, ProviderDefinition, ProviderRegistry
from clawgym.worker import execute_worker, verify_source_checkout
from clawgym.attempt_authority import claim_attempt
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
from clawgym_overlay.r0_panel_bridge import load_r0_panel_bridge, resolve_r0_panel_profile
from clawgym_overlay.reference_runner import SafeStratusRunner
from clawgym_overlay.release import load_release_manifests
from clawgym_overlay.validation_profiles import load_validation_profiles


def _read_json(path: str | Path):
    with Path(path).open(encoding="utf-8") as handle:
        document = json.load(handle)
    if not isinstance(document, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return document


def _verify_campaign_authorization(document: dict, *, candidate_digest: str, trial_digest: str, approval_digest: str, case_id: str, seed: int, partition: str, purpose: str, campaign_digest: str | None = None, generation: int | None = None) -> None:
    """Use the ClawGym bridge when available, with a narrow compatibility
    fallback for locked historical environments that predate WP7.4."""
    try:
        from clawgym.execution_bridge import verify_campaign_trial_authorization
    except ImportError:
        from clawgym.contracts import ContractValidationError, sha256_digest
        expected = dict(document)
        actual = expected.pop("authorization_digest", None)
        if actual != sha256_digest(expected):
            raise ContractValidationError("campaign authorization digest mismatch")
        if document.get("schema_id") != "agent_evolution.campaign_trial_authorization.v1" or document.get("execution_scope") != "reference_family_only":
            raise ContractValidationError("campaign authorization scope is invalid")
        if document.get("purpose") != purpose or document.get("partition") != partition or document.get("case_id") != case_id or document.get("seed") != seed:
            raise ContractValidationError("campaign authorization trial identity mismatch")
        if document.get("candidate_digest") != candidate_digest or document.get("trial_digest") != trial_digest or document.get("approval_digest") != approval_digest:
            raise ContractValidationError("campaign authorization digest references mismatch")
        if campaign_digest is not None and document.get("campaign_digest") != campaign_digest:
            raise ContractValidationError("campaign authorization campaign mismatch")
        if generation is not None and document.get("generation") != generation:
            raise ContractValidationError("campaign authorization generation mismatch")
        return
    verify_campaign_trial_authorization(
        authorization_document=document, candidate_digest=candidate_digest,
        trial_digest=trial_digest, approval_digest=approval_digest,
        case_id=case_id, seed=seed, partition=partition, purpose=purpose,
        campaign_digest=campaign_digest, generation=generation,
    )


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
    agent_document: dict, environment_document: dict, provider_revision: str,
    compatibility_bridge: dict | None = None,
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

    run_document = _read_json(args.run_manifest)
    run_id = run_document.get("run_id")
    if not isinstance(run_id, str) or not run_id:
        raise ValueError("run manifest must contain a run_id")
    runtime_workdir = args.runtime_workdir or str(Path(args.evidence_root).resolve().parent / f".runtime-{run_id}")
    prepare_runtime_workdir(runtime_workdir)
    agent_document = _read_json(args.agent_release)
    environment_document = _read_json(args.environment_release)
    approval_document = _read_json(args.approval) if getattr(args, "approval", None) else None
    matrix_document = _read_json(args.matrix) if getattr(args, "matrix", None) else None
    trial_document = _read_json(args.trial) if getattr(args, "trial", None) else None
    attempt_request_document = _read_json(args.attempt_request) if getattr(args, "attempt_request", None) else None
    attempt_ledger_document = _read_json(args.attempt_ledger) if getattr(args, "attempt_ledger", None) else None
    request_document = _read_json(args.validation_request) if getattr(args, "validation_request", None) else None
    candidate_document = _read_json(args.candidate) if getattr(args, "candidate", None) else None
    receipt_document = _read_json(args.materialization_receipt) if getattr(args, "materialization_receipt", None) else None
    parent_document = _read_json(args.parent_agent_release) if getattr(args, "parent_agent_release", None) else None
    readiness_attestation_document = _read_json(args.readiness_attestation) if getattr(args, "readiness_attestation", None) else None
    e0_agent_release_document = _read_json(args.e0_agent_release) if getattr(args, "e0_agent_release", None) else None
    campaign_authorization_document = _read_json(args.campaign_authorization) if getattr(args, "campaign_authorization", None) else None
    campaign_document = _read_json(args.campaign) if getattr(args, "campaign", None) else None
    campaign_plan_document = _read_json(args.campaign_plan) if getattr(args, "campaign_plan", None) else None
    readiness_control_set_document = _read_json(args.readiness_control_set) if getattr(args, "readiness_control_set", None) else None
    compatibility_bridge_document = _read_json(args.r0_compatibility_bridge) if getattr(args, "r0_compatibility_bridge", None) else None
    if compatibility_bridge_document is not None:
        # Validate the on-disk bridge again at the point of use.  The argument
        # is a path, never a caller-supplied digest or profile selector.
        compatibility_bridge_document = load_r0_panel_bridge(args.r0_compatibility_bridge)
    campaign_admission = None
    if any(value is not None for value in (campaign_document, campaign_plan_document, readiness_control_set_document)):
        if any(value is None for value in (campaign_document, campaign_plan_document, readiness_control_set_document)):
            raise ValueError("campaign execution requires campaign, plan and readiness-control-set documents")
        from clawgym.campaign_admission import verify_reference_campaign_admission
        campaign_admission = verify_reference_campaign_admission(
            campaign_document=campaign_document,
            plan_document=campaign_plan_document,
            readiness_control_set=readiness_control_set_document,
            expected_clawgym_revision=args.clawgym_revision,
            expected_provider_revision=args.provider_revision,
            expected_environment_release_digest=environment_document.get("environment_release_digest"),
            expected_deployment_lock_digest=campaign_document.get("deployment_lock_digest"),
        )
    if campaign_authorization_document is not None:
        required_campaign = (candidate_document, trial_document, approval_document)
        if any(document is None for document in required_campaign):
            raise ValueError("campaign execution requires candidate, trial and approval documents")
        _verify_campaign_authorization(
            campaign_authorization_document,
            candidate_digest=candidate_document["candidate_digest"],
            trial_digest=trial_document["trial_digest"],
            approval_digest=approval_document["approval_record_digest"],
            case_id=run_document.get("case_id", trial_document.get("case_id")),
            seed=run_document.get("seed", trial_document.get("seed")),
            partition=trial_document.get("partition"),
            purpose=campaign_authorization_document.get("purpose"),
            campaign_digest=campaign_authorization_document.get("campaign_digest"),
            generation=campaign_authorization_document.get("generation"),
        )
    verify_release_revisions(agent_document, environment_document, args.provider_revision, compatibility_bridge_document)

    from sregym.conductor.conductor import Conductor, ConductorConfig
    from sregym.conductor.conductor_api import request_shutdown, run_api

    deployment_lock = load_deployment_lock(
        provider_root / "clawgym_overlay" / "deployment.wp4.lock.json"
    )
    if campaign_admission is not None and campaign_document.get("deployment_lock_digest") != deployment_lock_digest(deployment_lock):
        raise ValueError("campaign deployment lock does not match provider lock")
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
        if compatibility_bridge_document is not None:
            profile = resolve_r0_panel_profile(
                compatibility_bridge_document,
                agent_release=agent_document,
                manifest_root=manifest_root,
            )
        if agent_document.get("adapter_id") != profile["adapter_id"]:
            raise ValueError("AgentRelease does not identify the frozen reference adapter")
        expected_profile_digest = profile.get("profile_digest") or sha256_digest(profile)
        if agent_document.get("invocation_profile_digest") != expected_profile_digest:
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
            canonical_json_bytes({"schema_id": "clawgym.campaign_admission_receipt.v1", "campaign_digest": campaign_admission[0], "plan_digest": campaign_admission[1], "provider_revision": args.provider_revision, "clawgym_revision": args.clawgym_revision}),
            media_type="application/json",
        )
    try:
        if run_document.get("lane") == "agent_validation" and args.materialization_bundle:
            from clawgym.execution_bridge import execute_approved_trial
            required = (request_document, candidate_document, receipt_document, parent_document, approval_document, matrix_document, trial_document)
            if any(document is None for document in required):
                raise ValueError("materialized candidate execution requires the complete approved artifact chain")
            if attempt_request_document is None or attempt_ledger_document is None or not args.attempt_claim_root:
                raise ValueError("materialized candidate execution requires attempt request, ledger and claim root")
            claim = claim_attempt(
                root=args.attempt_claim_root,
                approval_record_digest=approval_document["approval_record_digest"],
                trial_digest=trial_document["trial_digest"],
                attempt_number=attempt_request_document["attempt_number"],
                attempt_id=attempt_request_document["execution_attempt_id"],
                runtime_reference=args.provider_revision,
            )
            sink.write_bytes("host/execution-attempt-claim.json", canonical_json_bytes(claim), media_type="application/json")
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
                attempt_request_document=attempt_request_document,
                attempt_ledger_document=attempt_ledger_document,
                attempt_claim_document=claim,
                readiness_attestation_document=readiness_attestation_document,
                e0_agent_release_document=e0_agent_release_document,
                expected_clawgym_revision=args.clawgym_revision,
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
    command.add_argument("--attempt-request")
    command.add_argument("--attempt-ledger")
    command.add_argument("--attempt-claim-root")
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
