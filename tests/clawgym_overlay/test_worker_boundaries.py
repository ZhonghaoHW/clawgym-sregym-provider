from __future__ import annotations

import hashlib
import json
import runpy
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest
from clawgym.contracts import sha256_digest

from clawgym_overlay import worker, worker_admission
from clawgym_overlay.worker_admission import CampaignExecutionAdmission, ExecutionDocuments, validate_materialized_chain


def test_read_json_requires_object_and_reports_invalid_input(tmp_path: Path) -> None:
    valid = tmp_path / "valid.json"
    valid.write_text('{"ok": true}', encoding="utf-8")
    assert worker._read_json(valid) == {"ok": True}
    array = tmp_path / "array.json"
    array.write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError, match="JSON object"):
        worker._read_json(array)
    broken = tmp_path / "broken.json"
    broken.write_text("not json", encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        worker._read_json(broken)


def test_prepare_runtime_workdir_is_private_and_empty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import tempfile

    destination = tmp_path / "runtime"
    result = worker.prepare_runtime_workdir(destination)
    assert result == destination
    assert destination.stat().st_mode & 0o777 == 0o700
    assert tempfile.tempdir == str(destination)
    occupied = tmp_path / "occupied"
    occupied.mkdir()
    (occupied / "file").write_text("x", encoding="utf-8")
    with pytest.raises(ValueError, match="new empty"):
        worker.prepare_runtime_workdir(occupied)
    linked = tmp_path / "linked"
    linked.symlink_to(destination, target_is_directory=True)
    with pytest.raises(ValueError, match="new empty"):
        worker.prepare_runtime_workdir(linked)
    monkeypatch.delenv("TMPDIR", raising=False)


def test_prepare_runtime_workdir_accepts_existing_empty_directory_and_rejects_unwritable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    assert worker.prepare_runtime_workdir(empty) == empty
    monkeypatch.setattr(worker.os, "access", lambda *_args: False)
    with pytest.raises(ValueError, match="private and writable"):
        worker.prepare_runtime_workdir(tmp_path / "new")


def test_formal_topology_and_release_revision_guards(tmp_path: Path) -> None:
    root = tmp_path / "provider"
    overlay = root / "clawgym_overlay"
    overlay.mkdir(parents=True)
    topology = overlay / "kind.wp4.formal.yaml"
    topology.write_text("kind: Cluster\n", encoding="utf-8")
    digest = hashlib.sha256(topology.read_bytes()).hexdigest()
    assert worker.verify_formal_kind_topology(root, {"kind_topology_sha256": digest}) == topology
    with pytest.raises(ValueError, match="topology"):
        worker.verify_formal_kind_topology(root, {"kind_topology_sha256": "0" * 64})

    agent = {
        "agent_release_digest": "a" * 64,
        "runtime_reference": {"kind": "source_revision", "reference": "b" * 40},
    }
    environment = {"environment_release_digest": "c" * 64, "overlay_revision": "d" * 40}
    worker.verify_release_revisions(agent, environment, "b" * 40)
    with pytest.raises(ValueError, match="provider checkout"):
        worker.verify_release_revisions(agent, environment, "e" * 40)
    with pytest.raises(ValueError, match="EnvironmentRelease"):
        worker.verify_release_revisions(agent, {"overlay_revision": "bad"}, "b" * 40)


def test_release_revision_compatibility_bridge_is_narrow() -> None:
    bridge = {
        "r0_agent_release_digest": "a" * 64,
        "historical_provider_revision": "b" * 40,
        "historical_environment_overlay_revision": "c" * 40,
    }
    agent = {"agent_release_digest": "a" * 64, "runtime_reference": {"kind": "source_revision", "reference": "b" * 40}}
    environment = {"environment_release_digest": "d" * 64, "overlay_revision": "c" * 40}
    worker.verify_release_revisions(agent, environment, "e" * 40, bridge)

    wrong_agent = dict(agent, agent_release_digest="f" * 64)
    with pytest.raises(ValueError, match="scoped"):
        worker.verify_release_revisions(wrong_agent, environment, "e" * 40, bridge)
    wrong_runtime = dict(agent, runtime_reference={"kind": "source_revision", "reference": "f" * 40})
    with pytest.raises(ValueError, match="historical runtime"):
        worker.verify_release_revisions(wrong_runtime, environment, "e" * 40, bridge)
    wrong_environment = dict(environment, overlay_revision="f" * 40)
    with pytest.raises(ValueError, match="overlay"):
        worker.verify_release_revisions(agent, wrong_environment, "e" * 40, bridge)
    with pytest.raises(ValueError, match="current executable"):
        worker.verify_release_revisions(agent, environment, "b" * 40, bridge)


def test_campaign_authorization_wrapper_checks_digest_and_scope() -> None:
    document = {
        "schema_id": "agent_evolution.campaign_trial_authorization.v1",
        "campaign_digest": "a" * 64,
        "generation": 1,
        "purpose": "train",
        "case_id": "case-001",
        "seed": 2026083101,
        "partition": "train",
        "candidate_digest": "b" * 64,
        "trial_digest": "c" * 64,
        "approval_digest": "d" * 64,
        "execution_scope": "reference_family_only",
    }
    document["authorization_digest"] = sha256_digest(document)
    worker_admission.verify_campaign_authorization(
        document,
        candidate_digest="b" * 64,
        trial_digest="c" * 64,
        approval_digest="d" * 64,
        case_id="case-001",
        seed=2026083101,
        partition="train",
        purpose="train",
        campaign_digest="a" * 64,
        generation=1,
    )
    bad_scope = dict(document, execution_scope="automatic")
    bad_scope["authorization_digest"] = sha256_digest(
        {key: value for key, value in bad_scope.items() if key != "authorization_digest"}
    )
    with pytest.raises(ValueError, match="scope"):
        worker_admission.verify_campaign_authorization(
            bad_scope,
            candidate_digest="b" * 64,
            trial_digest="c" * 64,
            approval_digest="d" * 64,
            case_id="case-001",
            seed=2026083101,
            partition="train",
            purpose="train",
            campaign_digest="a" * 64,
            generation=1,
        )
    with pytest.raises(ValueError, match="trial identity"):
        worker_admission.verify_campaign_authorization(
            document,
            candidate_digest="b" * 64,
            trial_digest="c" * 64,
            approval_digest="d" * 64,
            case_id="case-002",
            seed=2026083101,
            partition="train",
            purpose="train",
            campaign_digest="a" * 64,
            generation=1,
        )


def test_campaign_authorization_propagates_approved_bridge_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """A bridge rejection must stop before worker/runtime startup."""
    import sys

    bridge = types.ModuleType("clawgym.execution_bridge")

    def reject(**_kwargs):
        raise ValueError("attempt claim already consumed")

    bridge.verify_campaign_trial_authorization = reject
    monkeypatch.setitem(sys.modules, "clawgym.execution_bridge", bridge)
    document = {"schema_id": "agent_evolution.campaign_trial_authorization.v1"}
    with pytest.raises(ValueError, match="already consumed"):
        worker_admission.verify_campaign_authorization(
            document,
            candidate_digest="a" * 64,
            trial_digest="b" * 64,
            approval_digest="c" * 64,
            case_id="case-001",
            seed=2026082701,
            partition="train",
            purpose="train",
        )


def test_binding_wraps_provider_identity() -> None:
    implementation = SimpleNamespace(
        provider_id="provider",
        provider_type="environment_provider",
        immutable_configuration_digest="a" * 64,
    )
    binding = worker._binding(implementation)
    assert binding.definition.provider_id == "provider"
    assert binding.implementation is implementation


def test_materialized_admission_requires_complete_chain_and_host_roots(tmp_path: Path) -> None:
    complete = tuple({"schema_id": f"fixture-{index}"} for index in range(10))

    def documents(*, lane: str, entries: tuple[dict[str, str] | None, ...]) -> ExecutionDocuments:
        return ExecutionDocuments(
            run={"run_id": "run-1", "lane": lane},
            agent={},
            environment={},
            validation_request=entries[0],
            candidate=entries[1],
            materialization_receipt=entries[2],
            parent_agent_release=entries[3],
            approval=entries[4],
            matrix=entries[5],
            trial=entries[6],
            attempt_request=entries[7],
            attempt_ledger=entries[8],
            environment_lease=entries[9],
        )

    assert (
        validate_materialized_chain(
            documents=documents(lane="environment_validation", entries=complete),
            materialization_bundle="bundle",
            attempt_claim_root=None,
            environment_lease_root=None,
        )
        is None
    )
    with pytest.raises(ValueError, match="complete approved artifact chain"):
        validate_materialized_chain(
            documents=documents(lane="agent_validation", entries=complete[:-1] + (None,)),
            materialization_bundle="bundle",
            attempt_claim_root=tmp_path / "claims",
            environment_lease_root=tmp_path / "leases",
        )
    with pytest.raises(ValueError, match="claim root"):
        validate_materialized_chain(
            documents=documents(lane="agent_validation", entries=complete),
            materialization_bundle="bundle",
            attempt_claim_root="",
            environment_lease_root=tmp_path / "leases",
        )
    with pytest.raises(ValueError, match="lease root"):
        validate_materialized_chain(
            documents=documents(lane="agent_validation", entries=complete),
            materialization_bundle="bundle",
            attempt_claim_root=tmp_path / "claims",
            environment_lease_root="",
        )
    assert (
        validate_materialized_chain(
            documents=documents(lane="agent_validation", entries=complete),
            materialization_bundle="bundle",
            attempt_claim_root=tmp_path / "claims",
            environment_lease_root=tmp_path / "leases",
        )
        is not None
    )


def test_execute_rejects_run_manifest_identity_before_runtime_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = tmp_path / "provider"
    clawgym = tmp_path / "clawgym"
    provider.mkdir()
    clawgym.mkdir()
    run_path = tmp_path / "run.json"
    run_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(worker, "verify_source_checkout", lambda *_args: None)
    args = SimpleNamespace(
        provider_checkout=str(provider),
        clawgym_checkout=str(clawgym),
        provider_revision="a" * 40,
        clawgym_revision="b" * 40,
        run_manifest=str(run_path),
        runtime_workdir=None,
        evidence_root=str(tmp_path / "evidence"),
        agent_release="missing",
        environment_release="missing",
        approval=None,
        matrix=None,
        trial=None,
        attempt_request=None,
        attempt_ledger=None,
        environment_lease=None,
        validation_request=None,
        candidate=None,
        materialization_receipt=None,
        parent_agent_release=None,
        readiness_attestation=None,
        e0_agent_release=None,
        campaign_authorization=None,
        campaign=None,
        campaign_plan=None,
        readiness_control_set=None,
        r0_compatibility_bridge=None,
        materialization_bundle=None,
        attempt_claim_root=None,
        environment_lease_root=None,
    )
    with pytest.raises(ValueError, match="run_id"):
        worker.execute(args)
    run_path.write_text('{"run_id": 1}', encoding="utf-8")
    with pytest.raises(ValueError, match="run_id"):
        worker.execute(args)


def test_execute_rejects_empty_run_id_after_explicit_workdir_setup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    args = _early_execute_args(tmp_path)
    Path(args.run_manifest).write_text('{"run_id": "", "lane": "agent_validation"}', encoding="utf-8")
    monkeypatch.setattr(worker, "verify_source_checkout", lambda *_args: None)
    with pytest.raises(ValueError, match="run_id"):
        worker.execute(args)


def test_execute_revalidates_explicit_r0_bridge_before_runtime_import(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    args = _early_execute_args(tmp_path, r0_compatibility_bridge=str(tmp_path / "bridge.json"))
    Path(args.r0_compatibility_bridge).write_text("{}", encoding="utf-8")
    monkeypatch.setattr(worker, "verify_source_checkout", lambda *_args: None)
    monkeypatch.setattr(
        worker,
        "load_r0_bridge",
        lambda _path: {
            "r0_agent_release_digest": "a" * 64,
            "historical_provider_revision": "b" * 40,
            "historical_environment_overlay_revision": "c" * 40,
        },
    )
    with pytest.raises((ImportError, ModuleNotFoundError, ValueError)):
        worker.execute(args)


def _early_execute_args(tmp_path: Path, **overrides: object) -> SimpleNamespace:
    provider = tmp_path / "provider"
    clawgym = tmp_path / "clawgym"
    provider.mkdir(exist_ok=True)
    clawgym.mkdir(exist_ok=True)
    run_path = tmp_path / "run.json"
    run_path.write_text('{"run_id": "run-1", "lane": "agent_validation"}', encoding="utf-8")
    agent_path = tmp_path / "agent.json"
    environment_path = tmp_path / "environment.json"
    agent_path.write_text("{}", encoding="utf-8")
    environment_path.write_text("{}", encoding="utf-8")
    values: dict[str, object] = {
        "provider_checkout": str(provider),
        "clawgym_checkout": str(clawgym),
        "provider_revision": "a" * 40,
        "clawgym_revision": "b" * 40,
        "run_manifest": str(run_path),
        "runtime_workdir": str(tmp_path / "runtime"),
        "evidence_root": str(tmp_path / "evidence"),
        "agent_release": str(agent_path),
        "environment_release": str(environment_path),
        "approval": None,
        "matrix": None,
        "trial": None,
        "attempt_request": None,
        "attempt_ledger": None,
        "environment_lease": None,
        "validation_request": None,
        "candidate": None,
        "materialization_receipt": None,
        "parent_agent_release": None,
        "readiness_attestation": None,
        "e0_agent_release": None,
        "campaign_authorization": None,
        "campaign": None,
        "campaign_plan": None,
        "readiness_control_set": None,
        "r0_compatibility_bridge": None,
        "materialization_bundle": None,
        "attempt_claim_root": None,
        "environment_lease_root": None,
        "agent_secret_file": None,
        "deployment_cache": str(tmp_path / "cache"),
        "episode_id": "episode-1",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_execute_rejects_partial_campaign_admission_before_runtime_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(worker, "verify_source_checkout", lambda *_args: None)
    args = _early_execute_args(tmp_path, campaign=str(tmp_path / "campaign.json"))
    Path(args.campaign).write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="campaign, plan and readiness-control-set"):
        worker.execute(args)


def test_execute_rejects_campaign_documents_with_missing_immutable_digests(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(worker, "verify_source_checkout", lambda *_args: None)
    paths = {name: tmp_path / f"{name}.json" for name in ("campaign", "campaign_plan", "readiness")}
    for path in paths.values():
        path.write_text("{}", encoding="utf-8")
    args = _early_execute_args(
        tmp_path,
        campaign=str(paths["campaign"]),
        campaign_plan=str(paths["campaign_plan"]),
        readiness_control_set=str(paths["readiness"]),
    )
    with pytest.raises(ValueError, match="immutable environment and lock digests"):
        worker.execute(args)


def test_execute_rejects_campaign_authorization_without_candidate_chain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(worker, "verify_source_checkout", lambda *_args: None)
    authorization = tmp_path / "authorization.json"
    authorization.write_text("{}", encoding="utf-8")
    args = _early_execute_args(tmp_path, campaign_authorization=str(authorization))
    with pytest.raises(ValueError, match="candidate, trial and approval"):
        worker.execute(args)


def test_worker_cli_requires_and_preserves_explicit_materialized_inputs() -> None:
    parsed = worker.parser().parse_args(
        [
            "execute",
            "--run-manifest",
            "run.json",
            "--agent-release",
            "agent.json",
            "--environment-release",
            "environment.json",
            "--evidence-root",
            "evidence",
            "--episode-id",
            "episode-1",
            "--clawgym-checkout",
            "clawgym",
            "--clawgym-revision",
            "a" * 40,
            "--provider-checkout",
            "provider",
            "--provider-revision",
            "b" * 40,
            "--deployment-cache",
            "cache",
            "--materialization-bundle",
            "bundle",
            "--attempt-request",
            "attempt-request.json",
            "--attempt-ledger",
            "attempt-ledger.json",
            "--attempt-claim-root",
            "claims",
            "--environment-lease",
            "lease.json",
            "--environment-lease-root",
            "leases",
        ]
    )
    assert parsed.materialization_bundle == "bundle"
    assert parsed.attempt_claim_root == "claims"
    assert parsed.environment_lease_root == "leases"


def test_execute_rejects_materialized_bundle_before_runtime_import(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(worker, "verify_source_checkout", lambda *_args: None)
    args = _early_execute_args(tmp_path, materialization_bundle=str(tmp_path / "bundle"))
    with pytest.raises(ValueError, match="complete approved artifact chain"):
        worker.execute(args)


def test_execute_rejects_execution_profile_lock_mismatch_before_runtime_import(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    args = _early_execute_args(tmp_path)
    Path(args.agent_release).write_text(
        json.dumps({"runtime_reference": {"kind": "source_revision", "reference": "a" * 40}}), encoding="utf-8"
    )
    Path(args.environment_release).write_text(json.dumps({"overlay_revision": "a" * 40}), encoding="utf-8")
    monkeypatch.setattr(worker, "verify_source_checkout", lambda *_args: None)
    monkeypatch.setattr(worker, "load_deployment_lock", lambda *_args: {"lock": True})
    monkeypatch.setattr(worker, "deployment_lock_digest", lambda _lock: "expected")
    monkeypatch.setattr(
        worker,
        "load_release_manifests",
        lambda _root: {"execution": {"deployment_lock_digest": "wrong", "kind_topology_sha256": "topology"}},
    )
    with pytest.raises(ValueError, match="execution profile"):
        worker.execute(args)


def test_execute_rejects_campaign_lock_mismatch_before_runtime_import(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    args = _early_execute_args(tmp_path)
    Path(args.agent_release).write_text(
        json.dumps({"runtime_reference": {"kind": "source_revision", "reference": "a" * 40}}), encoding="utf-8"
    )
    Path(args.environment_release).write_text(json.dumps({"overlay_revision": "a" * 40}), encoding="utf-8")
    monkeypatch.setattr(worker, "verify_source_checkout", lambda *_args: None)
    monkeypatch.setattr(worker, "load_deployment_lock", lambda *_args: {"lock": True})
    monkeypatch.setattr(worker, "deployment_lock_digest", lambda _lock: "expected")
    monkeypatch.setattr(
        worker,
        "validate_campaign_execution",
        lambda *_args, **_kwargs: CampaignExecutionAdmission(
            campaign={"deployment_lock_digest": "wrong"}, admission=("campaign", "plan")
        ),
    )
    with pytest.raises(ValueError, match="campaign deployment lock"):
        worker.execute(args)


def test_module_cli_help_is_a_safe_explicit_entrypoint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["clawgym-provider-worker", "--help"])
    monkeypatch.delitem(sys.modules, "clawgym_overlay.worker")
    with pytest.raises(SystemExit, match="0"):
        runpy.run_module("clawgym_overlay.worker", run_name="__main__")


def test_execute_legacy_environment_path_runs_through_typed_fakes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Exercise post-admission composition without a cluster or model."""
    provider = tmp_path / "provider"
    clawgym = tmp_path / "clawgym"
    (provider / "clawgym_overlay").mkdir(parents=True)
    clawgym.mkdir()
    run_path = tmp_path / "run.json"
    agent_path = tmp_path / "agent.json"
    environment_path = tmp_path / "environment.json"
    run_path.write_text(json.dumps({"run_id": "run-1", "lane": "environment_validation"}), encoding="utf-8")
    agent_path.write_text(
        json.dumps({"runtime_reference": {"kind": "source_revision", "reference": "a" * 40}}), encoding="utf-8"
    )
    environment_path.write_text(json.dumps({"overlay_revision": "d" * 40}), encoding="utf-8")
    run_path.write_text(json.dumps({"run_id": "run-1", "lane": "environment_validation"}), encoding="utf-8")
    args = _early_execute_args(
        tmp_path,
        provider_checkout=str(provider),
        clawgym_checkout=str(clawgym),
        run_manifest=str(run_path),
        agent_release=str(agent_path),
        environment_release=str(environment_path),
        runtime_workdir=str(tmp_path / "runtime"),
        evidence_root=str(tmp_path / "evidence"),
    )
    run_path.write_text(json.dumps({"run_id": "run-1", "lane": "environment_validation"}), encoding="utf-8")
    # _early_execute_args creates placeholder files; replace them with the
    # immutable identities required by the post-admission path.
    agent_path.write_text(
        json.dumps({"runtime_reference": {"kind": "source_revision", "reference": "a" * 40}}), encoding="utf-8"
    )
    environment_path.write_text(json.dumps({"overlay_revision": "d" * 40}), encoding="utf-8")

    monkeypatch.setattr(worker, "verify_source_checkout", lambda *_args: None)
    monkeypatch.setattr(
        worker,
        "validate_campaign_execution",
        lambda *_args, **_kwargs: CampaignExecutionAdmission(
            campaign={"deployment_lock_digest": "lock"}, admission=("campaign", "plan")
        ),
    )
    monkeypatch.setattr(worker, "verify_formal_kind_topology", lambda *_args: provider / "topology.yaml")
    monkeypatch.setattr(worker, "load_deployment_lock", lambda *_args: {"lock": True})
    monkeypatch.setattr(worker, "deployment_lock_digest", lambda _lock: "lock")
    monkeypatch.setattr(
        worker,
        "load_release_manifests",
        lambda _root: {
            "execution": {"deployment_lock_digest": "lock", "kind_topology_sha256": "topology"},
            "fault": {"steady_state": {"baseline_window_seconds": 1}, "max_experiment_duration_seconds": 10},
        },
    )
    monkeypatch.setattr(
        worker,
        "load_validation_profiles",
        lambda _root: (
            {"artifact_sink_id": "env", "namespace": "hotel-reservation", "resource_name": "deny-all-recommendation"},
            {"artifact_sink_id": "sink"},
        ),
    )

    class FakeRuntime:
        def __init__(self, lock, cache):
            self.lock, self.cache = lock, cache

        def configure_conductor(self, config):
            self.config = config

        def configure_services(self, conductor):
            self.conductor = conductor

        def cache_summary(self):
            return {"cache": "fake"}

        def cluster_image_inventory(self, _conductor):
            return {"images": []}

    monkeypatch.setattr(worker, "LockedRuntime", FakeRuntime)

    class FakeConductor:
        def __init__(self, _config):
            self.config = _config

        current_problem = SimpleNamespace(mitigation_oracle=SimpleNamespace(_run_recommendation_probe=lambda: True))

    class FakeConfig:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    api = types.ModuleType("sregym.conductor.conductor_api")
    api.run_api = lambda _conductor: None
    api.request_shutdown = lambda: None
    conductor_pkg = types.ModuleType("sregym.conductor")
    conductor_pkg.__path__ = []
    conductor_pkg.conductor_api = api
    conductor_impl = types.ModuleType("sregym.conductor.conductor")
    conductor_impl.Conductor = FakeConductor
    conductor_impl.ConductorConfig = FakeConfig
    sregym_pkg = types.ModuleType("sregym")
    sregym_pkg.__path__ = []
    monkeypatch.setitem(sys.modules, "sregym", sregym_pkg)
    monkeypatch.setitem(sys.modules, "sregym.conductor", conductor_pkg)
    monkeypatch.setitem(sys.modules, "sregym.conductor.conductor", conductor_impl)

    class FakeTelemetry:
        def __init__(self, snapshotter):
            self.snapshotter = snapshotter

        def capture(self, phase):
            return {"phase": phase}

    class FakeProbe:
        def __init__(self, *args, **kwargs):
            self.args, self.kwargs = args, kwargs

    class FakeAdapter:
        provider_id = "sregym"
        provider_type = "environment_provider"
        immutable_configuration_digest = "a" * 64

        def __init__(self, *args, **kwargs):
            self.args, self.kwargs = args, kwargs

        def invoke(self, *_args, **_kwargs):
            return None

    monkeypatch.setattr(worker, "build_reference_adapter", lambda **_kwargs: FakeAdapter())

    class FakeSink:
        provider_id = "sink"
        provider_type = "artifact_sink"
        immutable_configuration_digest = "c" * 64

        def __init__(self, *args, **kwargs):
            self.files = {}

        def write_bytes(self, name, payload, *, media_type):
            self.files[name] = (payload, media_type)

    class FakeRegistry:
        def __init__(self):
            self.bindings = []

        def register_binding(self, binding):
            self.bindings.append(binding)

    class FakeRun:
        lane = "environment_validation"
        seed = 1
        manifest_digest = "e" * 64

        @classmethod
        def from_dict(cls, document, *, registry):
            assert document["run_id"] == "run-1"
            assert len(registry.bindings) == 2
            return cls()

    monkeypatch.setattr(worker, "SREGymCausalTelemetryRecorder", FakeTelemetry)
    monkeypatch.setattr(worker, "build_kubernetes_telemetry_snapshotter", lambda _conductor: object())
    monkeypatch.setattr(worker, "SREGymLivePhaseProbe", FakeProbe)
    monkeypatch.setattr(worker, "register_sregym_providers", lambda *args, **kwargs: None)
    monkeypatch.setattr(worker, "verify_filtered_kubernetes_access", lambda *_args: True)
    monkeypatch.setattr(worker, "capture_oracle_attribution", lambda *_args: {})
    monkeypatch.setattr(worker, "delete_validation_network_policy", lambda *_args: None)
    monkeypatch.setattr(worker, "SREGymEnvironmentValidationAdapter", FakeAdapter)
    monkeypatch.setattr(worker, "ProviderRegistry", FakeRegistry)
    monkeypatch.setattr(worker, "RetainedArtifactSink", FakeSink)
    monkeypatch.setattr(worker, "RunManifest", FakeRun)
    monkeypatch.setattr(
        worker,
        "execute_worker",
        lambda **_kwargs: SimpleNamespace(bundle_digest="f" * 64, episode_digest="e" * 64),
    )

    worker.execute(args)
    assert "bundle_digest" in capsys.readouterr().out


def test_execute_materialized_agent_path_uses_claim_and_approved_bridge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Exercise the complete host-owned admission path without SREGym/model side effects."""
    provider = tmp_path / "provider"
    clawgym = tmp_path / "clawgym"
    (provider / "clawgym_overlay").mkdir(parents=True)
    clawgym.mkdir()
    run_path = tmp_path / "run.json"
    agent_path = tmp_path / "agent.json"
    environment_path = tmp_path / "environment.json"
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    run_path.write_text(
        json.dumps({"run_id": "run-agent", "lane": "agent_validation", "seed": 2026082701}), encoding="utf-8"
    )
    profile = {
        "adapter_id": "sregym.reference-agent",
        "profile_digest": "p" * 64,
        "model_id": "openai/deepseek-v4-pro",
        "api_base": "https://gateway.invalid/v1",
        "artifact_id": "network_policy_block",
        "command": ["python", "-m", "reference_driver_r1f"],
    }
    agent_path.write_text(
        json.dumps(
            {
                "agent_release_digest": "a" * 64,
                "runtime_reference": {"kind": "source_revision", "reference": "a" * 40},
                "adapter_id": profile["adapter_id"],
                "invocation_profile_digest": profile["profile_digest"],
            }
        ),
        encoding="utf-8",
    )
    environment_path.write_text(json.dumps({"overlay_revision": "d" * 40}), encoding="utf-8")
    secret = tmp_path / "agent-secret"
    secret.write_text("test-only", encoding="utf-8")
    secret.chmod(0o600)
    names = (
        "validation_request",
        "candidate",
        "materialization_receipt",
        "parent_agent_release",
        "approval",
        "matrix",
        "trial",
        "attempt_request",
        "attempt_ledger",
        "environment_lease",
    )
    docs = {name: tmp_path / f"{name}.json" for name in names}
    payloads = {
        "validation_request": {"schema_id": "validation-request"},
        "candidate": {"candidate_digest": "b" * 64},
        "materialization_receipt": {"receipt_digest": "c" * 64},
        "parent_agent_release": {"agent_release_digest": "e" * 64},
        "approval": {"approval_record_digest": "f" * 64},
        "matrix": {"matrix_digest": "1" * 64},
        "trial": {"trial_digest": "2" * 64, "partition": "validation", "case_id": "case-001", "seed": 2026082701},
        "attempt_request": {
            "attempt_request_digest": "3" * 64,
            "attempt_number": 1,
            "execution_attempt_id": "attempt-1",
        },
        "attempt_ledger": {"schema_id": "attempt-ledger"},
        "environment_lease": {"lease_digest": "4" * 64},
    }
    for name, path in docs.items():
        path.write_text(json.dumps(payloads[name]), encoding="utf-8")
    args = _early_execute_args(
        tmp_path,
        provider_checkout=str(provider),
        clawgym_checkout=str(clawgym),
        run_manifest=str(run_path),
        agent_release=str(agent_path),
        environment_release=str(environment_path),
        materialization_bundle=str(bundle),
        agent_secret_file=str(secret),
        validation_request=str(docs["validation_request"]),
        candidate=str(docs["candidate"]),
        materialization_receipt=str(docs["materialization_receipt"]),
        parent_agent_release=str(docs["parent_agent_release"]),
        approval=str(docs["approval"]),
        matrix=str(docs["matrix"]),
        trial=str(docs["trial"]),
        attempt_request=str(docs["attempt_request"]),
        attempt_ledger=str(docs["attempt_ledger"]),
        environment_lease=str(docs["environment_lease"]),
        attempt_claim_root=str(tmp_path / "claims"),
        environment_lease_root=str(tmp_path / "leases"),
    )
    # The shared early-argument helper creates placeholders; restore the
    # immutable identities needed by the post-admission path.
    run_path.write_text(
        json.dumps({"run_id": "run-agent", "lane": "agent_validation", "seed": 2026082701}), encoding="utf-8"
    )
    agent_path.write_text(
        json.dumps(
            {
                "agent_release_digest": "a" * 64,
                "runtime_reference": {"kind": "source_revision", "reference": "a" * 40},
                "adapter_id": profile["adapter_id"],
                "invocation_profile_digest": profile["profile_digest"],
            }
        ),
        encoding="utf-8",
    )
    environment_path.write_text(json.dumps({"overlay_revision": "d" * 40}), encoding="utf-8")

    monkeypatch.setattr(worker, "verify_source_checkout", lambda *_args: None)
    monkeypatch.setattr(worker, "verify_formal_kind_topology", lambda *_args: provider / "topology.yaml")
    monkeypatch.setattr(worker, "load_deployment_lock", lambda *_args: {"lock": True})
    monkeypatch.setattr(worker, "deployment_lock_digest", lambda _lock: "lock")
    monkeypatch.setattr(
        worker,
        "load_release_manifests",
        lambda _root: {
            "execution": {"deployment_lock_digest": "lock", "kind_topology_sha256": "topology"},
            "fault": {"steady_state": {"baseline_window_seconds": 1}, "max_experiment_duration_seconds": 10},
        },
    )
    monkeypatch.setattr(
        worker,
        "load_validation_profiles",
        lambda _root: (
            {"artifact_sink_id": "env", "namespace": "hotel-reservation", "resource_name": "deny-all-recommendation"},
            {"artifact_sink_id": "sink"},
        ),
    )
    monkeypatch.setattr(worker, "load_materialized_reference_profile", lambda *_args, **_kwargs: profile)

    class FakeRuntime:
        def __init__(self, lock, cache):
            self.lock, self.cache = lock, cache

        def configure_conductor(self, config):
            self.config = config

        def configure_services(self, conductor):
            self.conductor = conductor

        def cache_summary(self):
            return {"cache": "fake"}

        def cluster_image_inventory(self, _conductor):
            return {"images": []}

    monkeypatch.setattr(worker, "LockedRuntime", FakeRuntime)

    class FakeConductor:
        def __init__(self, _config):
            self.config = _config

        current_problem = SimpleNamespace(mitigation_oracle=SimpleNamespace(_run_recommendation_probe=lambda: True))

    class FakeConfig:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    api = types.ModuleType("sregym.conductor.conductor_api")
    api.run_api = lambda _conductor: None
    api.request_shutdown = lambda: None
    conductor_pkg = types.ModuleType("sregym.conductor")
    conductor_pkg.__path__ = []
    conductor_pkg.conductor_api = api
    conductor_impl = types.ModuleType("sregym.conductor.conductor")
    conductor_impl.Conductor = FakeConductor
    conductor_impl.ConductorConfig = FakeConfig
    sregym_pkg = types.ModuleType("sregym")
    sregym_pkg.__path__ = []
    monkeypatch.setitem(sys.modules, "sregym", sregym_pkg)
    monkeypatch.setitem(sys.modules, "sregym.conductor", conductor_pkg)
    monkeypatch.setitem(sys.modules, "sregym.conductor.conductor", conductor_impl)

    class FakeTelemetry:
        def __init__(self, snapshotter):
            self.snapshotter = snapshotter

        def capture(self, phase):
            return {"phase": phase}

    class FakeProbe:
        def __init__(self, *args, **kwargs):
            self.args, self.kwargs = args, kwargs

    class FakeAdapter:
        provider_id = "sregym.reference-agent"
        provider_type = "agent_adapter"
        immutable_configuration_digest = "a" * 64

        def __init__(self, *args, **kwargs):
            self.args, self.kwargs = args, kwargs

        def invoke(self, *_args, **_kwargs):
            return None

    monkeypatch.setattr(worker, "build_reference_adapter", lambda **_kwargs: FakeAdapter())

    class FakeSink:
        provider_id = "sink"
        provider_type = "artifact_sink"
        immutable_configuration_digest = "c" * 64

        def __init__(self, *args, **kwargs):
            self.files = {}

        def write_bytes(self, name, payload, *, media_type):
            self.files[name] = (payload, media_type)

    class FakeRegistry:
        def __init__(self):
            self.bindings = []

        def register_binding(self, binding):
            self.bindings.append(binding)

    class FakeRun:
        lane = "agent_validation"
        seed = 2026082701
        manifest_digest = "e" * 64

        @classmethod
        def from_dict(cls, document, *, registry):
            assert document["run_id"] == "run-agent"
            assert len(registry.bindings) == 2
            return cls()

    monkeypatch.setattr(worker, "SREGymCausalTelemetryRecorder", FakeTelemetry)
    monkeypatch.setattr(worker, "build_kubernetes_telemetry_snapshotter", lambda _conductor: object())
    monkeypatch.setattr(worker, "SREGymLivePhaseProbe", FakeProbe)
    monkeypatch.setattr(worker, "register_sregym_providers", lambda *args, **kwargs: None)
    monkeypatch.setattr(worker, "verify_filtered_kubernetes_access", lambda *_args: True)
    monkeypatch.setattr(worker, "capture_oracle_attribution", lambda *_args: {})
    monkeypatch.setattr(worker, "delete_validation_network_policy", lambda *_args: None)
    monkeypatch.setattr(worker, "SREGymReferenceAgentAdapter", FakeAdapter)
    monkeypatch.setattr(worker, "ProviderRegistry", FakeRegistry)
    monkeypatch.setattr(worker, "RetainedArtifactSink", FakeSink)
    monkeypatch.setattr(worker, "RunManifest", FakeRun)
    monkeypatch.setattr(
        worker,
        "SafeStratusRunner",
        lambda **kwargs: SimpleNamespace(profile=kwargs["profile"], secret_file=kwargs["secret_file"]),
    )

    import clawgym.execution_bridge as bridge

    lifecycle_v2 = types.ModuleType("clawgym.lifecycle_v2")
    lifecycle_v2.claim_attempt_v2 = lambda **kwargs: {
        "schema_id": "clawgym.execution_attempt_claim.v2",
        "claim_digest": "9" * 64,
        **kwargs,
    }
    monkeypatch.setitem(sys.modules, "clawgym.lifecycle_v2", lifecycle_v2)
    called: dict[str, object] = {}

    def execute_approved_trial(**kwargs):
        called.update(kwargs)
        return SimpleNamespace(bundle_digest="f" * 64, episode_digest="e" * 64)

    monkeypatch.setattr(bridge, "execute_approved_trial", execute_approved_trial)
    worker.execute(args)
    assert called["attempt_claim_document"]["schema_id"] == "clawgym.execution_attempt_claim.v2"
    assert "bundle_digest" in capsys.readouterr().out
