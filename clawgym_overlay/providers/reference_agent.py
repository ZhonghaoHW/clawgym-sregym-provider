"""The explicit WP5 Stratus reference AgentAdapter boundary."""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from clawgym.contracts import RunManifest, sha256_digest
from clawgym.providers import AgentInvocationResult, EvidencePayload, LifecycleOutcome

from clawgym_overlay.providers.sregym import _SREGymAccessHandle, _outcome, _utc_now


@dataclass(frozen=True, slots=True)
class ReferenceAgentExecution:
    """Safe host-side result of one isolated Stratus invocation."""

    exit_code: int
    submission: Any
    duration_ms: int
    amount: str = "0"
    currency: str = "USD"
    transcript_digest: str = ""
    transcript_bytes: int = 0
    transcript: str = ""
    trajectory_records: tuple[Mapping[str, Any], ...] = ()
    image_digest: str = ""
    timeout_seconds: int = 0
    diagnosis_handoff: Mapping[str, Any] | None = None
    action_ledger: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        if self.exit_code < 0 or self.duration_ms < 0 or self.transcript_bytes < 0 or self.timeout_seconds < 0:
            raise ValueError("reference execution fields must be non-negative")
        if self.transcript_digest and len(self.transcript_digest) != 64:
            raise ValueError("reference transcript digest must be SHA-256")
        if self.image_digest and len(self.image_digest) != 64:
            raise ValueError("reference image digest must be SHA-256")


@dataclass(slots=True)
class SREGymReferenceAgentAdapter:
    """Runs the frozen Stratus control only through filtered SREGym access."""

    immutable_configuration_digest: str
    execute_stratus: Callable[[RunManifest, str], ReferenceAgentExecution]
    clock: Callable[[], str] = _utc_now
    provider_id: str = field(default="sregym.reference-agent.v1", init=False)
    provider_type: str = field(default="agent_adapter", init=False)

    def invoke(self, run_manifest: RunManifest, access_handle: object) -> AgentInvocationResult:
        if run_manifest.lane != "agent_validation":
            raise RuntimeError("reference agent requires agent_validation lane")
        if not isinstance(access_handle, _SREGymAccessHandle):
            raise RuntimeError("reference agent requires filtered SREGym access")
        started_at = self.clock()
        started = time.monotonic()
        execution = self.execute_stratus(run_manifest, access_handle.kubeconfig_path)
        if not isinstance(execution, ReferenceAgentExecution):
            raise RuntimeError("reference agent runner returned an invalid result")
        duration_ms = max(execution.duration_ms, int((time.monotonic() - started) * 1000))
        completed_at = self.clock()
        status = "succeeded" if execution.exit_code == 0 else "failed"
        transcript_digest = execution.transcript_digest or sha256_digest(
            {"run": run_manifest.manifest_digest, "empty_transcript": True}
        )
        summary: Mapping[str, Any] = {
            "agent": "stratus",
            "exit_code": execution.exit_code,
            "transcript_sha256_digest": transcript_digest,
            "transcript_bytes": execution.transcript_bytes,
            "container_timeout_seconds": execution.timeout_seconds,
        }
        evidence = [
            EvidencePayload(
                artifact_key=f"runs/{run_manifest.manifest_digest}/reference-agent-invocation.json",
                document={
                    "schema_id": "clawgym.sregym_reference_agent_invocation.v1",
                    "provider_id": self.provider_id,
                    "status": status,
                    "summary": dict(summary),
                },
            ),
            EvidencePayload(
                artifact_key=f"runs/{run_manifest.manifest_digest}/reference-agent-process.json",
                document={
                    "schema_id": "clawgym.sregym_reference_agent_process.v1",
                    "image_sha256_digest": execution.image_digest,
                    "transcript": execution.transcript,
                    "transcript_sha256_digest": transcript_digest,
                    "transcript_bytes": execution.transcript_bytes,
                },
            ),
        ]
        if execution.trajectory_records:
            evidence.append(
                EvidencePayload(
                    artifact_key=f"runs/{run_manifest.manifest_digest}/reference-agent-trajectories.json",
                    document={
                        "schema_id": "clawgym.sregym_reference_agent_trajectories.v1",
                        "records": list(execution.trajectory_records),
                    },
                )
            )
        if execution.diagnosis_handoff is not None:
            evidence.append(EvidencePayload(
                artifact_key=f"runs/{run_manifest.manifest_digest}/sregym-diagnosis-handoff.json",
                document=dict(execution.diagnosis_handoff),
            ))
        if execution.action_ledger is not None:
            evidence.append(EvidencePayload(
                artifact_key=f"runs/{run_manifest.manifest_digest}/sregym-agent-action-ledger.json",
                document=dict(execution.action_ledger),
            ))
        return AgentInvocationResult(
            outcome=LifecycleOutcome(
                phase="agent_invocation",
                provider_id=self.provider_id,
                status=status,
                started_at=started_at,
                completed_at=completed_at,
            evidence=tuple(evidence),
            ),
            submission=execution.submission,
            amount=execution.amount,
            currency=execution.currency,
            duration_ms=duration_ms,
        )
