from __future__ import annotations

from types import SimpleNamespace

from clawgym_overlay.live_checks import (
    SREGymCausalTelemetryRecorder,
    SREGymLivePhaseProbe,
    SREGymLiveTelemetrySnapshotter,
    SafeTelemetryQuery,
    build_kubernetes_telemetry_snapshotter,
)


class Missing(Exception):
    status = 404


class FakeCore:
    namespace_exists = True

    def list_node(self):
        condition = SimpleNamespace(type="Ready", status="True")
        return SimpleNamespace(
            items=[SimpleNamespace(status=SimpleNamespace(conditions=[condition])) for _ in range(4)]
        )

    def read_namespace(self, name):
        if not self.namespace_exists:
            raise Missing()
        return object()


class FakeApps:
    def list_namespaced_deployment(self, namespace):
        status = SimpleNamespace(ready_replicas=1, unavailable_replicas=0)
        spec = SimpleNamespace(replicas=1)
        return SimpleNamespace(items=[SimpleNamespace(status=status, spec=spec)])

    def list_deployment_for_all_namespaces(self):
        return SimpleNamespace(items=[])


class FakeNetwork:
    present = False

    def read_namespaced_network_policy(self, name, namespace):
        if not self.present:
            raise Missing()
        return object()


def phase_probe():
    core = FakeCore()
    network = FakeNetwork()
    oracle = SimpleNamespace(_run_recommendation_probe=lambda: not network.present)
    problem = SimpleNamespace(networking_v1=network, mitigation_oracle=oracle)
    conductor = SimpleNamespace(
        kubectl=SimpleNamespace(core_v1_api=core, apps_v1_api=FakeApps()),
        current_problem=problem,
    )
    return SREGymLivePhaseProbe(conductor), core, network


def test_live_phase_probe_distinguishes_healthy_fault_and_cleanup() -> None:
    probe, core, network = phase_probe()
    assert probe("reset")["passed"] is True
    network.present = True
    fault = probe("fault")
    assert fault["passed"] is True
    assert fault["connectivity_healthy"] is False
    network.present = False
    assert probe("recovery")["passed"] is True
    core.namespace_exists = False
    assert probe("cleanup")["passed"] is True


def test_reset_proves_the_declared_steady_state_window() -> None:
    probe, _, _ = phase_probe()
    sleeps = []
    probe.baseline_window_seconds = 10
    probe.baseline_sample_interval_seconds = 5
    probe.sleep = sleeps.append

    result = probe("reset")

    assert result["passed"] is True
    assert result["baseline_window_seconds"] == 10
    assert result["baseline_samples"] == 3
    assert sleeps == [5, 5]


def test_reset_requires_locked_runtime_image_inventory() -> None:
    probe, _, _ = phase_probe()
    probe.runtime_image_inventory = lambda: {
        "passed": True,
        "observed_image_identity_count": 3,
        "observed_image_set_digest": "a" * 64,
    }
    result = probe("reset")
    assert result["passed"] is True
    assert result["runtime_image_inventory"]["observed_image_identity_count"] == 3

    probe.runtime_image_inventory = lambda: {"passed": False}
    result = probe("reset")
    assert result["passed"] is False
    assert "runtime-image-inventory-invalid" in result["abort_reasons"]


def test_telemetry_snapshot_exports_only_count_status_and_digest() -> None:
    snapshotter = SREGymLiveTelemetrySnapshotter(
        (
            SafeTelemetryQuery("prometheus", lambda: {"data": {"result": [{"value": "raw"}]}}),
            SafeTelemetryQuery("loki", lambda: {"data": {"result": []}}),
            SafeTelemetryQuery("jaeger", lambda: {"data": [{"trace": "raw"}]}),
        )
    )
    result = snapshotter()
    assert result["prometheus"]["status"] == "success"
    assert result["loki"]["status"] == "empty"
    assert result["jaeger"]["result_count"] == 1
    assert "raw" not in str(result)


def test_telemetry_query_failure_is_distinct_from_empty() -> None:
    def fail():
        raise TimeoutError("not retained")

    snapshotter = SREGymLiveTelemetrySnapshotter(
        (
            SafeTelemetryQuery("prometheus", fail),
            SafeTelemetryQuery("loki", lambda: {"data": {"result": []}}),
            SafeTelemetryQuery("jaeger", lambda: {"data": []}),
        )
    )
    result = snapshotter()
    assert result["prometheus"]["status"] == "error"
    assert result["loki"]["status"] == "empty"


def test_causal_telemetry_requires_all_four_service_transitions() -> None:
    snapshotter = SREGymLiveTelemetrySnapshotter(
        (
            SafeTelemetryQuery("prometheus", lambda: {"data": {"result": []}}),
            SafeTelemetryQuery("loki", lambda: {"data": {"result": []}}),
            SafeTelemetryQuery("jaeger", lambda: {"data": []}),
        )
    )
    recorder = SREGymCausalTelemetryRecorder(
        snapshotter,
        clock=lambda: "2026-08-25T01:02:03Z",
    )
    for window, healthy in (
        ("baseline", True),
        ("fault", False),
        ("mitigation", True),
        ("recovery", True),
    ):
        result = recorder.capture(window, healthy)
        assert result["queries_succeeded"] is True
        assert set(result["sources"]) == {"prometheus", "loki", "jaeger"}
        assert "raw" not in str(result)
    evidence = recorder()
    assert evidence["causal_transition"]["passed"] is True
    assert tuple(evidence["capture_windows"]) == recorder.REQUIRED_WINDOWS


def test_kubernetes_telemetry_uses_query_params_not_encoded_proxy_paths() -> None:
    calls = []

    class ApiClient:
        def call_api(self, resource_path, method, path_params, query_params, headers, **kwargs):
            calls.append((resource_path, method, path_params, query_params, headers, kwargs))
            if path_params["path"] == "api/traces":
                return '{"data":[{"traceID":"not-exported"}]}'
            return '{"data":{"result":[{"value":"not-exported"}]}}'

    core = SimpleNamespace(api_client=ApiClient())
    conductor = SimpleNamespace(kubectl=SimpleNamespace(core_v1_api=core))

    result = build_kubernetes_telemetry_snapshotter(conductor)()

    assert all(item["status"] == "success" for item in result.values())
    assert "not-exported" not in str(result)
    assert len(calls) == 3
    assert all("?" not in call[0] and "?" not in call[2]["path"] for call in calls)
    assert calls[0][3] == [("query", "up")]
    assert calls[1][3] == [("query", '{namespace="hotel-reservation"}')]
    assert calls[2][3] == [("service", "frontend"), ("limit", "20")]
