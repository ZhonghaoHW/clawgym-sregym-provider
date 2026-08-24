from __future__ import annotations

import hashlib
from types import SimpleNamespace

import pytest

from clawgym_overlay.locked_runtime import LockedRuntime, LockedRuntimeError
from test_deployment_lock import lock_fixture


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

    loki = SimpleNamespace(helm_configs={}, promtail_chart_path=None)
    locked.configure_services(SimpleNamespace(loki=loki))
    assert loki.helm_configs["remote_chart"] is False
    assert loki.promtail_chart_path.endswith("promtail-chart")

    declared = {
        artifact["source"].removeprefix("oci://")
        for artifact in document["artifacts"]
        if artifact["kind"] == "image"
    }
    locked.verify_required_images(declared)
    with pytest.raises(LockedRuntimeError, match="absent"):
        locked.verify_required_images(declared | {"undeclared.example/image@sha256:" + "f" * 64})


def test_locked_runtime_rejects_cache_tamper(tmp_path) -> None:
    locked, _ = runtime(tmp_path)
    (tmp_path / "metrics-server-manifest").write_text("tampered")
    with pytest.raises(LockedRuntimeError, match="digest mismatch"):
        locked.cached_artifact("metrics-server-manifest")
