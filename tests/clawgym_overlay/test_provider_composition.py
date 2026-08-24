from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest

from clawgym.artifacts import FilesystemArtifactSink
from clawgym.contracts import (
    AgentRelease,
    ProviderSelections,
    RunManifest,
    RuntimeReference,
    sha256_digest,
)
from clawgym.providers import (
    AgentInvocationResult,
    EvidencePayload,
    LifecycleOutcome,
    ProviderBinding,
    ProviderDefinition,
    ProviderRegistry,
)
from clawgym.runtime import LifecycleController
from clawgym_overlay.composition import register_sregym_providers
from clawgym_overlay.providers import (
    SREGymEnvironmentValidationAdapter,
    SREGymObservationProvider,
    SREGymOracleProvider,
)
from clawgym_overlay.providers.sregym import _SREGymAccessHandle
from clawgym_overlay.release import build_environment_release, load_release_manifests


ROOT = Path(__file__).resolve().parents[2]
NOW = "2026-08-24T12:00:00Z"


class FakeConductor:
    def __init__(self, *, oracle_success: bool = True) -> None:
        self.config = SimpleNamespace(defer_cleanup=False, task_stages=None)
        self.problem_id = None
        self.waiting_for_agent = True
        self.oracle_success = oracle_success
        self.results = {}
        self.calls = []
        self.access_path = None

    async def prepare_problem(self):
        self.calls.append("reset")
        return "success"

    def inject_problem_fault(self):
        self.calls.append("fault")
        return {"status": "injected", "problem_id": self.problem_id}

    async def submit(self, solution):
        self.calls.append("oracle")
        self.waiting_for_agent = False
        self.results = {"Mitigation": {"success": self.oracle_success, "submission": solution}}
        return {"status": "ok"}

    async def await_evaluation(self):
        return dict(self.results)

    def recover_problem_fault(self):
        self.calls.append("recovery")
        return {"status": "recovered", "problem_id": self.problem_id}

    def cleanup_problem(self):
        self.calls.append("cleanup")
        return {"status": "cleaned", "problem_id": self.problem_id}

    def start_k8s_proxy(self):
        self.calls.append("tool_grant")
        self.access_path = "/tmp/ephemeral-provider-kubeconfig"

    def stop_k8s_proxy(self):
        self.calls.append("tool_revoke")
        self.access_path = None

    def get_agent_kubeconfig_path(self):
        return self.access_path


@dataclass
class FakeAdapter:
    immutable_configuration_digest: str
    provider_id: str = "test-agent"
    provider_type: str = "agent_adapter"

    def invoke(self, run_manifest, access_handle) -> AgentInvocationResult:
        return AgentInvocationResult(
            outcome=LifecycleOutcome(
                phase="agent_invocation",
                provider_id=self.provider_id,
                status="succeeded",
                started_at=NOW,
                completed_at=NOW,
                evidence=(
                    EvidencePayload(
                        artifact_key=f"runs/{run_manifest.manifest_digest}/agent-invocation.json",
                        document={"status": "succeeded", "exit_code": 0},
                    ),
                ),
            ),
            submission={"agent_claimed_verdict": "fail", "action": "removed policy"},
            amount="0",
            currency="USD",
            duration_ms=10,
        )


class BoundSink(FilesystemArtifactSink):
    provider_id = "test-artifacts"
    provider_type = "artifact_sink"
    immutable_configuration_digest = sha256_digest({"sink": "wp3-test"})


def test_real_provider_classes_close_an_in_memory_episode(tmp_path: Path) -> None:
    manifests = load_release_manifests(ROOT / "clawgym_overlay" / "manifests")
    environment_release = build_environment_release(
        overlay_revision="a" * 40,
        manifests=manifests,
    )
    conductor = FakeConductor()
    registry = ProviderRegistry()
    bindings = register_sregym_providers(
        registry,
        conductor=conductor,
        manifests=manifests,
        snapshotter=lambda: {
            source: {
                "status": "empty",
                "result_count": 0,
                "summary_digest": sha256_digest({"source": source, "results": []}),
            }
            for source in ("prometheus", "loki", "jaeger")
        },
        phase_probe=lambda phase: {"passed": True, "phase": phase},
        access_verifier=lambda path: {"passed": True, "filtered": True},
    )
    assert len(bindings) == 5
    assert conductor.config.defer_cleanup is True
    assert conductor.config.task_stages == ("mitigation",)

    adapter = FakeAdapter(sha256_digest({"adapter": "test"}))
    sink = BoundSink(tmp_path)
    for implementation in (adapter, sink):
        registry.register_binding(
            ProviderBinding(
                ProviderDefinition(
                    implementation.provider_id,
                    implementation.provider_type,
                    implementation.immutable_configuration_digest,
                ),
                implementation,
            )
        )

    agent_release = AgentRelease.create(
        adapter_id=adapter.provider_id,
        runtime_reference=RuntimeReference("source_revision", "b" * 40),
        invocation_profile_digest=sha256_digest({"invocation": "test"}),
        tool_policy_profile_bundle_digest=sha256_digest({"tools": "test"}),
    )
    selections = ProviderSelections.from_dict(
        {
            "agent_adapter": adapter.provider_id,
            "environment_provider": "sregym.environment.v1",
            "oracle_provider": "sregym.oracle.v1",
            "tool_access_provider": "sregym.filtered-tools.v1",
            "execution_backend": "sregym.container-execution.v1",
            "observation_provider": "sregym.observation.v1",
            "artifact_sink": sink.provider_id,
        }
    )
    run = RunManifest.create(
        run_id="wp3-contract-run",
        lane="evaluation",
        seed=7,
        requested_at=NOW,
        requested_start_at=NOW,
        agent_release=agent_release,
        environment_release=environment_release,
        provider_selections=selections,
        registry=registry,
    )

    episode = LifecycleController(registry, clock=lambda: NOW).run_episode(
        episode_id="wp3-contract-episode",
        run_manifest=run,
    )

    assert episode.oracle_verdict.verdict == "pass"
    assert conductor.calls == [
        "reset",
        "fault",
        "tool_grant",
        "oracle",
        "recovery",
        "tool_revoke",
        "cleanup",
    ]
    episode.validate_against_run(run)
    json.dumps(episode.to_dict())
    retained_text = "\n".join(path.read_text() for path in tmp_path.rglob("*.json"))
    assert "/tmp/ephemeral-provider-kubeconfig" not in retained_text
    assert "agent_claimed_verdict" not in retained_text


def test_oracle_reports_error_when_required_host_result_is_missing() -> None:
    conductor = FakeConductor()
    conductor.waiting_for_agent = False
    provider = SREGymOracleProvider(
        conductor,
        sha256_digest({"oracle": "test"}),
        ("mitigation",),
        clock=lambda: NOW,
    )
    run = SimpleNamespace(manifest_digest="a" * 64)
    evaluation = provider.evaluate(run, {"agent_claimed_verdict": "pass"})
    assert evaluation.verdict == "error"
    assert evaluation.outcome.status == "failed"


def test_oracle_failure_overrides_agent_claimed_pass() -> None:
    conductor = FakeConductor(oracle_success=False)
    provider = SREGymOracleProvider(
        conductor,
        sha256_digest({"oracle": "test"}),
        ("mitigation",),
        clock=lambda: NOW,
    )
    run = SimpleNamespace(manifest_digest="a" * 64)
    evaluation = provider.evaluate(run, {"agent_claimed_verdict": "pass"})
    assert evaluation.verdict == "fail"
    assert evaluation.outcome.status == "succeeded"


def test_failed_phase_postcondition_fails_receipt() -> None:
    conductor = FakeConductor()
    manifests = load_release_manifests(ROOT / "clawgym_overlay" / "manifests")
    bindings = register_sregym_providers(
        ProviderRegistry(),
        conductor=conductor,
        manifests=manifests,
        snapshotter=lambda: {},
        phase_probe=lambda phase: {"passed": phase != "fault"},
    )
    environment = next(
        binding.implementation
        for binding in bindings
        if binding.definition.provider_type == "environment_provider"
    )
    run = SimpleNamespace(manifest_digest="a" * 64)
    assert environment.inject_fault(run).status == "failed"


def test_validation_adapter_is_no_model_and_lane_restricted() -> None:
    calls = []

    def delete_policy(path, namespace, name):
        calls.append((path, namespace, name))
        return {"deleted": True}

    adapter = SREGymEnvironmentValidationAdapter(
        sha256_digest({"adapter": "environment-validation"}),
        delete_policy,
        clock=lambda: NOW,
    )
    access = _SREGymAccessHandle("/temporary/filtered-kubeconfig")
    run = SimpleNamespace(lane="environment_validation", manifest_digest="a" * 64)

    result = adapter.invoke(run, access)

    assert result.amount == "0"
    assert isinstance(result.duration_ms, int)
    assert result.outcome.status == "succeeded"
    assert calls == [
        (
            "/temporary/filtered-kubeconfig",
            "hotel-reservation",
            "deny-all-recommendation",
        )
    ]
    with pytest.raises(RuntimeError, match="environment_validation"):
        adapter.invoke(SimpleNamespace(lane="evaluation", manifest_digest="a" * 64), access)


def test_causal_observation_provider_requires_complete_successful_transition() -> None:
    source_summary = {
        "status": "success",
        "result_count": 1,
        "summary_digest": "a" * 64,
    }
    windows = {
        window: {
            "window_started_at": NOW,
            "window_completed_at": NOW,
            "service_healthy": window != "fault",
            "sources": {
                source: dict(source_summary)
                for source in ("prometheus", "loki", "jaeger")
            },
        }
        for window in ("baseline", "fault", "mitigation", "recovery")
    }
    provider = SREGymObservationProvider(
        sha256_digest({"observation": "causal"}),
        ("prometheus", "loki", "jaeger"),
        lambda: {
            "capture_windows": windows,
            "causal_transition": {
                "baseline_healthy": True,
                "fault_observed": True,
                "mitigation_healthy": True,
                "recovery_healthy": True,
                "missing_windows": [],
                "passed": True,
            },
        },
    )
    run = SimpleNamespace(manifest_digest="a" * 64)
    evidence = provider.collect(run)[0].document
    assert evidence["availability"] == "available"
    assert evidence["causal_transition"]["passed"] is True

    windows["fault"]["sources"]["loki"]["status"] = "error"
    with pytest.raises(RuntimeError, match="failed query"):
        provider.collect(run)
