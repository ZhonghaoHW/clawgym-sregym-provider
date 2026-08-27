"""SREGym-backed implementations of the ClawGym runtime provider interfaces."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

from clawgym.contracts import RunManifest
from clawgym.providers import (
    AgentInvocationResult,
    EvidencePayload,
    LifecycleOutcome,
    OracleEvaluation,
    ToolAccessGrant,
)


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def _run(coroutine):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coroutine)
    raise RuntimeError("SREGym provider calls require a synchronous host controller thread")


class ConductorPort(Protocol):
    config: Any
    problem_id: str | None
    waiting_for_agent: bool

    async def prepare_problem(self): ...

    def inject_problem_fault(self) -> Mapping[str, Any]: ...

    async def submit(self, solution: Any) -> Mapping[str, Any]: ...

    async def await_evaluation(self) -> Mapping[str, Any]: ...

    def recover_problem_fault(self) -> Mapping[str, Any]: ...

    def cleanup_problem(self) -> Mapping[str, Any]: ...

    def start_k8s_proxy(self) -> None: ...

    def stop_k8s_proxy(self) -> None: ...

    def get_agent_kubeconfig_path(self) -> str | None: ...


def _outcome(
    *,
    phase: str,
    provider_id: str,
    status: str,
    started_at: str,
    completed_at: str,
    run: RunManifest,
    summary: Mapping[str, Any],
    extra_evidence: tuple[EvidencePayload, ...] = (),
) -> LifecycleOutcome:
    return LifecycleOutcome(
        phase=phase,
        provider_id=provider_id,
        status=status,
        started_at=started_at,
        completed_at=completed_at,
        evidence=(
            EvidencePayload(
                artifact_key=f"runs/{run.manifest_digest}/sregym-{phase}.json",
                document={
                    "schema_id": "clawgym.sregym_lifecycle_evidence.v1",
                    "phase": phase,
                    "provider_id": provider_id,
                    "status": status,
                    "summary": dict(summary),
                },
            ),
        ) + extra_evidence,
    )


@dataclass(slots=True)
class SREGymEnvironmentProvider:
    conductor: ConductorPort
    immutable_configuration_digest: str
    problem_id: str
    task_stages: tuple[str, ...]
    phase_probe: Callable[[str], Mapping[str, Any]] | None = None
    clock: Callable[[], str] = _utc_now
    provider_id: str = field(default="sregym.environment.v1", init=False)
    provider_type: str = field(default="environment_provider", init=False)

    def __post_init__(self) -> None:
        self.conductor.problem_id = self.problem_id
        self.conductor.config.defer_cleanup = True
        self.conductor.config.task_stages = self.task_stages

    def _call(self, phase: str, run_manifest: RunManifest, operation) -> LifecycleOutcome:
        started_at = self.clock()
        result = operation()
        probe = self.phase_probe(phase) if self.phase_probe is not None else {"passed": True}
        if isinstance(probe, Mapping):
            probe = dict(probe)
            if probe.get("passed") is not True:
                probe.setdefault("reason", "postcondition_failed")
        else:
            probe = {"passed": False, "reason": "postcondition_failed"}
        completed_at = self.clock()
        status_value = str(result.get("status", "")) if isinstance(result, Mapping) else str(result)
        failed = probe["passed"] is not True or status_value in {"cleanup_failed", "failed", "not_loaded"} or status_value.startswith(
            "skipped"
        )
        return _outcome(
            phase=phase,
            provider_id=self.provider_id,
            status="failed" if failed else "succeeded",
            started_at=started_at,
            completed_at=completed_at,
            run=run_manifest,
            summary={
                "problem_id": self.problem_id,
                "result": status_value,
                "postconditions": dict(probe),
            },
        )

    def reset(self, run_manifest: RunManifest) -> LifecycleOutcome:
        return self._call(
            "reset", run_manifest, lambda: _run(self.conductor.prepare_problem())
        )

    def inject_fault(self, run_manifest: RunManifest) -> LifecycleOutcome:
        return self._call("fault", run_manifest, self.conductor.inject_problem_fault)

    def recover(self, run_manifest: RunManifest) -> LifecycleOutcome:
        return self._call("recovery", run_manifest, self.conductor.recover_problem_fault)

    def cleanup(self, run_manifest: RunManifest) -> LifecycleOutcome:
        return self._call("cleanup", run_manifest, self.conductor.cleanup_problem)


@dataclass(slots=True)
class SREGymOracleProvider:
    conductor: ConductorPort
    immutable_configuration_digest: str
    required_stages: tuple[str, ...]
    clock: Callable[[], str] = _utc_now
    attribution_capture: Callable[[RunManifest, str], Mapping[str, Any]] | None = None
    provider_id: str = field(default="sregym.oracle.v1", init=False)
    provider_type: str = field(default="oracle_provider", init=False)

    def _capture(self, run_manifest: RunManifest, phase: str) -> dict[str, Any]:
        if self.attribution_capture is not None:
            value = self.attribution_capture(run_manifest, phase)
            return dict(value) if isinstance(value, Mapping) else {}
        return {"captured_at": self.clock(), "conductor_stage": "unknown", "waiting_for_agent": self.conductor.waiting_for_agent, "policy_exists": False, "target_endpoint_ready": False}

    @staticmethod
    def _summarize(results: Mapping[str, Any], required_stages: tuple[str, ...]) -> dict[str, Any]:
        summary: dict[str, Any] = {}
        for stage in required_stages:
            value = results.get(stage.capitalize())
            if not isinstance(value, Mapping):
                summary[stage] = {"present": False}
                continue
            item: dict[str, Any] = {
                "present": True,
                "success": value.get("success") is True,
            }
            if isinstance(value.get("accuracy"), (int, float)):
                item["accuracy"] = value["accuracy"]
            if value.get("error"):
                item["error_type"] = "oracle_error"
            summary[stage] = item
        return summary

    def evaluate(self, run_manifest: RunManifest, submission: Any) -> OracleEvaluation:
        started_at = self.clock()
        pre = self._capture(run_manifest, "pre_oracle")
        if self.conductor.waiting_for_agent:
            _run(self.conductor.submit(submission))
        results = _run(self.conductor.await_evaluation())
        summary = self._summarize(results, self.required_stages)
        missing = any(not summary[stage]["present"] for stage in self.required_stages)
        errors = any(summary[stage].get("error_type") for stage in self.required_stages)
        if missing or errors:
            verdict = "error"
            status = "failed"
        elif all(summary[stage]["success"] for stage in self.required_stages):
            verdict = "pass"
            status = "succeeded"
        else:
            verdict = "fail"
            status = "succeeded"
        completed_at = self.clock()
        post = self._capture(run_manifest, "post_oracle")
        environment_release = getattr(run_manifest, "environment_release", None)
        environment_release_digest = getattr(
            environment_release, "environment_release_digest", ""
        )
        attribution = {
            "schema_id": "clawgym.sregym_oracle_attribution.v1",
            "run_manifest_digest": run_manifest.manifest_digest,
            "environment_release_digest": environment_release_digest,
            "phase": "oracle",
            "pre_oracle": pre,
            "oracle_result": {"completed_at": completed_at, "verdict": verdict, "stage": getattr(self.conductor, "stage", "unknown"), "summary_digest": hashlib.sha256(json.dumps(summary, sort_keys=True, separators=(",", ":")).encode()).hexdigest()},
            "post_oracle": post,
        }
        attribution["attribution_digest"] = hashlib.sha256(json.dumps(attribution, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        return OracleEvaluation(
            outcome=_outcome(
                phase="oracle",
                provider_id=self.provider_id,
                status=status,
                started_at=started_at,
                completed_at=completed_at,
                run=run_manifest,
                summary={"required_stages": list(self.required_stages), "results": summary},
                extra_evidence=(EvidencePayload(artifact_key=f"runs/{run_manifest.manifest_digest}/sregym-oracle-attribution.json", document=attribution),),
            ),
            verdict=verdict,
        )


@dataclass(frozen=True, slots=True)
class _SREGymAccessHandle:
    kubeconfig_path: str


@dataclass(slots=True)
class SREGymToolAccessProvider:
    conductor: ConductorPort
    immutable_configuration_digest: str
    interfaces: tuple[str, ...]
    capabilities: tuple[str, ...]
    denied_namespaces: tuple[str, ...]
    access_verifier: Callable[[str], Mapping[str, Any]] | None = None
    provider_id: str = field(default="sregym.filtered-tools.v1", init=False)
    provider_type: str = field(default="tool_access_provider", init=False)

    def grant(self, run_manifest: RunManifest) -> ToolAccessGrant:
        self.conductor.start_k8s_proxy()
        path = self.conductor.get_agent_kubeconfig_path()
        if not path:
            raise RuntimeError("SREGym filtering proxy did not produce an access handle")
        checks = self.access_verifier(path) if self.access_verifier is not None else {"passed": True}
        if not isinstance(checks, Mapping) or checks.get("passed") is not True:
            self.conductor.stop_k8s_proxy()
            raise RuntimeError("filtered Kubernetes access failed its host verification")
        return ToolAccessGrant(
            handle=_SREGymAccessHandle(path),
            evidence=(
                EvidencePayload(
                    artifact_key=f"runs/{run_manifest.manifest_digest}/sregym-tool-grant.json",
                    document={
                        "schema_id": "clawgym.sregym_tool_evidence.v1",
                        "provider_id": self.provider_id,
                        "status": "granted",
                        "interfaces": list(self.interfaces),
                        "capabilities": list(self.capabilities),
                        "denied_namespaces": list(self.denied_namespaces),
                        "access_checks": dict(checks),
                    },
                ),
            ),
        )

    def revoke(
        self, run_manifest: RunManifest, grant: ToolAccessGrant
    ) -> tuple[EvidencePayload, ...]:
        if not isinstance(grant.handle, _SREGymAccessHandle):
            raise RuntimeError("tool access handle was not issued by SREGym")
        self.conductor.stop_k8s_proxy()
        return (
            EvidencePayload(
                artifact_key=f"runs/{run_manifest.manifest_digest}/sregym-tool-revoke.json",
                document={
                    "schema_id": "clawgym.sregym_tool_evidence.v1",
                    "provider_id": self.provider_id,
                    "status": "revoked",
                },
            ),
        )


@dataclass(slots=True)
class SREGymObservationProvider:
    immutable_configuration_digest: str
    sources: tuple[str, ...]
    snapshotter: Callable[[], Mapping[str, Any]]
    provider_id: str = field(default="sregym.observation.v1", init=False)
    provider_type: str = field(default="observation_provider", init=False)

    def collect(self, run_manifest: RunManifest) -> tuple[EvidencePayload, ...]:
        snapshot = self.snapshotter()
        if not isinstance(snapshot, Mapping):
            raise RuntimeError("observation snapshot must be an object")
        if "capture_windows" in snapshot:
            return self._collect_causal(run_manifest, snapshot)
        summaries: dict[str, dict[str, Any]] = {}
        for source in self.sources:
            value = snapshot.get(source)
            if not isinstance(value, Mapping) or set(value) != {
                "status",
                "result_count",
                "summary_digest",
            }:
                raise RuntimeError(f"{source} snapshot has an invalid safe summary")
            if value["status"] not in {"success", "empty", "error"}:
                raise RuntimeError(f"{source} snapshot status is invalid")
            count = value["result_count"]
            if isinstance(count, bool) or not isinstance(count, int) or count < 0:
                raise RuntimeError(f"{source} snapshot result_count is invalid")
            digest = value["summary_digest"]
            if not isinstance(digest, str) or len(digest) != 64:
                raise RuntimeError(f"{source} snapshot digest is invalid")
            summaries[source] = dict(value)
        availability = "available" if any(
            item["status"] == "success" for item in summaries.values()
        ) else "empty"
        if any(item["status"] == "error" for item in summaries.values()):
            availability = "error"
        return (
            EvidencePayload(
                artifact_key=f"runs/{run_manifest.manifest_digest}/sregym-observations.json",
                document={
                    "schema_id": "clawgym.sregym_observation_evidence.v1",
                    "provider_id": self.provider_id,
                    "availability": availability,
                    "sources": summaries,
                },
            ),
        )

    def _collect_causal(
        self, run_manifest: RunManifest, snapshot: Mapping[str, Any]
    ) -> tuple[EvidencePayload, ...]:
        if set(snapshot) != {"capture_windows", "causal_transition"}:
            raise RuntimeError("causal observation snapshot has invalid fields")
        windows = snapshot["capture_windows"]
        transition = snapshot["causal_transition"]
        required_windows = ("baseline", "fault", "mitigation", "recovery")
        if not isinstance(windows, Mapping) or tuple(windows) != required_windows:
            raise RuntimeError("causal observation windows are incomplete or unordered")
        safe_windows: dict[str, Any] = {}
        query_error = False
        for window in required_windows:
            value = windows[window]
            if not isinstance(value, Mapping) or set(value) != {
                "window_started_at",
                "window_completed_at",
                "service_healthy",
                "sources",
            }:
                raise RuntimeError(f"{window} observation window is invalid")
            sources = value["sources"]
            if not isinstance(sources, Mapping) or set(sources) != set(self.sources):
                raise RuntimeError(f"{window} observation sources are invalid")
            safe_sources: dict[str, Any] = {}
            for source in self.sources:
                summary = sources[source]
                if not isinstance(summary, Mapping) or set(summary) != {
                    "status",
                    "result_count",
                    "summary_digest",
                }:
                    raise RuntimeError(f"{window}/{source} summary is invalid")
                if summary["status"] not in {"success", "empty", "error"}:
                    raise RuntimeError(f"{window}/{source} status is invalid")
                if summary["status"] == "error":
                    query_error = True
                safe_sources[source] = dict(summary)
            safe_windows[window] = {
                "window_started_at": value["window_started_at"],
                "window_completed_at": value["window_completed_at"],
                "service_healthy": value["service_healthy"] is True,
                "sources": safe_sources,
            }
        if not isinstance(transition, Mapping) or transition.get("passed") is not True:
            raise RuntimeError("causal service transition was not proven")
        if query_error:
            raise RuntimeError("causal telemetry contains a failed query")
        return (
            EvidencePayload(
                artifact_key=f"runs/{run_manifest.manifest_digest}/sregym-observations.json",
                document={
                    "schema_id": "clawgym.sregym_causal_observation_evidence.v1",
                    "provider_id": self.provider_id,
                    "availability": "available",
                    "capture_windows": safe_windows,
                    "causal_transition": dict(transition),
                },
            ),
        )


@dataclass(slots=True)
class SREGymExecutionBackend:
    immutable_configuration_digest: str
    timeout_seconds: int
    provider_id: str = field(default="sregym.container-execution.v1", init=False)
    provider_type: str = field(default="execution_backend", init=False)

    def execute(self, run_manifest, adapter, grant) -> AgentInvocationResult:
        result = adapter.invoke(run_manifest, grant.handle)
        if not isinstance(result, AgentInvocationResult):
            raise RuntimeError("AgentAdapter returned an invalid invocation result")
        if result.duration_ms > self.timeout_seconds * 1000:
            raise TimeoutError("agent invocation exceeded immutable execution timeout")
        return result


@dataclass(slots=True)
class SREGymEnvironmentValidationAdapter:
    """No-model adapter that removes exactly the selected NetworkPolicy."""

    immutable_configuration_digest: str
    delete_policy: Callable[[str, str, str], Mapping[str, Any]]
    namespace: str = "hotel-reservation"
    policy_name: str = "deny-all-recommendation"
    steady_state_probe: Callable[[], bool] | None = None
    telemetry_capture: Callable[[str, bool], Mapping[str, Any]] | None = None
    clock: Callable[[], str] = _utc_now
    provider_id: str = field(default="sregym.environment-validation.v1", init=False)
    provider_type: str = field(default="agent_adapter", init=False)

    def invoke(self, run_manifest: RunManifest, access_handle: object) -> AgentInvocationResult:
        if run_manifest.lane != "environment_validation":
            raise RuntimeError("environment validation adapter requires environment_validation lane")
        if not isinstance(access_handle, _SREGymAccessHandle):
            raise RuntimeError("environment validation adapter requires filtered SREGym access")
        started_at = self.clock()
        start = time.monotonic()
        result = self.delete_policy(
            access_handle.kubeconfig_path,
            self.namespace,
            self.policy_name,
        )
        service_healthy = (
            bool(self.steady_state_probe()) if self.steady_state_probe is not None else True
        )
        telemetry = (
            dict(self.telemetry_capture("mitigation", service_healthy))
            if self.telemetry_capture is not None
            else None
        )
        duration_ms = max(0, int((time.monotonic() - start) * 1000))
        status = (
            "succeeded"
            if isinstance(result, Mapping)
            and result.get("deleted") is True
            and service_healthy
            and (telemetry is None or telemetry.get("queries_succeeded") is True)
            else "failed"
        )
        completed_at = self.clock()
        return AgentInvocationResult(
            outcome=_outcome(
                phase="agent_invocation",
                provider_id=self.provider_id,
                status=status,
                started_at=started_at,
                completed_at=completed_at,
                run=run_manifest,
                summary={
                    "operation": "delete-network-policy",
                    "namespace": self.namespace,
                    "resource_name": self.policy_name,
                    "deleted": status == "succeeded",
                    "service_healthy": service_healthy,
                    "telemetry_window": telemetry,
                },
            ),
            submission={"operation": "delete-network-policy", "completed": status == "succeeded"},
            amount="0",
            currency="USD",
            duration_ms=duration_ms,
        )
