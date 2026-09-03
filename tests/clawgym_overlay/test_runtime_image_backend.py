from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from clawgym_overlay.locked_runtime import LockedRuntimeError
from clawgym_overlay.runtime_image_backend import (
    SubprocessRuntimeImageBackend,
    TemporaryArchive,
    _canonical_image_target,
    preload_images_with_backend,
    validate_cluster_name,
    validate_node_inventory,
)


@pytest.mark.parametrize("name", ["", "Bad_Name", "-bad", "bad-", "1bad", "a" * 64])
def test_validate_cluster_name_is_closed(name: str) -> None:
    with pytest.raises(LockedRuntimeError):
        validate_cluster_name(name)


def test_validate_node_inventory_requires_one_control_plane() -> None:
    with pytest.raises(LockedRuntimeError, match="empty"):
        validate_node_inventory(())
    with pytest.raises(LockedRuntimeError, match="duplicated"):
        validate_node_inventory(("formal-control-plane", "formal-control-plane"))
    with pytest.raises(LockedRuntimeError, match="one control"):
        validate_node_inventory(("formal-worker",))
    assert validate_node_inventory(("formal-control-plane", "formal-worker")) == (
        "formal-control-plane",
        ("formal-worker",),
    )


def test_temporary_archive_is_removed_after_success_and_failure() -> None:
    path: Path
    with TemporaryArchive(prefix="test-image-") as path:
        path.write_bytes(b"fixture")
        assert path.is_file()
    assert not path.exists()


def test_temporary_archive_exit_without_enter_is_safe() -> None:
    archive = TemporaryArchive()
    archive.__exit__(None, None, None)
    with pytest.raises(RuntimeError), TemporaryArchive() as path:
        path.write_bytes(b"fixture")
        raise RuntimeError("boom")
    assert not path.exists()


def test_backend_discovery_and_default_readiness() -> None:
    calls: list[tuple[tuple[str, ...], dict[str, object]]] = []

    def runner(command: tuple[str, ...], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        calls.append((command, kwargs))
        if command[0] == "kind":
            return subprocess.CompletedProcess(command, 0, stdout="cluster-control-plane\ncluster-worker\n")
        return subprocess.CompletedProcess(command, 0)

    backend = SubprocessRuntimeImageBackend(
        cluster_name="cluster",
        platform="linux/amd64",
        runner=runner,
    )
    assert backend.discover_nodes("cluster") == ("cluster-control-plane", "cluster-worker")
    assert backend.ready("cluster-control-plane", "registry/image@sha256:" + "a" * 64)
    assert any(call[0][0:2] == ("docker", "exec") for call in calls)


def test_backend_pull_failure_is_typed() -> None:
    def runner(command: tuple[str, ...], **kwargs: object) -> None:
        raise subprocess.CalledProcessError(1, command)

    backend = SubprocessRuntimeImageBackend(
        cluster_name="cluster",
        platform="linux/amd64",
        runner=runner,
    )
    with pytest.raises(LockedRuntimeError, match="failed to pull"):
        backend.pull("cluster-control-plane", "registry/image@sha256:" + "a" * 64, "linux/amd64")

    def unavailable(_command: tuple[str, ...], **_kwargs: object) -> None:
        raise OSError("docker unavailable")

    unavailable_backend = SubprocessRuntimeImageBackend(
        cluster_name="cluster", platform="linux/amd64", runner=unavailable
    )
    with pytest.raises(LockedRuntimeError, match="failed to pull"):
        unavailable_backend.pull("cluster-control-plane", "registry/image@sha256:" + "a" * 64, "linux/amd64")


@pytest.mark.parametrize(
    ("reference", "expected"),
    [
        ("image", "docker.io/library/image:latest"),
        ("library/image", "docker.io/library/image:latest"),
        ("localhost/image", "localhost/image:latest"),
        ("registry.example/image:v1", "registry.example/image:v1"),
    ],
)
def test_canonical_image_target_is_deterministic(reference: str, expected: str) -> None:
    assert _canonical_image_target(reference) == expected


def test_default_host_seeder_rejects_unverified_host_image() -> None:
    backend = SubprocessRuntimeImageBackend(
        cluster_name="cluster",
        platform="linux/amd64",
        runner=lambda *_args, **_kwargs: subprocess.CompletedProcess([], 1, stdout=""),
    )
    assert not backend.seed_host(
        {"target": "image", "integrity": "sha256:" + "a" * 64},
        "registry/image",
        ("cluster-control-plane",),
    )


def test_default_host_seeder_loads_and_tags_verified_image() -> None:
    digest = "sha256:" + "a" * 64
    calls: list[tuple[str, ...]] = []

    def runner(command: tuple[str, ...], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        calls.append(command)
        if command[0:3] == ("docker", "image", "inspect"):
            return subprocess.CompletedProcess(command, 0, stdout=digest)
        return subprocess.CompletedProcess(command, 0)

    backend = SubprocessRuntimeImageBackend(cluster_name="cluster", platform="linux/amd64", runner=runner)
    assert backend.seed_host(
        {"target": "image", "integrity": digest},
        "registry/image",
        ("cluster-control-plane",),
    )
    assert any(command[0:3] == ("kind", "load", "image-archive") for command in calls)


def test_default_host_seeder_fails_closed_when_load_fails() -> None:
    digest = "sha256:" + "a" * 64

    def runner(command: tuple[str, ...], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        if command[0:3] == ("docker", "image", "inspect"):
            return subprocess.CompletedProcess(command, 0, stdout=digest)
        if command[0:3] == ("kind", "load", "image-archive"):
            return subprocess.CompletedProcess(command, 1)
        return subprocess.CompletedProcess(command, 0)

    backend = SubprocessRuntimeImageBackend(cluster_name="cluster", platform="linux/amd64", runner=runner)
    assert not backend.seed_host({"target": "image", "integrity": digest}, "registry/image", ("node",))


def test_default_host_seeder_fails_closed_when_save_fails() -> None:
    digest = "sha256:" + "a" * 64

    def runner(command: tuple[str, ...], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        if command[0:3] == ("docker", "image", "inspect"):
            return subprocess.CompletedProcess(command, 0, stdout=digest)
        if command[0:3] == ("docker", "image", "save"):
            return subprocess.CompletedProcess(command, 1)
        return subprocess.CompletedProcess(command, 0)

    backend = SubprocessRuntimeImageBackend(cluster_name="cluster", platform="linux/amd64", runner=runner)
    assert not backend.seed_host({"target": "image", "integrity": digest}, "registry/image", ("node",))


def test_default_platform_digest_resolver_uses_locked_materialize_path(monkeypatch: pytest.MonkeyPatch) -> None:
    expected = "sha256:" + "b" * 64
    monkeypatch.setattr(
        "clawgym_overlay.materialize_lock.resolve_node_platform_digest",
        lambda node, source, integrity, platform, runner: expected,
    )
    backend = SubprocessRuntimeImageBackend(
        cluster_name="cluster", platform="linux/amd64", runner=lambda *_a, **_k: None
    )
    assert backend.resolve_platform_digest("node", "source", "sha256:" + "c" * 64, "linux/amd64") == expected


class _PreloadFakeBackend:
    def __init__(self, *, failures: int = 0) -> None:
        self.failures = failures
        self.pull_calls = 0
        self.calls: list[str] = []

    def discover_nodes(self, _cluster: str) -> tuple[str, ...]:
        return ("cluster-control-plane", "cluster-worker")

    def ready(self, _node: str, _source: str) -> bool:
        return self.pull_calls > 0 and self.failures == 0

    def seed_host(self, _artifact, _source: str, _nodes: tuple[str, ...]) -> bool:
        self.calls.append("seed")
        return False

    def remove(self, _node: str, _source: str) -> None:
        self.calls.append("remove")

    def pull(self, _node: str, _source: str, _platform: str) -> None:
        self.pull_calls += 1
        self.calls.append("pull")
        if self.pull_calls <= self.failures:
            raise LockedRuntimeError("transient pull")

    def resolve_platform_digest(self, *_args: str) -> str:
        return "sha256:" + "d" * 64

    def tag(self, *_args: str) -> None:
        self.calls.append("tag")

    def export(self, *_args) -> None:
        self.calls.append("export")

    def import_image(self, *_args) -> None:
        self.calls.append("import")


def _preload_document() -> dict[str, object]:
    return {
        "artifacts": [
            {
                "name": "runtime-image.test",
                "source": "oci://registry/image@sha256:" + "e" * 64,
                "target": "registry/image:test",
                "integrity": "sha256:" + "e" * 64,
                "platform_integrity": "sha256:" + "d" * 64,
            }
        ]
    }


def test_preload_retries_transient_pull_and_cleans_archive() -> None:
    backend = _PreloadFakeBackend(failures=2)
    sleeps: list[float] = []
    receipt = preload_images_with_backend(
        _preload_document(),
        "cluster",
        backend=backend,
        nodes=None,
        platform="linux/amd64",
        sleeper=sleeps.append,
    )
    assert receipt["image_count"] == 1
    assert backend.pull_calls == 3
    assert sleeps == [30, 60]
    assert backend.calls.count("import") == 1


def test_preload_raises_after_pull_retry_budget() -> None:
    backend = _PreloadFakeBackend(failures=5)
    with pytest.raises(LockedRuntimeError, match="failed to pull"):
        preload_images_with_backend(
            _preload_document(),
            "cluster",
            backend=backend,
            nodes=None,
            platform="linux/amd64",
            sleeper=lambda _seconds: None,
        )


def test_preload_backend_discovers_nodes_and_rejects_empty_inventory() -> None:
    class EmptyBackend:
        def discover_nodes(self, _cluster: str) -> tuple[str, ...]:
            return ()

    with pytest.raises(LockedRuntimeError, match="empty"):
        preload_images_with_backend(
            {"artifacts": []},
            "cluster",
            backend=EmptyBackend(),
            nodes=None,
            platform="linux/amd64",
            sleeper=lambda _seconds: None,
        )
