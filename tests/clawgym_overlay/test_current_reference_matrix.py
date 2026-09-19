"""Current-revision Reference/Provider cases used by the pre-ECS matrix.

These tests exercise the real Provider-owned adapter and ToolAccess classes
with in-memory host doubles.  They do not import ZeroClaw or turn a Provider
native process result into a ZeroClaw receipt.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from clawgym import sha256_digest

from clawgym_overlay.providers import (
    ReferenceAgentExecution,
    SREGymReferenceAgentAdapter,
    SREGymToolAccessProvider,
)
from clawgym_overlay.providers.sregym import SREGymAccessHandle

NOW = "2026-09-13T12:00:00Z"


def _reference_run() -> SimpleNamespace:
    return SimpleNamespace(lane="agent_validation", manifest_digest="a" * 64)


def _reference_result(*, exit_code: int, timeout_seconds: int = 0):
    adapter = SREGymReferenceAgentAdapter(
        sha256_digest({"adapter": "current-reference-matrix"}),
        lambda _run, _kubeconfig: ReferenceAgentExecution(
            exit_code=exit_code,
            submission=None,
            duration_ms=4,
            transcript_digest="b" * 64,
            transcript_bytes=4,
            transcript="bounded process evidence",
            image_digest="c" * 64,
            timeout_seconds=timeout_seconds,
        ),
        clock=lambda: NOW,
    )
    return adapter.invoke(_reference_run(), SREGymAccessHandle("/temporary/filtered-kubeconfig"))


def test_reference_adapter_preserves_nonzero_process_status() -> None:
    result = _reference_result(exit_code=1)

    assert result.outcome.status == "failed"
    assert result.submission is None
    summary = result.outcome.evidence[0].document["summary"]
    assert summary["exit_code"] == 1
    assert result.outcome.evidence[1].document["transcript_sha256_digest"] == "b" * 64
    assert result.outcome.evidence[1].document["schema_id"] == "clawgym.sregym_reference_agent_process.v2"
    assert result.outcome.evidence[1].document["transcript_redacted"] is True
    assert "transcript" not in result.outcome.evidence[1].document


def test_reference_adapter_does_not_retain_raw_trajectory_records() -> None:
    result = SREGymReferenceAgentAdapter(
        sha256_digest({"adapter": "reference-trajectory-redaction"}),
        lambda run, kubeconfig: ReferenceAgentExecution(
            exit_code=0,
            submission={"ok": True},
            duration_ms=1,
            transcript_digest="a" * 64,
            transcript_bytes=11,
            transcript="model response must not be retained",
            trajectory_records=(
                {
                    "name": "events.jsonl",
                    "sha256_digest": "b" * 64,
                    "bytes": 42,
                    "text": "prompt and model response must not be retained",
                },
            ),
            image_digest="c" * 64,
        ),
        clock=lambda: NOW,
    ).invoke(SimpleNamespace(lane="agent_validation", manifest_digest="a" * 64), SREGymAccessHandle("k"))

    process = result.outcome.evidence[1].document
    assert process["trajectory_record_count"] == 1
    assert process["trajectory_records_redacted"] is True
    assert process["trajectory_records_sha256_digest"] == sha256_digest(
        [
            {
                "name": "events.jsonl",
                "sha256_digest": "b" * 64,
                "bytes": 42,
                "text": "prompt and model response must not be retained",
            }
        ]
    )
    assert all("trajectory" not in ref.artifact_key for ref in result.outcome.evidence)
    assert all("prompt and model response" not in str(ref.document) for ref in result.outcome.evidence)


def test_reference_adapter_preserves_process_timeout_status() -> None:
    result = _reference_result(exit_code=124, timeout_seconds=30)

    assert result.outcome.status == "failed"
    assert result.submission is None
    summary = result.outcome.evidence[0].document["summary"]
    assert summary["exit_code"] == 124
    assert summary["container_timeout_seconds"] == 30


def test_reference_adapter_rejects_incomplete_diagnosis_handoff_after_zero_exit() -> None:
    adapter = SREGymReferenceAgentAdapter(
        sha256_digest({"adapter": "current-reference-incomplete-diagnosis"}),
        lambda _run, _kubeconfig: ReferenceAgentExecution(
            exit_code=0,
            submission={"agent_claimed_verdict": "pass"},
            duration_ms=4,
            transcript_digest="b" * 64,
            transcript_bytes=4,
            transcript="bounded process evidence",
            image_digest="c" * 64,
            diagnosis_handoff={"status": "incomplete"},
        ),
        clock=lambda: NOW,
    )

    result = adapter.invoke(_reference_run(), SREGymAccessHandle("/temporary/filtered-kubeconfig"))

    assert result.outcome.status == "failed"
    assert result.submission is None
    summary = result.outcome.evidence[0].document["summary"]
    assert summary["completion_validated"] is False
    assert summary["completion_failure_reason"] == "diagnosis_handoff_incomplete"


class _ToolConductor:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def start_k8s_proxy(self) -> None:
        self.calls.append("tool_grant")

    def get_agent_kubeconfig_path(self) -> str:
        return "/temporary/filtered-kubeconfig"

    def stop_k8s_proxy(self) -> None:
        self.calls.append("tool_revoke")


def test_reference_provider_tool_verification_timeout_closes_proxy() -> None:
    conductor = _ToolConductor()
    run = SimpleNamespace(
        manifest_digest="d" * 64,
        agent_release=SimpleNamespace(agent_release_digest="e" * 64),
        environment_release=SimpleNamespace(environment_release_digest="f" * 64),
    )

    def timeout(_path: str) -> object:
        raise TimeoutError("verification timed out")

    provider = SREGymToolAccessProvider(
        conductor,
        sha256_digest({"provider": "current-reference-matrix"}),
        ("mcp",),
        ("read",),
        ("kube-system",),
        access_verifier=timeout,
    )

    with pytest.raises(TimeoutError, match="timed out"):
        provider.grant(run)
    assert conductor.calls == ["tool_grant", "tool_revoke"]
