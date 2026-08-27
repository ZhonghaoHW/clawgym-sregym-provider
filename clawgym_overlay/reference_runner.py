"""Host-controlled, least-privilege Stratus container invocation for WP5."""

from __future__ import annotations

import hashlib
import re
import stat
import subprocess
import tempfile
import time
from pathlib import Path

from clawgym.contracts import RunManifest

from clawgym_overlay.providers.reference_agent import ReferenceAgentExecution


_SENSITIVE_OUTPUT = re.compile(
    r"(?i)(?:bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}|"
    r"(?:sk|ak)-[A-Za-z0-9_-]{12,}|"
    r"\b[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}\b|"
    r"-----BEGIN [A-Z0-9 ]*(?:PRIVATE KEY|CERTIFICATE)-----"
)


def _safe_text(payload: bytes) -> str:
    """Retain process evidence without persisting credentials or host paths."""

    text = payload.decode("utf-8", errors="replace")
    text = _SENSITIVE_OUTPUT.sub("[REDACTED]", text)
    return re.sub(r"(?<![A-Za-z0-9_.-])/(?:[^\s\x00]+)", "[HOST_PATH]", text)


def _trajectory_records(root: Path) -> tuple[dict[str, object], ...]:
    records: list[dict[str, object]] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        payload = path.read_bytes()
        records.append(
            {
                "name": path.relative_to(root).as_posix(),
                "sha256_digest": hashlib.sha256(payload).hexdigest(),
                "bytes": len(payload),
                "text": _safe_text(payload),
            }
        )
    return tuple(records)


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
        image_id = subprocess.run(
            ["docker", "image", "inspect", self._image, "--format", "{{.Id}}"],
            capture_output=True,
            check=True,
            text=True,
        ).stdout.strip()
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id):
            raise RuntimeError("reference agent image does not have a local SHA-256 identity")
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
                image_id,
                *self._profile["command"],
            ]
            completed = subprocess.run(command, capture_output=True, text=False, timeout=1800, check=False)
            transcript = completed.stdout + completed.stderr
            digest = hashlib.sha256(transcript).hexdigest()
            size = len(transcript)
            trajectories = _trajectory_records(Path(logs))
        duration_ms = int((time.monotonic() - started) * 1000)
        return ReferenceAgentExecution(
            exit_code=completed.returncode if completed.returncode >= 0 else 1,
            submission={"reference_agent": "stratus", "run_manifest_digest": run_manifest.manifest_digest},
            duration_ms=duration_ms,
            transcript_digest=digest,
            transcript_bytes=size,
            transcript=_safe_text(transcript),
            trajectory_records=trajectories,
            image_digest=image_id.removeprefix("sha256:"),
        )
