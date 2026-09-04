from __future__ import annotations

import hashlib
from types import SimpleNamespace

import pytest
from test_deployment_lock import lock_fixture

from clawgym_overlay.locked_runtime import LockedRuntime, LockedRuntimeError


def runtime(tmp_path):
    document = lock_fixture()
    for artifact in document["artifacts"]:
        if artifact["kind"] in {"manifest", "chart"}:
            payload = artifact["name"].encode()
            (tmp_path / artifact["name"]).write_bytes(payload)
            artifact["integrity"] = "sha256:" + hashlib.sha256(payload).hexdigest()
    return LockedRuntime(document, tmp_path), document


def test_locked_runtime_configures_only_verified_assets_and_digest_images(tmp_path) -> None:
    locked, document = runtime(tmp_path)
    config = SimpleNamespace()
    locked.configure_conductor(config)
    assert config.metrics_server_manifest.endswith("metrics-server-manifest")
    assert config.openebs_manifest.endswith("openebs-manifest")
    assert all("@sha256:" in image for image in config.application_image_overrides.values())
    assert set(config.application_image_overrides) == {"runtime-image.application.recommendation"}
    expected_mcp = next(
        item["source"].removeprefix("oci://")
        for item in document["artifacts"]
        if item["name"] == "runtime-image.mcp-server"
    )
    expected_workload = next(
        item["source"].removeprefix("oci://")
        for item in document["artifacts"]
        if item["name"] == "runtime-image.workload"
    )
    assert config.mcp_image == expected_mcp
    assert config.workload_image == expected_workload

    loki = SimpleNamespace(helm_configs={}, promtail_chart_path=None)
    prometheus = SimpleNamespace(helm_configs={})
    locked.configure_services(SimpleNamespace(loki=loki, prometheus=prometheus))
    assert loki.helm_configs["remote_chart"] is True
    assert loki.promtail_chart_path.endswith("promtail-chart")
    assert prometheus.helm_configs["extra_args"] == [
        "--set",
        "kube-state-metrics.image.repository=kube-state-metrics/kube-state-metrics",
        "--set",
        "kube-state-metrics.image.tag=v2.9.2",
        "--atomic",
        "--wait",
        "--wait-for-jobs",
        "--timeout",
        "300s",
    ]
    assert prometheus.helm_configs["remote_chart"] is True
    assert prometheus.helm_configs["chart_path"].endswith("prometheus-chart")

    declared = {
        artifact["source"].removeprefix("oci://") for artifact in document["artifacts"] if artifact["kind"] == "image"
    }
    locked.verify_required_images(declared)
    with pytest.raises(LockedRuntimeError, match="absent"):
        locked.verify_required_images(declared | {"undeclared.example/image@sha256:" + "f" * 64})


def test_configure_services_adds_atomic_helm_fragments_idempotently(tmp_path) -> None:
    locked, _ = runtime(tmp_path)
    conductor = SimpleNamespace(
        loki=SimpleNamespace(helm_configs={}, promtail_chart_path=None),
        prometheus=SimpleNamespace(helm_configs={"extra_args": ["--set", "server.retention=1d"]}),
    )

    locked.configure_services(conductor)
    locked.configure_services(conductor)

    arguments = conductor.prometheus.helm_configs["extra_args"]
    assert arguments.count("--set") == 3
    assert arguments.count("kube-state-metrics.image.repository=kube-state-metrics/kube-state-metrics") == 1
    assert arguments.count("kube-state-metrics.image.tag=v2.9.2") == 1
    assert arguments.count("--atomic") == 1
    assert arguments.count("--wait") == 1
    assert arguments.count("--wait-for-jobs") == 1
    assert arguments.count("--timeout") == 1
    assert arguments.count("300s") == 1


def test_locked_runtime_rejects_cache_tamper(tmp_path) -> None:
    locked, _ = runtime(tmp_path)
    (tmp_path / "metrics-server-manifest").write_text("tampered")
    with pytest.raises(LockedRuntimeError, match="digest mismatch"):
        locked.cached_artifact("metrics-server-manifest")


def test_locked_runtime_rejects_extra_cache_content(tmp_path) -> None:
    document = lock_fixture()
    for artifact in document["artifacts"]:
        if artifact["kind"] in {"manifest", "chart"}:
            payload = artifact["name"].encode()
            (tmp_path / artifact["name"]).write_bytes(payload)
            artifact["integrity"] = "sha256:" + hashlib.sha256(payload).hexdigest()
    (tmp_path / "stale-download").write_text("not declared")
    with pytest.raises(LockedRuntimeError, match="exactly match"):
        LockedRuntime(document, tmp_path)


def test_cluster_inventory_compares_content_digests_without_exporting_identities(tmp_path) -> None:
    locked, document = runtime(tmp_path)
    declared = next(item for item in document["artifacts"] if item["name"].startswith("runtime-image."))
    status = SimpleNamespace(
        name="application",
        image_id=f"runtime@{declared['platform_integrity']}",
    )
    pod = SimpleNamespace(
        spec=SimpleNamespace(
            init_containers=[],
            containers=[SimpleNamespace(name="application", image=declared["target"])],
        ),
        status=SimpleNamespace(init_container_statuses=[], container_statuses=[status]),
    )
    core = SimpleNamespace(list_pod_for_all_namespaces=lambda: SimpleNamespace(items=[pod]))
    conductor = SimpleNamespace(kubectl=SimpleNamespace(core_v1_api=core))

    result = locked.cluster_image_inventory(conductor)

    assert result["passed"] is True
    assert result["observed_image_identity_count"] == 1
    assert "registry" not in str(result)


def test_cluster_inventory_accepts_index_digest_reported_by_containerd(tmp_path) -> None:
    locked, document = runtime(tmp_path)
    declared = next(item for item in document["artifacts"] if item["name"].startswith("runtime-image."))
    status = SimpleNamespace(
        name="application",
        image_id=f"runtime@{declared['integrity']}",
    )
    pod = SimpleNamespace(
        spec=SimpleNamespace(
            init_containers=[],
            containers=[SimpleNamespace(name="application", image=declared["target"])],
        ),
        status=SimpleNamespace(init_container_statuses=[], container_statuses=[status]),
    )
    core = SimpleNamespace(list_pod_for_all_namespaces=lambda: SimpleNamespace(items=[pod]))
    conductor = SimpleNamespace(kubectl=SimpleNamespace(core_v1_api=core))

    result = locked.cluster_image_inventory(conductor)

    assert result["passed"] is True


def test_cluster_inventory_accepts_bare_containerd_sha256(tmp_path) -> None:
    locked, document = runtime(tmp_path)
    declared = next(item for item in document["artifacts"] if item["name"].startswith("runtime-image."))
    status = SimpleNamespace(name="application", image_id=declared["platform_integrity"])
    pod = SimpleNamespace(
        spec=SimpleNamespace(
            init_containers=[], containers=[SimpleNamespace(name="application", image=declared["target"])]
        ),
        status=SimpleNamespace(init_container_statuses=[], container_statuses=[status]),
    )
    core = SimpleNamespace(list_pod_for_all_namespaces=lambda: SimpleNamespace(items=[pod]))
    result = locked.cluster_image_inventory(SimpleNamespace(kubectl=SimpleNamespace(core_v1_api=core)))
    assert result["passed"] is True


def test_cluster_inventory_accepts_kind_amd64_alias_for_bundled_image(tmp_path) -> None:
    locked, document = runtime(tmp_path)
    document["artifacts"].append(
        {
            "name": "kind-bundled-image.kube-apiserver",
            "kind": "image",
            "version": "v1",
            "source": "oci://kindest/node@sha256:" + "1" * 64,
            "integrity": "sha256:" + "1" * 64,
            "target": "registry.k8s.io/kube-apiserver:v1",
        }
    )
    status = SimpleNamespace(name="apiserver", image_id="sha256:" + "2" * 64)
    pod = SimpleNamespace(
        spec=SimpleNamespace(
            init_containers=[],
            containers=[SimpleNamespace(name="apiserver", image="registry.k8s.io/kube-apiserver-amd64:v1")],
        ),
        status=SimpleNamespace(init_container_statuses=[], container_statuses=[status]),
    )
    core = SimpleNamespace(list_pod_for_all_namespaces=lambda: SimpleNamespace(items=[pod]))
    result = locked.cluster_image_inventory(SimpleNamespace(kubectl=SimpleNamespace(core_v1_api=core)))
    assert result["passed"] is True


def test_cluster_inventory_accepts_kind_bundled_kube_proxy_tag(tmp_path) -> None:
    locked, _ = runtime(tmp_path)
    status = SimpleNamespace(name="kube-proxy", image_id="sha256:" + "f" * 64)
    pod = SimpleNamespace(
        spec=SimpleNamespace(
            init_containers=[], containers=[SimpleNamespace(name="kube-proxy", image="registry.k8s.io/kube-proxy:v1.35.0")]
        ),
        status=SimpleNamespace(init_container_statuses=[], container_statuses=[status]),
    )
    core = SimpleNamespace(list_pod_for_all_namespaces=lambda: SimpleNamespace(items=[pod]))
    result = locked.cluster_image_inventory(SimpleNamespace(kubectl=SimpleNamespace(core_v1_api=core)))
    assert result["passed"] is True
