from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from clawgym_overlay import worker, worker_admission
from clawgym_overlay.worker_admission import (
    ExecutionDocuments,
    LegacyExecutionAdmission,
    MaterializedExecutionAdmission,
    validate_campaign_chain,
    validate_campaign_execution,
    validate_worker_admission,
)
from clawgym_overlay.worker_runtime import WorkerHostSession, WorkerRuntimeDeps, execute_admitted_trial


def docs(**run_overrides: object) -> ExecutionDocuments:
    run = {"run_id": "run-1", "lane": "environment_validation", **run_overrides}
    return ExecutionDocuments(run=run, agent={}, environment={})


@pytest.mark.parametrize(
    "run, message",
    [
        ({"run_id": "", "lane": "environment_validation"}, "run_id"),
        ({"run_id": "run-1", "lane": "unknown"}, "validation lane"),
        ({"run_id": "run-1", "lane": "environment_validation", "case_id": 1}, "case_id"),
        ({"run_id": "run-1", "lane": "environment_validation", "seed": True}, "seed"),
        ({"run_id": "run-1", "lane": "environment_validation", "partition": 1}, "partition"),
    ],
)
def test_admission_rejects_invalid_run_identity(run: dict[str, object], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        validate_worker_admission(
            ExecutionDocuments(run=run, agent={}, environment={}),
            materialization_bundle=None,
            attempt_claim_root=None,
            environment_lease_root=None,
        )


def test_materialized_admission_returns_typed_object(tmp_path: Path) -> None:
    document = ExecutionDocuments(
        run={"run_id": "run-1", "lane": "agent_validation", "seed": 1},
        agent={},
        environment={},
        validation_request={},
        candidate={},
        materialization_receipt={},
        parent_agent_release={},
        approval={},
        matrix={},
        trial={},
        attempt_request={},
        attempt_ledger={},
        environment_lease={},
    )
    admission = validate_worker_admission(
        document,
        materialization_bundle=tmp_path / "bundle",
        attempt_claim_root=tmp_path / "claims",
        environment_lease_root=tmp_path / "leases",
    )
    assert isinstance(admission, MaterializedExecutionAdmission)
    assert admission.materialization_bundle == tmp_path / "bundle"


def test_campaign_chain_requires_all_documents() -> None:
    with pytest.raises(ValueError, match="campaign, plan"):
        validate_campaign_chain(ExecutionDocuments(run={}, agent={}, environment={}, campaign={}))
    assert validate_campaign_chain(docs()) is None
    with pytest.raises(ValueError, match="campaign, plan"):
        validate_campaign_chain(
            ExecutionDocuments(
                run={"run_id": "run-1", "lane": "agent_validation"},
                agent={},
                environment={},
                campaign={},
                campaign_plan={},
            )
        )


def test_campaign_preflight_verifies_campaign_and_authorization_before_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: dict[str, object] = {}
    admission_module = type(sys)("clawgym.campaign_admission")

    def verify_reference_campaign_admission(**kwargs: object) -> tuple[str, str]:
        calls["campaign"] = kwargs
        return ("campaign-digest", "plan-digest")

    admission_module.verify_reference_campaign_admission = verify_reference_campaign_admission
    monkeypatch.setitem(sys.modules, "clawgym.campaign_admission", admission_module)
    monkeypatch.setattr(
        "clawgym_overlay.worker_admission.verify_campaign_authorization",
        lambda document, **kwargs: calls.update(authorization=(document, kwargs)),
    )
    documents = ExecutionDocuments(
        run={"run_id": "run-1", "lane": "agent_validation", "case_id": "case-001", "seed": 7},
        agent={},
        environment={},
        campaign={"deployment_lock_digest": "l" * 64},
        campaign_plan={},
        readiness_control_set={},
        campaign_authorization={"purpose": "train", "campaign_digest": "c", "generation": 2},
        candidate={"candidate_digest": "a" * 64},
        trial={"trial_digest": "b" * 64, "partition": "train"},
        approval={"approval_record_digest": "d" * 64},
    )
    result = validate_campaign_execution(
        documents,
        environment_release_digest="e" * 64,
        clawgym_revision="f" * 40,
        provider_revision="1" * 40,
    )
    assert result.admission == ("campaign-digest", "plan-digest")
    assert result.campaign == documents.campaign
    assert calls["campaign"]
    assert calls["authorization"]


@pytest.mark.parametrize(
    ("documents", "message"),
    [
        (
            ExecutionDocuments(
                run={"run_id": "run-1", "lane": "agent_validation"}, agent={}, environment={}, campaign={}
            ),
            "campaign, plan",
        ),
        (
            ExecutionDocuments(
                run={"run_id": "run-1", "lane": "agent_validation"},
                agent={},
                environment={},
                campaign_authorization={"purpose": "train"},
            ),
            "candidate, trial and approval",
        ),
    ],
)
def test_campaign_preflight_rejects_incomplete_documents(documents: ExecutionDocuments, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        validate_campaign_execution(
            documents,
            environment_release_digest="e" * 64,
            clawgym_revision="f" * 40,
            provider_revision="1" * 40,
        )


def test_legacy_admission_and_runtime_dispatch() -> None:
    admission = validate_worker_admission(
        docs(), materialization_bundle=None, attempt_claim_root=None, environment_lease_root=None
    )
    assert isinstance(admission, LegacyExecutionAdmission)
    called: list[str] = []
    deps = WorkerRuntimeDeps(
        claim_attempt=lambda _: called.append("claim") or {},
        approved_executor=lambda _: SimpleNamespace(bundle_digest="a", episode_digest="b"),
        legacy_executor=lambda _: called.append("legacy") or SimpleNamespace(bundle_digest="c", episode_digest="d"),
        write_claim=lambda _: called.append("write"),
    )
    outcome = execute_admitted_trial(materialized=False, documents={}, deps=deps)
    assert outcome.bundle_digest == "c"
    assert called == ["legacy"]


def test_runtime_dispatch_requires_chain_and_claims_materialized() -> None:
    called: list[str] = []
    deps = WorkerRuntimeDeps(
        claim_attempt=lambda _: called.append("claim") or {"claim": True},
        approved_executor=lambda payload: (
            called.append(str(payload["attempt_claim"])) or SimpleNamespace(bundle_digest="a", episode_digest="b")
        ),
        legacy_executor=lambda _: SimpleNamespace(bundle_digest="c", episode_digest="d"),
        write_claim=lambda _: called.append("write"),
    )
    with pytest.raises(ValueError, match="complete approved"):
        execute_admitted_trial(materialized=True, documents={}, deps=deps)
    result = execute_admitted_trial(
        materialized=True,
        documents={
            "attempt_request": {},
            "validation_request": {},
            "candidate": {},
            "materialization_receipt": {},
            "parent_agent_release": {},
            "approval": {},
            "matrix": {},
            "trial": {},
        },
        deps=deps,
    )
    assert result.episode_digest == "b"
    assert called[0] == "claim"
    assert called[1] == "write"


def test_runtime_rejects_result_without_digests() -> None:
    deps = WorkerRuntimeDeps(
        claim_attempt=lambda _: {},
        approved_executor=lambda _: object(),
        legacy_executor=lambda _: object(),
        write_claim=lambda _: None,
    )
    with pytest.raises(ValueError, match="missing bundle"):
        execute_admitted_trial(materialized=False, documents={}, deps=deps)


def test_host_session_only_joins_started_api_and_preserves_primary_failure() -> None:
    calls: list[str] = []

    def start() -> None:
        calls.append("start")

    def shutdown() -> None:
        calls.append("shutdown")
        raise RuntimeError("shutdown failed")

    def join() -> None:
        calls.append("join")

    with pytest.raises(ValueError, match="primary"), WorkerHostSession(start, shutdown, join):
        raise ValueError("primary")
    assert calls == ["start", "shutdown", "join"]


def test_host_session_surfaces_cleanup_failure_without_primary_error() -> None:
    calls: list[str] = []

    def start() -> None:
        calls.append("start")

    def shutdown() -> None:
        calls.append("shutdown")
        raise RuntimeError("shutdown failed")

    def join() -> None:
        calls.append("join")

    with pytest.raises(RuntimeError, match="shutdown failed"), WorkerHostSession(start, shutdown, join):
        pass
    assert calls == ["start", "shutdown", "join"]


def test_host_session_surfaces_join_failure_after_shutdown() -> None:
    calls: list[str] = []

    def start() -> None:
        calls.append("start")

    def shutdown() -> None:
        calls.append("shutdown")

    def join() -> None:
        calls.append("join")
        raise RuntimeError("join failed")

    with pytest.raises(RuntimeError, match="provider API shutdown failed"), WorkerHostSession(start, shutdown, join):
        pass
    assert calls == ["start", "shutdown", "join"]


def test_host_session_keeps_first_cleanup_failure_when_join_also_fails() -> None:
    def fail_shutdown() -> None:
        raise RuntimeError("shutdown failed")

    def fail_join() -> None:
        raise RuntimeError("join failed")

    with (
        pytest.raises(RuntimeError, match="provider API shutdown failed"),
        WorkerHostSession(lambda: None, fail_shutdown, fail_join),
    ):
        pass


def test_host_session_does_not_shutdown_when_api_start_fails() -> None:
    calls: list[str] = []

    def start() -> None:
        calls.append("start")
        raise RuntimeError("start failed")

    with (
        pytest.raises(RuntimeError, match="start failed"),
        WorkerHostSession(start, lambda: calls.append("shutdown"), lambda: calls.append("join")),
    ):
        pass
    assert calls == ["start"]


def test_campaign_authorization_legacy_fallback_is_strict(monkeypatch: pytest.MonkeyPatch) -> None:
    document = {
        "schema_id": "agent_evolution.campaign_trial_authorization.v1",
        "execution_scope": "reference_family_only",
        "purpose": "train",
        "partition": "train",
        "case_id": "case-001",
        "seed": 1,
        "candidate_digest": "a" * 64,
        "trial_digest": "b" * 64,
        "approval_digest": "c" * 64,
        "campaign_digest": "expected",
        "generation": 1,
    }
    from clawgym.contracts import sha256_digest

    document["authorization_digest"] = sha256_digest(
        {key: value for key, value in document.items() if key != "authorization_digest"}
    )
    monkeypatch.setitem(sys.modules, "clawgym.execution_bridge", None)
    worker_admission.verify_campaign_authorization(
        document,
        candidate_digest="a" * 64,
        trial_digest="b" * 64,
        approval_digest="c" * 64,
        case_id="case-001",
        seed=1,
        partition="train",
        purpose="train",
    )
    with pytest.raises(Exception, match="digest mismatch"):
        worker_admission.verify_campaign_authorization(
            dict(document, authorization_digest="0" * 64),
            candidate_digest="a" * 64,
            trial_digest="b" * 64,
            approval_digest="c" * 64,
            case_id="case-001",
            seed=1,
            partition="train",
            purpose="train",
        )
    for mutation, message in (
        ({"execution_scope": "automatic"}, "scope"),
        ({"case_id": "case-002"}, "trial identity"),
        ({"candidate_digest": "d" * 64}, "references"),
        ({"campaign_digest": "e" * 64}, "campaign mismatch"),
        ({"generation": 2}, "generation mismatch"),
    ):
        changed = dict(document)
        changed.update(mutation)
        changed["authorization_digest"] = sha256_digest(
            {key: value for key, value in changed.items() if key != "authorization_digest"}
        )
        with pytest.raises(Exception, match=message):
            worker_admission.verify_campaign_authorization(
                changed,
                candidate_digest="a" * 64,
                trial_digest="b" * 64,
                approval_digest="c" * 64,
                case_id="case-001",
                seed=1,
                partition="train",
                purpose="train",
                campaign_digest="expected",
                generation=1,
            )


def test_worker_main_dispatches_explicit_command(monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[str] = []
    monkeypatch.setattr(worker, "execute", lambda _args: called.append("execute"))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "worker",
            "execute",
            "--run-manifest",
            "run",
            "--agent-release",
            "agent",
            "--environment-release",
            "environment",
            "--evidence-root",
            "evidence",
            "--episode-id",
            "episode",
            "--clawgym-checkout",
            "clawgym",
            "--clawgym-revision",
            "a" * 40,
            "--provider-checkout",
            "provider",
            "--provider-revision",
            "b" * 40,
            "--deployment-cache",
            "cache",
        ],
    )
    worker.main()
    assert called == ["execute"]
