"""Materialize an immutable WP4 deployment cache and preload locked images."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import tempfile
import time
import urllib.request
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, BinaryIO

from clawgym_overlay.deployment_lock import load_deployment_lock, validate_deployment_lock
from clawgym_overlay.locked_runtime import LockedRuntime, LockedRuntimeError


def materialize_assets(
    document: Mapping[str, Any],
    cache_root: str | Path,
    *,
    opener: Callable[[str], BinaryIO] = urllib.request.urlopen,
) -> dict[str, Any]:
    """Populate a caller-created empty directory from checksum-locked HTTPS assets."""

    validate_deployment_lock(document)
    root = Path(cache_root)
    if not root.exists() or not root.is_dir() or root.is_symlink():
        raise LockedRuntimeError("deployment cache must be an existing regular directory")
    root = root.resolve(strict=True)
    if any(root.iterdir()):
        raise LockedRuntimeError("formal deployment cache must be empty before materialization")
    created: list[Path] = []
    try:
        for artifact in sorted(document["artifacts"], key=lambda item: item["name"]):
            if artifact["kind"] not in {"manifest", "chart"}:
                continue
            destination = root / artifact["name"]
            temporary = root / f".{artifact['name']}.partial"
            created.append(temporary)
            digest = hashlib.sha256()
            with opener(artifact["source"]) as source, temporary.open("xb") as output:
                while chunk := source.read(1024 * 1024):
                    digest.update(chunk)
                    output.write(chunk)
                output.flush()
                os.fsync(output.fileno())
            actual = f"sha256:{digest.hexdigest()}"
            if actual != artifact["integrity"]:
                raise LockedRuntimeError(f"downloaded deployment asset digest mismatch: {artifact['name']}")
            os.chmod(temporary, 0o600)
            os.replace(temporary, destination)
            created.append(destination)
        return LockedRuntime(document, root).cache_summary()
    except Exception:
        for path in reversed(created):
            path.unlink(missing_ok=True)
        raise


def preload_runtime_images(
    document: Mapping[str, Any],
    cluster_name: str,
    *,
    runner: Callable[..., Any] = subprocess.run,
    nodes: tuple[str, ...] | None = None,
    sleeper: Callable[[float], None] = time.sleep,
    ready_checker: Callable[[str, str], bool] | None = None,
    host_seeder: Callable[[Mapping[str, Any], str, tuple[str, ...]], bool] | None = None,
) -> dict[str, Any]:
    """Pull each locked runtime digest directly into every Kind node."""

    validate_deployment_lock(document)
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,62}", cluster_name):
        raise LockedRuntimeError("Kind cluster name is invalid")
    identities: list[str] = []
    platform = document["platform"].replace("-", "/", 1)
    if nodes is None:
        completed = runner(
            ("kind", "get", "nodes", "--name", cluster_name),
            check=True,
            capture_output=True,
            text=True,
        )
        nodes = tuple(line for line in completed.stdout.splitlines() if line)
    if not nodes or len(nodes) != len(set(nodes)):
        raise LockedRuntimeError("Kind node inventory is empty or duplicated")
    control_nodes = tuple(node for node in nodes if node.endswith("control-plane"))
    if len(control_nodes) != 1:
        raise LockedRuntimeError("Kind node inventory must contain one control plane")
    control_node = control_nodes[0]
    worker_nodes = tuple(node for node in nodes if node != control_node)
    if ready_checker is None:
        def ready_checker(node: str, source: str) -> bool:
            completed = subprocess.run(
                (
                    "docker", "exec", "--privileged", node,
                    "ctr", "--namespace=k8s.io", "images", "check", "--quiet",
                    f"name=={source}",
                ),
                check=False,
                capture_output=True,
                text=True,
            )
            return completed.returncode == 0 and source in completed.stdout.splitlines()

    def canonical_target(reference: str) -> str:
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

    if host_seeder is None:
        def host_seeder(
            artifact: Mapping[str, Any],
            source: str,
            node_names: tuple[str, ...],
        ) -> bool:
            target = artifact["target"]
            descriptor = subprocess.run(
                ("docker", "image", "inspect", "--format={{.Descriptor.digest}}", target),
                check=False,
                capture_output=True,
                text=True,
            )
            if descriptor.returncode != 0 or descriptor.stdout.strip() != artifact["integrity"]:
                return False
            archive_descriptor, archive_name = tempfile.mkstemp(
                prefix="clawgym-host-image-", suffix=".tar"
            )
            os.close(archive_descriptor)
            archive = Path(archive_name)
            try:
                saved = subprocess.run(
                    (
                        "docker", "image", "save", "--platform", platform,
                        "--output", str(archive), target,
                    ),
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                if saved.returncode != 0:
                    return False
                loaded = subprocess.run(
                    (
                        "kind", "load", "image-archive", "--name", cluster_name,
                        str(archive),
                    ),
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                if loaded.returncode != 0:
                    return False
                normalized_target = canonical_target(target)
                for node in node_names:
                    tagged = subprocess.run(
                        (
                            "docker", "exec", "--privileged", node,
                            "ctr", "--namespace=k8s.io", "images", "tag",
                            "--force", normalized_target, source,
                        ),
                        check=False,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                    if tagged.returncode != 0:
                        return False
                return True
            finally:
                archive.unlink(missing_ok=True)

    for artifact in sorted(document["artifacts"], key=lambda item: item["name"]):
        if not artifact["name"].startswith("runtime-image."):
            continue
        source = artifact["source"].removeprefix("oci://")
        target = canonical_target(artifact["target"])
        if not ready_checker(control_node, source):
            host_seeder(artifact, source, nodes)
        descriptor, archive_name = tempfile.mkstemp(prefix="clawgym-node-image-", suffix=".tar")
        os.close(descriptor)
        archive = Path(archive_name)

        def execute(
            stage: str,
            command: tuple[str, ...],
            *,
            attempts: int = 1,
            **kwargs: Any,
        ) -> None:
            for attempt in range(attempts):
                try:
                    runner(
                        command,
                        check=True,
                        stderr=subprocess.DEVNULL,
                        **kwargs,
                    )
                    return
                except subprocess.CalledProcessError as exc:
                    if attempt + 1 == attempts:
                        raise LockedRuntimeError(
                            f"failed to {stage} locked runtime image: {artifact['name']}"
                        ) from exc
                    sleeper(min(30 * (2**attempt), 240))

        try:
            if not ready_checker(control_node, source):
                execute(
                    "pull",
                    (
                        "docker", "exec", "--privileged", control_node,
                        "ctr", "--namespace=k8s.io", "images", "pull",
                        "--platform", platform, source,
                    ),
                    attempts=5,
                    stdout=subprocess.DEVNULL,
                )
            execute(
                "tag",
                (
                    "docker", "exec", "--privileged", control_node,
                    "ctr", "--namespace=k8s.io", "images", "tag",
                    "--force", source, target,
                ),
                stdout=subprocess.DEVNULL,
            )
            with archive.open("wb") as output:
                execute(
                    "export",
                    (
                        "docker", "exec", "--privileged", control_node,
                        "ctr", "--namespace=k8s.io", "images", "export",
                        "--platform", platform, "-", source,
                    ),
                    stdout=output,
                )
            for node in worker_nodes:
                with archive.open("rb") as source_archive:
                    execute(
                        "import",
                        (
                            "docker", "exec", "--privileged", "-i", node,
                            "ctr", "--namespace=k8s.io", "images", "import",
                            "--platform", platform, "--digests",
                            "--snapshotter=overlayfs", "-",
                        ),
                        stdin=source_archive,
                        stdout=subprocess.DEVNULL,
                    )
                execute(
                    "tag",
                    (
                        "docker", "exec", "--privileged", node,
                        "ctr", "--namespace=k8s.io", "images", "tag",
                        "--force", source, target,
                    ),
                    stdout=subprocess.DEVNULL,
                )
        finally:
            archive.unlink(missing_ok=True)
        identities.append(f"{artifact['target']}:{artifact['integrity']}")
    return {
        "schema_id": "clawgym.sregym_preloaded_images.v1",
        "image_count": len(identities),
        "image_set_digest": hashlib.sha256("\n".join(identities).encode()).hexdigest(),
    }


def main() -> None:
    parser = argparse.ArgumentParser(prog="clawgym-materialize-lock")
    parser.add_argument("--lock", required=True)
    subcommands = parser.add_subparsers(dest="command", required=True)
    assets = subcommands.add_parser("assets")
    assets.add_argument("--cache-root", required=True)
    images = subcommands.add_parser("images")
    images.add_argument("--cluster-name", required=True)
    arguments = parser.parse_args()
    document = load_deployment_lock(arguments.lock)
    if arguments.command == "assets":
        result = materialize_assets(document, arguments.cache_root)
    else:
        result = preload_runtime_images(document, arguments.cluster_name)
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    main()
