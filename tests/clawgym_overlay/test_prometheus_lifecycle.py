from __future__ import annotations

from types import SimpleNamespace

from sregym.service.telemetry import prometheus as prometheus_module


def test_prometheus_creates_namespace_before_pvc_and_helm(monkeypatch, tmp_path) -> None:
    events: list[str] = []

    class FakeKubectl:
        core_v1_api = SimpleNamespace(read_namespace=lambda *, name: events.append(f"namespace-read:{name}"))

        def exec_command(self, command: str) -> str:
            assert "create namespace observe" in command
            events.append("namespace-created")
            return "configured"

    service = prometheus_module.Prometheus()
    service.pvc_config_file = str(tmp_path / "pvc.yaml")
    (tmp_path / "pvc.yaml").write_text("metadata:\n  name: prometheus-pvc\n")
    monkeypatch.setattr(prometheus_module, "KubeCtl", FakeKubectl)
    monkeypatch.setattr(service, "_is_prometheus_running", lambda: False)
    monkeypatch.setattr(service, "_wait_for_namespace_termination", lambda: events.append("termination-checked"))
    monkeypatch.setattr(service, "_delete_pvc", lambda: events.append("old-pvc-deleted"))
    monkeypatch.setattr(service, "_pvc_exists", lambda _: False)
    monkeypatch.setattr(service, "_apply_pvc", lambda: events.append("pvc-applied"))
    monkeypatch.setattr(prometheus_module.Helm, "uninstall", lambda **_: events.append("helm-uninstall"))
    monkeypatch.setattr(prometheus_module.Helm, "install", lambda **_: events.append("helm-install"))
    monkeypatch.setattr(
        prometheus_module.Helm,
        "assert_if_deployed",
        lambda _: events.append("helm-ready"),
    )

    service.deploy()

    assert events.index("namespace-created") < events.index("pvc-applied")
    assert events.index("pvc-applied") < events.index("helm-install")
    assert events[-1] == "helm-ready"
