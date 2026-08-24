"""Live Kubernetes postconditions and safely summarized telemetry queries."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from clawgym.contracts import sha256_digest


@dataclass(slots=True)
class SREGymLivePhaseProbe:
    """Prove the exact network-policy slice state after every lifecycle phase."""

    conductor: Any
    expected_nodes: int = 4
    namespace: str = "hotel-reservation"
    policy_name: str = "deny-all-recommendation"

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

    def _connectivity_healthy(self) -> bool:
        return bool(self.conductor.current_problem.mitigation_oracle._run_recommendation_probe())

    def __call__(self, phase: str) -> Mapping[str, Any]:
        nodes_ready = self._nodes_ready()
        if phase == "reset":
            application_ready = self._application_ready()
            policy_present = self._policy_exists()
            connectivity_healthy = self._connectivity_healthy()
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
            passed = nodes_ready and not namespace_exists
            return {
                "passed": passed,
                "nodes_ready": nodes_ready,
                "application_namespace_absent": not namespace_exists,
                "fault_absent": True,
            }
        else:
            return {"passed": False, "reason": "unsupported_phase"}
        return {
            "passed": passed,
            "nodes_ready": nodes_ready,
            "application_ready": application_ready,
            "fault_present": policy_present,
            "connectivity_healthy": connectivity_healthy,
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
