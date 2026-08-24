from __future__ import annotations

import hashlib
import io
import subprocess

import pytest

from clawgym_overlay.locked_runtime import LockedRuntimeError
from clawgym_overlay.materialize_lock import materialize_assets, preload_runtime_images
from test_deployment_lock import lock_fixture


def asset_payloads(document):
    payloads = {}
    for artifact in document["artifacts"]:
        if artifact["kind"] in {"manifest", "chart"}:
            payload = f"locked:{artifact['name']}".encode()
            payloads[artifact["source"]] = payload
            artifact["integrity"] = "sha256:" + hashlib.sha256(payload).hexdigest()
    return payloads


def test_materialize_assets_requires_empty_cache_and_verifies_every_digest(tmp_path) -> None:
    document = lock_fixture()
    payloads = asset_payloads(document)

    result = materialize_assets(document, tmp_path, opener=lambda url: io.BytesIO(payloads[url]))

    assert result["asset_count"] == len(payloads)
    assert {path.name for path in tmp_path.iterdir()} == {
        item["name"] for item in document["artifacts"] if item["kind"] in {"manifest", "chart"}
    }
    with pytest.raises(LockedRuntimeError, match="empty"):
        materialize_assets(document, tmp_path, opener=lambda url: io.BytesIO(payloads[url]))


def test_materialize_assets_removes_partial_cache_after_digest_failure(tmp_path) -> None:
    document = lock_fixture()
    payloads = asset_payloads(document)
    first = next(iter(payloads))
    payloads[first] = b"tampered"

    with pytest.raises(LockedRuntimeError, match="digest mismatch"):
        materialize_assets(document, tmp_path, opener=lambda url: io.BytesIO(payloads[url]))

    assert list(tmp_path.iterdir()) == []


def test_preload_images_uses_only_locked_sources_and_declared_targets() -> None:
    document = lock_fixture()
    calls = []

    result = preload_runtime_images(
        document,
        "clawgym-formal",
        runner=lambda command, **kwargs: calls.append((command, kwargs)),
        nodes=("formal-control-plane", "formal-worker"),
        ready_checker=lambda node, source: False,
        host_seeder=lambda artifact, source, nodes: False,
    )

    images = [item for item in document["artifacts"] if item["name"].startswith("runtime-image.")]
    assert result["image_count"] == len(images)
    assert len(calls) == 6 * len(images)
    removals = calls[::6]
    non_removals = [call for index, call in enumerate(calls) if index % 6]
    assert all(call[1]["check"] is False for call in removals)
    assert all(call[1]["check"] is True for call in non_removals)
    assert all(call[0][7:8] == ("remove",) for call in removals)
    pulls = calls[1::6]
    assert all(call[0][0:3] == ("docker", "exec", "--privileged") for call in pulls)
    assert all(call[0][8:10] == ("--platform", "linux/amd64") for call in pulls)
    assert all("latest" not in call[0][10] for call in pulls)
    exports = calls[3::6]
    assert all(call[0][7:10] == ("export", "--platform", "linux/amd64") for call in exports)
    imports = calls[4::6]
    assert all(call[0][8:10] == ("import", "--platform") for call in imports)
    tags = calls[2::6] + calls[5::6]
    assert all(call[0][7:9] == ("tag", "--force") for call in tags)
    assert any(call[0][-1] == "docker.io/library/runtime-image.probe:latest" for call in tags)


def test_preload_failure_identifies_only_locked_artifact_and_stage() -> None:
    document = lock_fixture()

    def fail_load(command, **kwargs):
        if command[7:9] == ("tag", "--force"):
            raise subprocess.CalledProcessError(1, command)

    with pytest.raises(
        LockedRuntimeError,
        match=r"failed to tag locked runtime image: runtime-image\.",
    ):
        preload_runtime_images(
            document,
            "clawgym-formal",
            runner=fail_load,
            nodes=("formal-control-plane",),
            ready_checker=lambda node, source: False,
            host_seeder=lambda artifact, source, nodes: False,
        )


def test_preload_retries_transient_control_plane_pull() -> None:
    document = lock_fixture()
    attempts = 0
    sleeps = []

    def transient_pull(command, **kwargs):
        nonlocal attempts
        if command[7:10] == ("pull", "--platform", "linux/amd64"):
            attempts += 1
            if attempts < 3:
                raise subprocess.CalledProcessError(1, command)

    preload_runtime_images(
        document,
        "clawgym-formal",
        runner=transient_pull,
        nodes=("formal-control-plane",),
        sleeper=sleeps.append,
        ready_checker=lambda node, source: False,
        host_seeder=lambda artifact, source, nodes: False,
    )

    assert attempts >= 3
    assert sleeps[:2] == [30, 60]


def test_preload_reuses_complete_control_plane_content_without_registry_pull() -> None:
    document = lock_fixture()
    calls = []

    preload_runtime_images(
        document,
        "clawgym-formal",
        runner=lambda command, **kwargs: calls.append(command),
        nodes=("formal-control-plane",),
        ready_checker=lambda node, source: True,
        host_seeder=lambda artifact, source, nodes: False,
    )

    assert not any(command[7:8] == ("pull",) for command in calls)
    assert any(command[7:8] == ("export",) for command in calls)


def test_default_readiness_requires_exportable_platform_content() -> None:
    document = lock_fixture()
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        if command[7:8] == ("export",) and kwargs.get("check") is False:
            return subprocess.CompletedProcess(command, 1)
        return subprocess.CompletedProcess(command, 0)

    preload_runtime_images(
        document,
        "clawgym-formal",
        runner=run,
        nodes=("formal-control-plane",),
        host_seeder=lambda artifact, source, nodes: False,
        sleeper=lambda seconds: None,
    )

    assert any(command[7:8] == ("pull",) for command in calls)


def test_preload_can_seed_only_verified_host_content_before_pull() -> None:
    document = lock_fixture()
    seeded = []

    def seed(artifact, source, nodes):
        seeded.append((artifact["integrity"], source, nodes))
        return True

    preload_runtime_images(
        document,
        "clawgym-formal",
        runner=lambda command, **kwargs: None,
        nodes=("formal-control-plane",),
        ready_checker=lambda node, source: bool(seeded),
        host_seeder=seed,
    )

    assert seeded
    assert all(item[0].startswith("sha256:") for item in seeded)
