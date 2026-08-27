"""Host-controlled, least-privilege Stratus container invocation for WP5."""

from __future__ import annotations

import hashlib
import os
import stat
import subprocess
import tempfile
import time
from pathlib import Path

from clawgym.contracts import RunManifest

from clawgym_overlay.providers.reference_agent import ReferenceAgentExecution


class ReferenceAgentSecretError(ValueError):
    """Raised when the agent-only secret file is absent or unsafe."""


def read_agent_secret(path: str | Path) -> str:
    secret = Path(path)
    try:
        metadata = secret.lstat()
    except OSError as exc:
        raise ReferenceAgentSecretError("agent secret file is unavailable") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise ReferenceAgentSecretError("agent secret must be a regular non-symlink file")
    if stat.S_IMODE(metadata.st_mode) != 0o600:
        raise ReferenceAgentSecretError("agent secret file must have mode 0600")
    value = secret.read_text(encoding="utf-8").strip()
    if not value:
        raise ReferenceAgentSecretError("agent secret file is empty")
    return value


class SafeStratusRunner:
    """Run Stratus with only filtered Kubernetes access and one model credential."""

    def __init__(self, *, profile: dict, secret_file: str | Path, image: str = "sregym-agent-base:latest"):
        self._profile = profile
        self._secret_file = Path(secret_file)
        self._image = image

    def __call__(self, run_manifest: RunManifest, filtered_kubeconfig_path: str) -> ReferenceAgentExecution:
        kubeconfig = Path(filtered_kubeconfig_path)
        if not kubeconfig.is_file() or kubeconfig.is_symlink():
            raise RuntimeError("filtered kubeconfig is unavailable")
        key = read_agent_secret(self._secret_file)
        started = time.monotonic()
        with tempfile.TemporaryDirectory(prefix="clawgym-wp5-agent-") as logs:
            command = [
                "docker", "run", "--rm", "--network=host",
                "--add-host=host.docker.internal:host-gateway", "--read-only",
                "--tmpfs", "/tmp:rw,noexec,nosuid,size=256m",
                "--cap-drop=ALL", "--security-opt=no-new-privileges",
                "--cpus=4", "--memory=8g",
                "-v", f"{kubeconfig.resolve()}:/root/.kube/config:ro",
                "-v", f"{Path(logs).resolve()}:/logs:rw",
                "-e", "KUBECONFIG=/root/.kube/config",
                "-e", "AGENT_LOGS_DIR=/logs",
                "-e", f"AGENT_MODEL_ID={self._profile['model_id']}",
                "-e", f"AGENT_API_BASE={self._profile['api_base']}",
                "-e", f"AGENT_API_KEY={key}",
                "-e", "API_HOSTNAME=host.docker.internal",
                "-e", "MCP_SERVER_URL=http://host.docker.internal:9954",
                self._image,
                *self._profile["command"],
            ]
            completed = subprocess.run(command, capture_output=True, text=False, timeout=1800, check=False)
            digest = hashlib.sha256(completed.stdout + completed.stderr).hexdigest()
            size = len(completed.stdout) + len(completed.stderr)
        duration_ms = int((time.monotonic() - started) * 1000)
        return ReferenceAgentExecution(
            exit_code=completed.returncode if completed.returncode >= 0 else 1,
            submission={"reference_agent": "stratus", "run_manifest_digest": run_manifest.manifest_digest},
            duration_ms=duration_ms,
            transcript_digest=digest,
            transcript_bytes=size,
        )
