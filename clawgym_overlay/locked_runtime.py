"""Materialize a validated deployment lock into live SREGym configuration."""

from __future__ import annotations

import hashlib
import re
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
        expected_cache = {
            name
            for name, artifact in self.artifacts.items()
            if artifact["kind"] in {"manifest", "chart"}
        }
        actual_cache = {path.name for path in self.cache_root.iterdir()}
        if actual_cache != expected_cache:
            raise LockedRuntimeError("deployment cache does not exactly match locked deployment assets")
        for name in sorted(expected_cache):
            self.cached_artifact(name)

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
        def repository(reference: str) -> str:
            without_digest = reference.split("@", maxsplit=1)[0]
            head, separator, tail = without_digest.rpartition("/")
            if ":" in tail:
                tail = tail.split(":", maxsplit=1)[0]
            return f"{head}{separator}{tail}"

        return {
            repository(artifact["target"]): artifact["source"].removeprefix("oci://")
            for artifact in self.document["artifacts"]
            if artifact["name"].startswith("runtime-image.application.")
        }

    def image_reference(self, name: str) -> str:
        artifact = self.artifacts[name]
        if artifact["kind"] != "image":
            raise LockedRuntimeError(f"{name} is not a locked image")
        return artifact["source"].removeprefix("oci://")

    @staticmethod
    def _image_digest(reference: str) -> str:
        match = re.search(r"sha256:([0-9a-f]{64})$", reference)
        if match is None:
            raise LockedRuntimeError("observed runtime image is not content addressed")
        return match.group(1)

    def verify_required_images(self, observed_images: set[str]) -> None:
        declared = {
            self._image_digest(artifact["source"])
            for artifact in self.document["artifacts"]
            if artifact["kind"] == "image"
        }
        observed = {self._image_digest(reference) for reference in observed_images}
        undeclared = observed - declared
        if undeclared:
            raise LockedRuntimeError("runtime contains images absent from deployment lock")

    def cluster_image_inventory(self, conductor: Any) -> dict[str, Any]:
        pods = conductor.kubectl.core_v1_api.list_pod_for_all_namespaces().items
        # Kubernetes/containerd may report either the multi-platform index
        # digest or the selected linux/amd64 manifest digest in
        # ``status.imageID``.  Both identities are locked; accepting either
        # keeps the inventory check strict without depending on the runtime's
        # reporting choice.
        declared_digests = {
            self._image_digest(artifact[field])
            for artifact in self.document["artifacts"]
            if artifact["kind"] == "image"
            and artifact["name"].startswith("runtime-image.")
            for field in ("integrity", "platform_integrity")
        }
        bundled_targets = {
            artifact["target"]
            for artifact in self.document["artifacts"]
            if artifact["kind"] == "image" and artifact["name"].startswith("kind-bundled-image.")
        }

        def is_bundled_target(target: str | None) -> bool:
            if target in bundled_targets:
                return True
            # Kind's node image may expose amd64-qualified names for control
            # plane components even though the lock records the canonical
            # target without that platform suffix.
            if target and "-amd64:" in target:
                return target.replace("-amd64:", ":", 1) in bundled_targets
            return False

        observed_tokens: list[str] = []
        container_count = 0
        for pod in pods:
            specifications = {
                container.name: container.image
                for containers in (
                    pod.spec.init_containers or [],
                    pod.spec.containers or [],
                )
                for container in containers
            }
            for statuses in (
                pod.status.init_container_statuses or [],
                pod.status.container_statuses or [],
            ):
                for status in statuses:
                    if not status.image_id:
                        continue
                    container_count += 1
                    target = specifications.get(status.name)
                    # Kind-bundled images are reported by containerd as a
                    # bare manifest digest (``sha256:...``), while the lock
                    # identifies them by their image target inside the
                    # kindest/node image.  Prefer that explicit target
                    # classification before treating a bare digest as a
                    # normal registry image.
                    if is_bundled_target(target) and status.image_id.startswith("sha256:"):
                        observed_tokens.append(f"kind-bundled:{target}")
                        continue
                    if "@sha256:" in status.image_id or status.image_id.startswith("sha256:"):
                        digest = self._image_digest(status.image_id)
                        if digest not in declared_digests:
                            raise LockedRuntimeError(
                                "runtime contains images absent from deployment lock"
                            )
                        observed_tokens.append(f"digest:{digest}")
                        continue
                    if not is_bundled_target(target):
                        raise LockedRuntimeError(
                            "runtime contains an unrecognized image bundled in the Kind node"
                        )
                    observed_tokens.append(f"kind-bundled:{target}")
        tokens = sorted(observed_tokens)
        return {
            "passed": True,
            "observed_container_count": container_count,
            "observed_image_identity_count": len(set(tokens)),
            "observed_image_set_digest": hashlib.sha256("\n".join(tokens).encode()).hexdigest(),
        }

    def cache_summary(self) -> dict[str, Any]:
        assets = [
            {
                "name": name,
                "integrity": self.artifacts[name]["integrity"],
            }
            for name in sorted(self.artifacts)
            if self.artifacts[name]["kind"] in {"manifest", "chart"}
        ]
        return {
            "schema_id": "clawgym.sregym_deployment_cache.v1",
            "asset_count": len(assets),
            "asset_set_digest": hashlib.sha256(
                "\n".join(f"{item['name']}:{item['integrity']}" for item in assets).encode()
            ).hexdigest(),
        }

    def configure_conductor(self, config: Any) -> None:
        config.metrics_server_manifest = str(self.cached_artifact("metrics-server-manifest"))
        config.openebs_manifest = str(self.cached_artifact("openebs-manifest"))
        config.application_image_overrides = self.image_overrides()
        config.mcp_image = self.image_reference("runtime-image.mcp-server")
        config.workload_image = self.image_reference("runtime-image.workload")

    def configure_services(self, conductor: Any) -> None:
        # The Prometheus chart packages dependencies as .tgz files during
        # install.  Pin the kube-state-metrics repository explicitly so the
        # chart's registry prefix is not duplicated when resolving the locked
        # image target.
        prometheus_args = conductor.prometheus.helm_configs.setdefault("extra_args", [])
        if "kube-state-metrics.image.repository=kube-state-metrics/kube-state-metrics" not in prometheus_args:
            prometheus_args.extend(
                [
                    "--set",
                    "kube-state-metrics.image.repository=kube-state-metrics/kube-state-metrics",
                ]
            )
        conductor.loki.helm_configs.update(
            {
                "chart_path": str(self.cached_artifact("loki-chart")),
                "remote_chart": False,
            }
        )
        conductor.loki.promtail_chart_path = str(self.cached_artifact("promtail-chart"))
