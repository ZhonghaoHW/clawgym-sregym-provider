"""Materialize an immutable WP4 deployment cache and preload locked images."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
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

    for artifact in sorted(document["artifacts"], key=lambda item: item["name"]):
        if not artifact["name"].startswith("runtime-image."):
            continue
        source = artifact["source"].removeprefix("oci://")
        target = canonical_target(artifact["target"])
        for node in nodes:
            commands = (
                (
                    "pull",
                    (
                        "docker", "exec", "--privileged", node,
                        "ctr", "--namespace=k8s.io", "images", "pull",
                        "--platform", platform, source,
                    ),
                ),
                (
                    "tag",
                    (
                        "docker", "exec", "--privileged", node,
                        "ctr", "--namespace=k8s.io", "images", "tag",
                        "--force", source, target,
                    ),
                ),
            )
            for stage, command in commands:
                try:
                    runner(
                        command,
                        check=True,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                except subprocess.CalledProcessError as exc:
                    raise LockedRuntimeError(
                        f"failed to {stage} locked runtime image: {artifact['name']}"
                    ) from exc
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
