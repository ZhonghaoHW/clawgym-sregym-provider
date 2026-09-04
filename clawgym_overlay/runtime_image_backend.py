"""Typed Docker/Kind seam used by locked image materialization."""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager
from pathlib import Path
from types import TracebackType
from typing import Any, Protocol

from clawgym_overlay.locked_runtime import LockedRuntimeError


def validate_cluster_name(cluster_name: str) -> None:
    if not re.fullmatch(r"[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?", cluster_name):
        raise LockedRuntimeError("Kind cluster name is invalid")


def validate_node_inventory(nodes: tuple[str, ...]) -> tuple[str, tuple[str, ...]]:
    if not nodes or len(nodes) != len(set(nodes)):
        raise LockedRuntimeError("Kind node inventory is empty or duplicated")
    control_nodes = tuple(node for node in nodes if node.endswith("control-plane"))
    if len(control_nodes) != 1:
        raise LockedRuntimeError("Kind node inventory must contain one control plane")
    control = control_nodes[0]
    workers = tuple(node for node in nodes if node != control)
    return control, workers


class RuntimeImageBackend(Protocol):
    def discover_nodes(self, cluster_name: str) -> tuple[str, ...]: ...

    def ready(self, node: str, source: str) -> bool: ...

    def seed_host(self, artifact: Mapping[str, Any], source: str, nodes: tuple[str, ...]) -> bool: ...

    def remove(self, node: str, source: str) -> None: ...

    def pull(self, node: str, source: str, platform: str) -> None: ...

    def resolve_platform_digest(self, node: str, source: str, integrity: str, platform: str) -> str: ...

    def tag(self, node: str, source: str, target: str) -> None: ...

    def export(self, node: str, source: str, platform: str, archive: Path) -> None: ...

    def import_image(self, node: str, archive: Path, platform: str) -> None: ...


class TemporaryArchive(AbstractContextManager[Path]):
    """Exclusive temporary archive with guaranteed cleanup."""

    def __init__(self, *, prefix: str = "clawgym-node-image-") -> None:
        self._prefix = prefix
        self._path: Path | None = None

    def __enter__(self) -> Path:
        descriptor, name = tempfile.mkstemp(prefix=self._prefix, suffix=".tar")
        os.close(descriptor)
        self._path = Path(name)
        return self._path

    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        if self._path is not None:
            self._path.unlink(missing_ok=True)


class SubprocessRuntimeImageBackend:
    """Explicit Docker/Kind implementation; all host I/O is injectable."""

    def __init__(
        self,
        *,
        cluster_name: str,
        platform: str,
        runner: Callable[..., Any] = subprocess.run,
        ready_checker: Callable[[str, str], bool] | None = None,
        host_seeder: Callable[[Mapping[str, Any], str, tuple[str, ...]], bool] | None = None,
        platform_digest_resolver: Callable[[str, str, str, str], str] | None = None,
    ) -> None:
        self.cluster_name = cluster_name
        self.platform = platform
        self.runner = runner
        self.ready_checker = ready_checker
        self.host_seeder = host_seeder
        self.platform_digest_resolver = platform_digest_resolver

    def discover_nodes(self, cluster_name: str) -> tuple[str, ...]:
        completed = self.runner(
            ("kind", "get", "nodes", "--name", cluster_name),
            check=True,
            capture_output=True,
            text=True,
        )
        return tuple(line for line in completed.stdout.splitlines() if line)

    def ready(self, node: str, source: str) -> bool:
        if self.ready_checker is not None:
            return self.ready_checker(node, source)
        completed = self.runner(
            (
                "docker",
                "exec",
                "--privileged",
                node,
                "ctr",
                "--namespace=k8s.io",
                "images",
                "export",
                "--platform",
                self.platform,
                "-",
                source,
            ),
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return completed.returncode == 0

    def seed_host(self, artifact: Mapping[str, Any], source: str, nodes: tuple[str, ...]) -> bool:
        if self.host_seeder is not None:
            return self.host_seeder(artifact, source, nodes)
        target = _canonical_image_target(str(artifact["target"]))
        descriptor = self.runner(
            ("docker", "image", "inspect", "--format={{.Descriptor.digest}}", target),
            check=False,
            capture_output=True,
            text=True,
        )
        observed_digest = descriptor.stdout.strip()
        allowed_digests = {
            str(artifact["integrity"]),
            str(artifact.get("platform_integrity", artifact["integrity"])),
        }
        # A platform-constrained ``docker image save`` may expose the locked
        # linux/amd64 child digest through ``docker image inspect`` instead of
        # the multi-platform index digest.  Both are trusted only when they
        # are explicitly present in the lock artifact.
        if descriptor.returncode != 0 or observed_digest not in allowed_digests:
            return False
        # ``docker image save --platform`` imports the platform manifest into
        # containerd.  A multi-platform index digest (``integrity``) is not
        # necessarily present as an image reference after that import; the
        # node only has the locked linux/amd64 child digest.  Create both the
        # index-shaped source alias used by the preload state machine and the
        # human-readable target tag from that platform reference.
        platform_integrity = str(artifact.get("platform_integrity", artifact["integrity"]))
        platform_source = _image_reference_with_digest(source, platform_integrity)
        with TemporaryArchive(prefix="clawgym-host-image-") as archive:
            saved = self.runner(
                ("docker", "image", "save", "--platform", self.platform, "--output", str(archive), target),
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            if saved.returncode != 0:
                return False
            loaded = self.runner(
                ("kind", "load", "image-archive", "--name", self.cluster_name, str(archive)),
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            if loaded.returncode != 0:
                return False
            for node in nodes:
                # A platform manifest cannot be tagged with the digest of its
                # multi-platform index.  Keep the immutable platform digest
                # reference and the human-readable target tag instead.
                self.tag(node, target, platform_source)
        return True

    def remove(self, node: str, source: str) -> None:
        self.runner(
            ("docker", "exec", "--privileged", node, "ctr", "--namespace=k8s.io", "images", "remove", source),
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def _run(self, stage: str, command: tuple[str, ...], **kwargs: Any) -> None:
        try:
            self.runner(command, check=True, stderr=subprocess.DEVNULL, **kwargs)
        except (OSError, subprocess.CalledProcessError) as exc:
            raise LockedRuntimeError(f"failed to {stage} locked runtime image") from exc

    def pull(self, node: str, source: str, platform: str) -> None:
        self._run(
            "pull",
            (
                "docker",
                "exec",
                "--privileged",
                node,
                "ctr",
                "--namespace=k8s.io",
                "images",
                "pull",
                "--local",
                "--platform",
                platform,
                source,
            ),
            stdout=subprocess.DEVNULL,
        )

    def resolve_platform_digest(self, node: str, source: str, integrity: str, platform: str) -> str:
        from clawgym_overlay.materialize_lock import resolve_node_platform_digest

        if self.platform_digest_resolver is not None:
            return self.platform_digest_resolver(node, source, integrity, platform)
        return resolve_node_platform_digest(node, source, integrity, platform, runner=self.runner)

    def tag(self, node: str, source: str, target: str) -> None:
        self._run(
            "tag",
            (
                "docker",
                "exec",
                "--privileged",
                node,
                "ctr",
                "--namespace=k8s.io",
                "images",
                "tag",
                "--force",
                source,
                target,
            ),
            stdout=subprocess.DEVNULL,
        )

    def export(self, node: str, source: str, platform: str, archive: Path) -> None:
        with archive.open("wb") as output:
            self._run(
                "export",
                (
                    "docker",
                    "exec",
                    "--privileged",
                    node,
                    "ctr",
                    "--namespace=k8s.io",
                    "images",
                    "export",
                    "--platform",
                    platform,
                    "-",
                    source,
                ),
                stdout=output,
            )

    def import_image(self, node: str, archive: Path, platform: str) -> None:
        with archive.open("rb") as source:
            self._run(
                "import",
                (
                    "docker",
                    "exec",
                    "--privileged",
                    "-i",
                    node,
                    "ctr",
                    "--namespace=k8s.io",
                    "images",
                    "import",
                    "--platform",
                    platform,
                    "--digests",
                    "--snapshotter=overlayfs",
                    "-",
                ),
                stdin=source,
                stdout=subprocess.DEVNULL,
            )


def _canonical_image_target(reference: str) -> str:
    head = reference.split("/", maxsplit=1)[0]
    if "/" not in reference:
        result = f"docker.io/library/{reference}"
    elif "." not in head and ":" not in head and head != "localhost":
        result = f"docker.io/{reference}"
    else:
        result = reference
    tail = result.rsplit("/", maxsplit=1)[-1]
    if ":" not in tail and "@" not in tail:
        result = f"{result}:latest"
    return result


def _image_reference_with_digest(reference: str, digest: str) -> str:
    """Return ``reference`` with its immutable platform digest substituted."""

    if not reference or not digest.startswith("sha256:"):
        raise LockedRuntimeError("image reference is missing a platform digest")
    base = reference.split("@", maxsplit=1)[0]
    head, separator, tail = base.rpartition("/")
    if ":" in tail:
        tail = tail.split(":", maxsplit=1)[0]
    base = f"{head}{separator}{tail}"
    return f"{base}@{digest}"


def preload_images_with_backend(
    document: Mapping[str, Any],
    cluster_name: str,
    *,
    backend: RuntimeImageBackend,
    nodes: tuple[str, ...] | None,
    platform: str,
    sleeper: Callable[[float], None],
) -> dict[str, Any]:
    """Deterministically materialize all locked runtime images."""

    validate_cluster_name(cluster_name)
    if nodes is None:
        nodes = backend.discover_nodes(cluster_name)
    control, workers = validate_node_inventory(nodes)
    identities: list[str] = []
    for artifact in sorted(document["artifacts"], key=lambda item: item["name"]):
        if not artifact["name"].startswith("runtime-image."):
            continue
        source = artifact["source"].removeprefix("oci://")
        target = _canonical_image_target(artifact["target"])
        platform_integrity = str(artifact.get("platform_integrity", artifact["integrity"]))
        node_source = _image_reference_with_digest(source, platform_integrity)

        def run_step(stage: str, operation: Callable[[], None], *, artifact_name: str = artifact["name"]) -> None:
            try:
                operation()
            except LockedRuntimeError as exc:
                raise LockedRuntimeError(f"failed to {stage} locked runtime image: {artifact_name}") from exc

        if not backend.ready(control, node_source):
            backend.seed_host(artifact, source, nodes)
        with TemporaryArchive() as archive:
            if not backend.ready(control, node_source):
                backend.remove(control, node_source)
                for attempt in range(5):
                    try:
                        run_step("pull", lambda source=node_source: backend.pull(control, source, platform))
                        break
                    except LockedRuntimeError:
                        if attempt == 4:
                            raise
                        sleeper(min(30 * (2**attempt), 240))
            actual = backend.resolve_platform_digest(control, node_source, artifact["integrity"], platform)
            if actual != artifact["platform_integrity"]:
                raise LockedRuntimeError(f"locked runtime platform digest mismatch: {artifact['name']}")
            run_step("tag", lambda source=node_source, target=target: backend.tag(control, source, target))
            run_step("export", lambda source=node_source: backend.export(control, source, platform, archive))
            for node in workers:
                run_step("import", lambda node=node: backend.import_image(node, archive, platform))
                run_step("tag", lambda node=node, source=node_source, target=target: backend.tag(node, source, target))
        identities.append(f"{artifact['target']}:{artifact['integrity']}:{artifact['platform_integrity']}")
    return {
        "schema_id": "clawgym.sregym_preloaded_images.v1",
        "image_count": len(identities),
        "image_set_digest": __import__("hashlib").sha256("\n".join(identities).encode()).hexdigest(),
    }
