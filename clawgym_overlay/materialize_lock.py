"""Materialize the immutable deployment lock through typed host seams."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import time
import urllib.request
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, BinaryIO, cast

from clawgym_overlay.deployment_lock import load_deployment_lock, validate_deployment_lock
from clawgym_overlay.locked_runtime import LockedRuntime, LockedRuntimeError
from clawgym_overlay.runtime_image_backend import SubprocessRuntimeImageBackend, preload_images_with_backend


def resolve_node_platform_digest(
    node: str,
    source: str,
    integrity: str,
    platform: str,
    *,
    runner: Callable[..., Any] = subprocess.run,
) -> str:
    """Resolve exactly one locked platform descriptor from a Kind node."""

    listed = runner(
        ("docker", "exec", "--privileged", node, "ctr", "--namespace=k8s.io", "images", "list", f"name=={source}"),
        check=True,
        capture_output=True,
        text=True,
    )
    lines = listed.stdout.splitlines()
    if len(lines) != 2 or len(lines[1].split()) < 3:
        raise LockedRuntimeError("locked runtime image descriptor is missing")
    descriptor_digest = lines[1].split()[2]
    if descriptor_digest != integrity:
        return descriptor_digest
    content = runner(
        ("docker", "exec", "--privileged", node, "ctr", "--namespace=k8s.io", "content", "get", integrity),
        check=True,
        capture_output=True,
    )
    try:
        descriptor: Any = json.loads(content.stdout)
    except (TypeError, json.JSONDecodeError) as exc:
        raise LockedRuntimeError("locked runtime image descriptor is invalid") from exc
    if not isinstance(descriptor, Mapping):
        raise LockedRuntimeError("locked runtime image descriptor is invalid")
    descriptor = cast(Mapping[str, Any], descriptor)
    manifests = descriptor.get("manifests")
    if manifests is None:
        return descriptor_digest
    if not isinstance(manifests, list):
        raise LockedRuntimeError("locked runtime image descriptor is invalid")
    os_name, architecture = platform.split("/", 1)
    matches: list[Mapping[str, Any]] = []
    for raw_item in cast(list[Any], manifests):
        if not isinstance(raw_item, Mapping):
            raise LockedRuntimeError("locked runtime image descriptor is invalid")
        item = cast(Mapping[str, Any], raw_item)
        item_platform = item.get("platform")
        if not isinstance(item_platform, Mapping):
            raise LockedRuntimeError("locked runtime image descriptor is invalid")
        item_platform = cast(Mapping[str, Any], item_platform)
        if (
            item_platform.get("os") == os_name
            and item_platform.get("architecture") == architecture
            and not item_platform.get("variant")
        ):
            matches.append(item)
    if len(matches) != 1 or not isinstance(matches[0].get("digest"), str):
        raise LockedRuntimeError("locked runtime platform descriptor is ambiguous")
    return cast(str, matches[0]["digest"])


def materialize_assets(
    document: Mapping[str, Any],
    cache_root: str | Path,
    *,
    opener: Callable[[str], BinaryIO] = urllib.request.urlopen,
) -> dict[str, Any]:
    """Populate a caller-created empty directory from checksum-locked assets."""

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
            if f"sha256:{digest.hexdigest()}" != artifact["integrity"]:
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
    platform_digest_resolver: Callable[[str, str, str, str], str] | None = None,
) -> dict[str, Any]:
    """Validate and preload locked images using an explicit backend."""

    validate_deployment_lock(document)
    platform = document["platform"].replace("-", "/", 1)
    backend = SubprocessRuntimeImageBackend(
        cluster_name=cluster_name,
        platform=platform,
        runner=runner,
        ready_checker=ready_checker,
        host_seeder=host_seeder,
        platform_digest_resolver=platform_digest_resolver,
    )
    return preload_images_with_backend(
        document,
        cluster_name,
        backend=backend,
        nodes=nodes,
        platform=platform,
        sleeper=sleeper,
    )


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
    result = (
        materialize_assets(document, arguments.cache_root)
        if arguments.command == "assets"
        else preload_runtime_images(document, arguments.cluster_name)
    )
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    main()
