"""Side-effect boundary for approved provider worker execution."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, cast


@dataclass(frozen=True)
class ExecutionOutcome:
    bundle_digest: str
    episode_digest: str


@dataclass(frozen=True)
class WorkerRuntimeDeps:
    """Typed callbacks injected by the composition root and tests."""

    claim_attempt: Callable[[Mapping[str, Any]], Mapping[str, Any]]
    approved_executor: Callable[[Mapping[str, Any]], Any]
    legacy_executor: Callable[[Mapping[str, Any]], Any]
    write_claim: Callable[[Mapping[str, Any]], None]


@dataclass
class WorkerHostSession:
    """Own exactly one API-thread lifetime after host admission succeeds."""

    start_api: Callable[[], None]
    request_shutdown: Callable[[], Any]
    join_api: Callable[[], None]

    def __enter__(self) -> WorkerHostSession:
        self.start_api()
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> bool:
        cleanup_error: BaseException | None = None
        try:
            self.request_shutdown()
        except BaseException as error:
            cleanup_error = error
        try:
            self.join_api()
        except BaseException as error:
            if cleanup_error is None:
                cleanup_error = error
        if cleanup_error is not None and exc_type is None:
            raise RuntimeError("provider API shutdown failed") from cleanup_error
        return False


def execute_admitted_trial(
    *,
    materialized: bool,
    documents: Mapping[str, Mapping[str, Any] | None],
    deps: WorkerRuntimeDeps,
) -> ExecutionOutcome:
    """Execute one already-admitted trial through exactly one lane."""

    if materialized:
        required = (
            documents.get("validation_request"),
            documents.get("candidate"),
            documents.get("materialization_receipt"),
            documents.get("parent_agent_release"),
            documents.get("approval"),
            documents.get("matrix"),
            documents.get("trial"),
            documents.get("attempt_request"),
        )
        if any(item is None for item in required):
            raise ValueError("materialized candidate execution requires the complete approved artifact chain")
        claim_input = cast(Mapping[str, Any], documents["attempt_request"])
        claim = deps.claim_attempt(claim_input)
        deps.write_claim(claim)
        result = deps.approved_executor({**documents, "attempt_claim": claim})
    else:
        result = deps.legacy_executor(documents)
    bundle_digest = getattr(result, "bundle_digest", None)
    episode_digest = getattr(result, "episode_digest", None)
    if not isinstance(bundle_digest, str) or not isinstance(episode_digest, str):
        raise ValueError("worker execution result is missing bundle and episode digests")
    return ExecutionOutcome(bundle_digest, episode_digest)
