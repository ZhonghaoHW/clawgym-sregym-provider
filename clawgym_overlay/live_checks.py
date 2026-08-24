"""Live Kubernetes postconditions and safely summarized telemetry queries."""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from clawgym.contracts import sha256_digest


@dataclass(slots=True)
class SREGymLivePhaseProbe:
    """Prove the exact network-policy slice state after every lifecycle phase."""

    conductor: Any
    expected_nodes: int = 4
    namespace: str = "hotel-reservation"
    policy_name: str = "deny-all-recommendation"
    telemetry_capture: Callable[[str, bool], Mapping[str, Any]] | None = None
    baseline_window_seconds: int = 0
    baseline_sample_interval_seconds: int = 5
    sleep: Callable[[float], None] = time.sleep
    max_experiment_duration_seconds: int = 600
    monotonic: Callable[[], float] = time.monotonic
    _started_at: float | None = field(default=None, init=False)

    @staticmethod
    def _ready(condition_owner: Any) -> bool:
        return any(
            condition.type == "Ready" and condition.status == "True"
            for condition in (condition_owner.status.conditions or [])
        )

    def _nodes_ready(self) -> bool:
        nodes = self.conductor.kubectl.core_v1_api.list_node().items
        return len(nodes) == self.expected_nodes and all(self._ready(node) for node in nodes)

    def _application_ready(self) -> bool:
        deployments = self.conductor.kubectl.apps_v1_api.list_namespaced_deployment(
            self.namespace
        ).items
        if not deployments:
            return False
        return all(
            (item.status.ready_replicas or 0) == (item.spec.replicas or 1)
            and (item.status.unavailable_replicas or 0) == 0
            for item in deployments
        )

    def _policy_exists(self) -> bool:
        try:
            self.conductor.current_problem.networking_v1.read_namespaced_network_policy(
                self.policy_name,
                self.namespace,
            )
            return True
        except Exception as exc:
            if getattr(exc, "status", None) == 404:
                return False
            raise

    def _namespace_exists(self) -> bool:
        try:
            self.conductor.kubectl.core_v1_api.read_namespace(self.namespace)
            return True
        except Exception as exc:
            if getattr(exc, "status", None) == 404:
                return False
            raise

    def _non_target_impact(self) -> bool:
        deployments = self.conductor.kubectl.apps_v1_api.list_deployment_for_all_namespaces().items
        for item in deployments:
            if item.metadata.namespace == self.namespace:
                continue
            desired = item.spec.replicas or 1
            if (item.status.ready_replicas or 0) < desired:
                return True
        return False

    def _connectivity_healthy(self) -> bool:
        return bool(self.conductor.current_problem.mitigation_oracle._run_recommendation_probe())

    def _baseline_connectivity(self) -> tuple[bool, int]:
        samples = [self._connectivity_healthy()]
        remaining = self.baseline_window_seconds
        while remaining > 0:
            interval = min(self.baseline_sample_interval_seconds, remaining)
            self.sleep(interval)
            remaining -= interval
            samples.append(self._connectivity_healthy())
        return all(samples), len(samples)

    def __call__(self, phase: str) -> Mapping[str, Any]:
        if self._started_at is None:
            self._started_at = self.monotonic()
        nodes_ready = self._nodes_ready()
        non_target_impact = self._non_target_impact()
        duration_exceeded = (
            self.monotonic() - self._started_at > self.max_experiment_duration_seconds
        )
        if phase == "reset":
            application_ready = self._application_ready()
            policy_present = self._policy_exists()
            connectivity_healthy, baseline_samples = self._baseline_connectivity()
            passed = nodes_ready and application_ready and not policy_present and connectivity_healthy
        elif phase == "fault":
            application_ready = self._application_ready()
            policy_present = self._policy_exists()
            connectivity_healthy = self._connectivity_healthy()
            passed = nodes_ready and application_ready and policy_present and not connectivity_healthy
        elif phase == "recovery":
            application_ready = self._application_ready()
            policy_present = self._policy_exists()
            connectivity_healthy = self._connectivity_healthy()
            passed = nodes_ready and application_ready and not policy_present and connectivity_healthy
        elif phase == "cleanup":
            application_ready = False
            policy_present = False
            connectivity_healthy = False
            namespace_exists = self._namespace_exists()
            passed = nodes_ready and not namespace_exists and not non_target_impact
            return {
                "passed": passed,
                "nodes_ready": nodes_ready,
                "application_namespace_absent": not namespace_exists,
                "fault_absent": True,
                "non_target_impact": non_target_impact,
                "duration_exceeded": duration_exceeded,
            }
        else:
            return {"passed": False, "reason": "unsupported_phase"}
        telemetry = None
        capture_window = {"reset": "baseline", "fault": "fault", "recovery": "recovery"}.get(
            phase
        )
        if capture_window is not None and self.telemetry_capture is not None:
            telemetry = dict(self.telemetry_capture(capture_window, connectivity_healthy))
        telemetry_unavailable = telemetry is not None and telemetry.get("queries_succeeded") is not True
        abort_reasons = []
        if not nodes_ready:
            abort_reasons.append("kind-node-not-ready")
        if non_target_impact:
            abort_reasons.append("non-target-namespace-impact")
        if telemetry_unavailable:
            abort_reasons.append("telemetry-unavailable")
        if duration_exceeded:
            abort_reasons.append("max-experiment-duration-exceeded")
        passed = passed and not abort_reasons
        return {
            "passed": passed,
            "nodes_ready": nodes_ready,
            "application_ready": application_ready,
            "fault_present": policy_present,
            "connectivity_healthy": connectivity_healthy,
            "baseline_window_seconds": self.baseline_window_seconds if phase == "reset" else 0,
            "baseline_samples": baseline_samples if phase == "reset" else 0,
            "abort_reasons": abort_reasons,
            "telemetry_window": telemetry,
        }


@dataclass(frozen=True, slots=True)
class SafeTelemetryQuery:
    source: str
    query: Callable[[], Any]


class SREGymLiveTelemetrySnapshotter:
    """Run real queries but export only status, count, and content digest."""

    def __init__(self, queries: tuple[SafeTelemetryQuery, ...]) -> None:
        names = tuple(query.source for query in queries)
        if len(names) != len(set(names)) or set(names) != {"prometheus", "loki", "jaeger"}:
            raise ValueError("telemetry queries must identify Prometheus, Loki, and Jaeger once")
        self._queries = queries

    @staticmethod
    def _normalize(value: Any) -> Any:
        if isinstance(value, str):
            return json.loads(value)
        return value

    @staticmethod
    def _count(document: Any) -> int:
        if not isinstance(document, Mapping):
            return 0
        data = document.get("data")
        if isinstance(data, list):
            return len(data)
        if isinstance(data, Mapping):
            result = data.get("result")
            if isinstance(result, list):
                return len(result)
        return 0

    def __call__(self) -> Mapping[str, Any]:
        summaries: dict[str, Any] = {}
        for query in self._queries:
            try:
                document = self._normalize(query.query())
                count = self._count(document)
                summaries[query.source] = {
                    "status": "success" if count > 0 else "empty",
                    "result_count": count,
                    "summary_digest": sha256_digest(document),
                }
            except Exception as exc:
                summaries[query.source] = {
                    "status": "error",
                    "result_count": 0,
                    "summary_digest": sha256_digest(
                        {"source": query.source, "error_type": type(exc).__name__}
                    ),
                }
        return summaries


@dataclass(slots=True)
class SREGymCausalTelemetryRecorder:
    """Retain safe summaries for the four reviewed causal observation windows."""

    snapshotter: SREGymLiveTelemetrySnapshotter
    clock: Callable[[], str] = lambda: datetime.now(UTC).replace(microsecond=0).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    _windows: dict[str, dict[str, Any]] = field(default_factory=dict, init=False)

    REQUIRED_WINDOWS = ("baseline", "fault", "mitigation", "recovery")

    def capture(self, window: str, service_healthy: bool) -> Mapping[str, Any]:
        if window not in self.REQUIRED_WINDOWS or window in self._windows:
            raise RuntimeError("telemetry capture window is invalid or duplicated")
        window_started_at = self.clock()
        sources = dict(self.snapshotter())
        window_completed_at = self.clock()
        queries_succeeded = all(
            isinstance(value, Mapping) and value.get("status") in {"success", "empty"}
            for value in sources.values()
        )
        self._windows[window] = {
            "window_started_at": window_started_at,
            "window_completed_at": window_completed_at,
            "service_healthy": bool(service_healthy),
            "sources": sources,
        }
        return {
            "window": window,
            "queries_succeeded": queries_succeeded,
            "service_healthy": bool(service_healthy),
        }

    def __call__(self) -> Mapping[str, Any]:
        missing = [window for window in self.REQUIRED_WINDOWS if window not in self._windows]
        transition = {
            "baseline_healthy": self._windows.get("baseline", {}).get("service_healthy") is True,
            "fault_observed": self._windows.get("fault", {}).get("service_healthy") is False,
            "mitigation_healthy": self._windows.get("mitigation", {}).get("service_healthy") is True,
            "recovery_healthy": self._windows.get("recovery", {}).get("service_healthy") is True,
            "missing_windows": missing,
        }
        transition["passed"] = not missing and all(
            transition[key]
            for key in (
                "baseline_healthy",
                "fault_observed",
                "mitigation_healthy",
                "recovery_healthy",
            )
        )
        return {"capture_windows": dict(self._windows), "causal_transition": transition}


def verify_filtered_kubernetes_access(kubeconfig_path: str) -> Mapping[str, Any]:
    """Host-check the temporary filtered API without exporting its path or contents."""

    from kubernetes import client, config

    api_client = config.new_client_from_config(config_file=kubeconfig_path)
    core = client.CoreV1Api(api_client)
    namespaces = {item.metadata.name for item in core.list_namespace().items}
    pods = core.list_namespaced_pod("hotel-reservation").items
    workload_hidden = not any((pod.metadata.labels or {}).get("job") == "workload" for pod in pods)
    passed = "hotel-reservation" in namespaces and not ({"chaos-mesh", "khaos"} & namespaces) and workload_hidden
    return {
        "passed": passed,
        "target_namespace_visible": "hotel-reservation" in namespaces,
        "denied_namespaces_hidden": not bool({"chaos-mesh", "khaos"} & namespaces),
        "workload_hidden": workload_hidden,
    }


def delete_validation_network_policy(
    kubeconfig_path: str,
    namespace: str,
    policy_name: str,
) -> Mapping[str, Any]:
    """Delete exactly one policy through the filtered, non-admin kubeconfig."""

    from kubernetes import client, config

    api_client = config.new_client_from_config(config_file=kubeconfig_path)
    networking = client.NetworkingV1Api(api_client)
    try:
        networking.delete_namespaced_network_policy(policy_name, namespace)
    except Exception as exc:
        if getattr(exc, "status", None) != 404:
            raise
    try:
        networking.read_namespaced_network_policy(policy_name, namespace)
    except Exception as exc:
        if getattr(exc, "status", None) == 404:
            return {"deleted": True}
        raise
    return {"deleted": False}


def build_kubernetes_telemetry_snapshotter(conductor: Any) -> SREGymLiveTelemetrySnapshotter:
    """Bind three fixed read-only service-proxy queries to the host Kubernetes client."""

    core = conductor.kubectl.core_v1_api

    def service_query(name: str, path: str):
        return lambda: core.connect_get_namespaced_service_proxy_with_path(
            name=name,
            namespace="observe",
            path=path,
        )

    return SREGymLiveTelemetrySnapshotter(
        (
            SafeTelemetryQuery(
                "prometheus",
                service_query("prometheus-server:80", "api/v1/query?query=up"),
            ),
            SafeTelemetryQuery(
                "loki",
                service_query(
                    "loki-gateway:80",
                    "loki/api/v1/query?query=%7Bnamespace%3D%22hotel-reservation%22%7D",
                ),
            ),
            SafeTelemetryQuery(
                "jaeger",
                service_query("jaeger-out:16686", "api/traces?service=frontend&limit=20"),
            ),
        )
    )
