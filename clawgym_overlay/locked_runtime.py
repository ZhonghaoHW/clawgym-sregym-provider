"""Materialize a validated deployment lock into live SREGym configuration."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Mapping

from clawgym_overlay.deployment_lock import validate_deployment_lock


class LockedRuntimeError(ValueError):
    pass


class LockedRuntime:
    def __init__(self, document: Mapping[str, Any], cache_root: str | Path) -> None:
        validate_deployment_lock(document)
        root = Path(cache_root)
        if not root.exists() or not root.is_dir() or root.is_symlink():
            raise LockedRuntimeError("deployment cache must be an existing regular directory")
        self.document = document
        self.cache_root = root.resolve(strict=True)
        self.artifacts = {item["name"]: item for item in document["artifacts"]}

    def cached_artifact(self, name: str) -> Path:
        artifact = self.artifacts[name]
        if artifact["kind"] not in {"manifest", "chart"}:
            raise LockedRuntimeError(f"{name} is not a cached deployment asset")
        path = self.cache_root / name
        if path.is_symlink() or not path.is_file() or not path.resolve(strict=True).is_relative_to(
            self.cache_root
        ):
            raise LockedRuntimeError(f"cached artifact is missing or unsafe: {name}")
        digest = "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != artifact["integrity"]:
            raise LockedRuntimeError(f"cached artifact digest mismatch: {name}")
        return path

    def image_overrides(self) -> dict[str, str]:
        return {
            artifact["target"]: artifact["source"].removeprefix("oci://")
            for artifact in self.document["artifacts"]
            if artifact["name"].startswith("runtime-image.")
        }

    def verify_required_images(self, observed_images: set[str]) -> None:
        declared = {
            artifact["source"].removeprefix("oci://")
            for artifact in self.document["artifacts"]
            if artifact["kind"] == "image"
        }
        undeclared = observed_images - declared
        if undeclared:
            raise LockedRuntimeError("runtime contains images absent from deployment lock")

    def configure_conductor(self, config: Any) -> None:
        config.metrics_server_manifest = str(self.cached_artifact("metrics-server-manifest"))
        config.openebs_manifest = str(self.cached_artifact("openebs-manifest"))
        config.application_image_overrides = self.image_overrides()

    def configure_services(self, conductor: Any) -> None:
        conductor.loki.helm_configs.update(
            {
                "chart_path": str(self.cached_artifact("loki-chart")),
                "remote_chart": False,
            }
        )
        conductor.loki.promtail_chart_path = str(self.cached_artifact("promtail-chart"))
