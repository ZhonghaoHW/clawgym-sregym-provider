import logging
import os
import shutil
import socket
import subprocess
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

import requests
import yaml

from sregym.paths import MCP_SERVER_K8S
from sregym.service.kubectl import KubeCtl

logger = logging.getLogger("all.sregym.mcp_server")


class MCPServer:
    def __init__(self, image_override: str | None = None):
        self.namespace = "sregym"
        self.service_name = "mcp-server"
        self.port = 9954
        self.port_forward_process = None
        self.kubectl = KubeCtl()
        self.image_override = image_override

    @contextmanager
    def _deployment_resources(self):
        if self.image_override is None:
            yield MCP_SERVER_K8S
            return
        repository, separator, digest = self.image_override.rpartition("@sha256:")
        if not separator or not repository or len(digest) != 64:
            raise RuntimeError("MCP image override must be an immutable SHA-256 OCI reference")
        with tempfile.TemporaryDirectory(prefix="sregym-mcp-") as temporary_dir:
            resources = Path(temporary_dir) / "k8s"
            shutil.copytree(MCP_SERVER_K8S, resources)
            kustomization_path = resources / "kustomization.yaml"
            with kustomization_path.open(encoding="utf-8") as handle:
                kustomization = yaml.safe_load(handle)
            image = next(
                (item for item in kustomization.get("images", []) if item.get("name") == "sregym"),
                None,
            )
            if image is None:
                raise RuntimeError("MCP kustomization does not declare the sregym image")
            image["newName"] = repository
            image["digest"] = f"sha256:{digest}"
            image.pop("newTag", None)
            with kustomization_path.open("w", encoding="utf-8") as handle:
                yaml.safe_dump(kustomization, handle, sort_keys=False)
            yield resources

    def _is_running(self) -> bool:
        """Check if the MCP server deployment already exists and is ready."""
        result = self.kubectl.exec_command(
            f"kubectl get deployment {self.service_name} -n {self.namespace} -o jsonpath='{{.status.readyReplicas}}'"
        )
        value = result.strip().strip("'")
        # exec_command returns stderr on failure (e.g. "Error from server (NotFound)"),
        # so only treat a purely numeric positive value as "running".
        return value.isdigit() and int(value) > 0

    def _ensure_rbac(self):
        """Ensure RBAC resources exist even if the MCP server pod is already running."""
        rbac_dir = MCP_SERVER_K8S
        for resource in ["clusterrole.yaml", "clusterrolebinding.yaml"]:
            self.kubectl.exec_command(f"kubectl apply -f {rbac_dir / resource}")
        logger.info("MCP server RBAC resources ensured.")

    def deploy(self):
        """Deploy the MCP server into the cluster via kustomize.

        Skips redeployment if the MCP server is already running to avoid disrupting existing connections.
        Always ensures RBAC resources exist regardless of pod state.
        """
        self._ensure_rbac()

        if self._is_running():
            logger.info("MCP server already running, skipping redeploy.")
            if not self._is_port_forward_healthy():
                logger.info("Port-forward is absent or stale, restarting.")
                self.start_port_forward()
            return

        with self._deployment_resources() as resources:
            self.kubectl.exec_command(f"kubectl apply -k {resources}")
        self.kubectl.wait_for_ready(self.namespace)
        self.start_port_forward()
        logger.info("MCP server deployed successfully.")

    def is_port_in_use(self, port: int) -> bool:
        """Check if a local TCP port is already bound."""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            return s.connect_ex(("127.0.0.1", port)) == 0

    def _is_port_forward_healthy(self) -> bool:
        """Check if the port-forward is actually serving traffic, not just bound."""
        try:
            resp = requests.get(f"http://127.0.0.1:{self.port}/kubectl/sse", stream=True, timeout=5)
            resp.close()
            # SSE endpoint returns 200 with text/event-stream
            return resp.status_code == 200
        except Exception:
            return False

    def _kill_stale_port_forward(self):
        """Kill any existing kubectl port-forward process on our port."""
        if self.port_forward_process and self.port_forward_process.poll() is None:
            logger.info("Killing existing port-forward process to re-establish fresh connection.")
            self.stop_port_forward()
            self.port_forward_process = None

        # Also kill orphaned port-forward processes from previous runs that we
        # don't hold a handle to (e.g. the process survived a previous crash).
        if self.is_port_in_use(self.port):
            try:
                result = subprocess.run(f"lsof -ti tcp:{self.port}", shell=True, capture_output=True, text=True)
                for pid in result.stdout.strip().split():
                    if pid.isdigit():
                        logger.info(f"Killing orphaned process {pid} on port {self.port}")
                        subprocess.run(f"kill {pid}", shell=True)
                time.sleep(1)
            except Exception as e:
                logger.warning(f"Failed to kill stale port-forward: {e}")

    def start_port_forward(self):
        """Starts port-forwarding to access the MCP server."""
        self._kill_stale_port_forward()

        for attempt in range(3):
            if self.is_port_in_use(self.port):
                logger.debug(
                    f"Port {self.port} is already in use. Attempt {attempt + 1} of 3. Retrying in 3 seconds..."
                )
                time.sleep(3)
                continue

            command = (
                f"kubectl port-forward svc/{self.service_name} {self.port}:9954 -n {self.namespace} --address 0.0.0.0"
            )
            self.port_forward_process = subprocess.Popen(
                command,
                shell=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            time.sleep(3)

            if self.port_forward_process.poll() is None:
                os.environ["MCP_SERVER_PORT"] = str(self.port)
                logger.info(f"Port forwarding established at {self.port}. MCP_SERVER_PORT set.")
                break
            else:
                logger.warning("Port forwarding failed. Retrying...")
        else:
            logger.warning("Failed to establish port forwarding after multiple attempts.")

    def stop_port_forward(self):
        """Stops the kubectl port-forward command and cleans up resources."""
        if self.port_forward_process:
            self.port_forward_process.terminate()
            try:
                self.port_forward_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                logger.warning("Port-forward process did not terminate in time, killing...")
                self.port_forward_process.kill()

            if self.port_forward_process.stdout:
                self.port_forward_process.stdout.close()
            if self.port_forward_process.stderr:
                self.port_forward_process.stderr.close()

            logger.info("Port forwarding for MCP server stopped.")
