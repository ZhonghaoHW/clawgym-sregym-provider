"""Pure admission contracts for the provider worker.

The worker composition root must not import SREGym or start an API thread
until these checks have succeeded.  This module deliberately contains no
provider, subprocess, network, or model imports.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast


def verify_campaign_authorization(
    document: Mapping[str, Any],
    *,
    candidate_digest: str,
    trial_digest: str,
    approval_digest: str,
    case_id: str,
    seed: int,
    partition: str,
    purpose: str,
    campaign_digest: str | None = None,
    generation: int | None = None,
) -> None:
    """Verify campaign authorization using the current bridge or legacy wire rules."""

    try:
        from clawgym.execution_bridge import verify_campaign_trial_authorization
    except ImportError:
        from clawgym.contracts import ContractValidationError, sha256_digest

        expected = dict(document)
        actual = expected.pop("authorization_digest", None)
        if actual != sha256_digest(expected):
            raise ContractValidationError("campaign authorization digest mismatch") from None
        if (
            document.get("schema_id") != "agent_evolution.campaign_trial_authorization.v1"
            or document.get("execution_scope") != "reference_family_only"
        ):
            raise ContractValidationError("campaign authorization scope is invalid") from None
        if (
            document.get("purpose") != purpose
            or document.get("partition") != partition
            or document.get("case_id") != case_id
            or document.get("seed") != seed
        ):
            raise ContractValidationError("campaign authorization trial identity mismatch") from None
        if (
            document.get("candidate_digest") != candidate_digest
            or document.get("trial_digest") != trial_digest
            or document.get("approval_digest") != approval_digest
        ):
            raise ContractValidationError("campaign authorization digest references mismatch") from None
        if campaign_digest is not None and document.get("campaign_digest") != campaign_digest:
            raise ContractValidationError("campaign authorization campaign mismatch") from None
        if generation is not None and document.get("generation") != generation:
            raise ContractValidationError("campaign authorization generation mismatch") from None
        return
    verify_campaign_trial_authorization(
        authorization_document=document,
        candidate_digest=candidate_digest,
        trial_digest=trial_digest,
        approval_digest=approval_digest,
        case_id=case_id,
        seed=seed,
        partition=partition,
        purpose=purpose,
        campaign_digest=campaign_digest,
        generation=generation,
    )


@dataclass(frozen=True)
class ExecutionDocuments:
    """All explicitly supplied documents for one worker invocation."""

    run: Mapping[str, Any]
    agent: Mapping[str, Any]
    environment: Mapping[str, Any]
    approval: Mapping[str, Any] | None = None
    matrix: Mapping[str, Any] | None = None
    trial: Mapping[str, Any] | None = None
    attempt_request: Mapping[str, Any] | None = None
    attempt_ledger: Mapping[str, Any] | None = None
    environment_lease: Mapping[str, Any] | None = None
    validation_request: Mapping[str, Any] | None = None
    candidate: Mapping[str, Any] | None = None
    materialization_receipt: Mapping[str, Any] | None = None
    parent_agent_release: Mapping[str, Any] | None = None
    campaign_authorization: Mapping[str, Any] | None = None
    campaign: Mapping[str, Any] | None = None
    campaign_plan: Mapping[str, Any] | None = None
    readiness_control_set: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class ExecutionIdentity:
    run_id: str
    lane: str
    case_id: str | None
    seed: int | None
    partition: str | None


@dataclass(frozen=True)
class MaterializedExecutionAdmission:
    documents: ExecutionDocuments
    identity: ExecutionIdentity
    materialization_bundle: Path
    attempt_claim_root: Path
    environment_lease_root: Path


@dataclass(frozen=True)
class LegacyExecutionAdmission:
    documents: ExecutionDocuments
    identity: ExecutionIdentity


@dataclass(frozen=True)
class CampaignExecutionAdmission:
    """Validated campaign documents and the bridge-issued admission tuple."""

    campaign: Mapping[str, Any] | None
    admission: tuple[str, str] | None


def _require_run_identity(run: Mapping[str, Any]) -> ExecutionIdentity:
    run_id = run.get("run_id")
    lane = run.get("lane")
    if not isinstance(run_id, str) or not run_id:
        raise ValueError("run manifest must contain a run_id")
    if lane not in {"environment_validation", "agent_validation"}:
        raise ValueError("live worker requires a reviewed validation lane")
    case_id = run.get("case_id")
    if case_id is not None and not isinstance(case_id, str):
        raise ValueError("run manifest case_id must be a string")
    seed = run.get("seed")
    if seed is not None and (not isinstance(seed, int) or isinstance(seed, bool)):
        raise ValueError("run manifest seed must be an integer")
    partition = run.get("partition")
    if partition is not None and not isinstance(partition, str):
        raise ValueError("run manifest partition must be a string")
    return ExecutionIdentity(run_id, lane, case_id, seed, partition)


def validate_materialized_chain(
    documents: ExecutionDocuments,
    *,
    materialization_bundle: str | Path | None,
    attempt_claim_root: str | Path | None,
    environment_lease_root: str | Path | None,
) -> MaterializedExecutionAdmission | None:
    """Validate the host-owned materialized chain without side effects."""

    identity = _require_run_identity(documents.run)
    if identity.lane != "agent_validation" or not materialization_bundle:
        return None
    required = (
        documents.validation_request,
        documents.candidate,
        documents.materialization_receipt,
        documents.parent_agent_release,
        documents.approval,
        documents.matrix,
        documents.trial,
        documents.attempt_request,
        documents.attempt_ledger,
        documents.environment_lease,
    )
    if any(item is None for item in required):
        raise ValueError("materialized candidate execution requires the complete approved artifact chain")
    if not isinstance(attempt_claim_root, (str, Path)) or not str(attempt_claim_root):
        raise ValueError("materialized candidate execution requires a host-owned claim root")
    if not isinstance(environment_lease_root, (str, Path)) or not str(environment_lease_root):
        raise ValueError("materialized candidate execution requires a host-owned lease root")
    return MaterializedExecutionAdmission(
        documents,
        identity,
        Path(materialization_bundle),
        Path(attempt_claim_root),
        Path(environment_lease_root),
    )


def validate_campaign_chain(
    documents: ExecutionDocuments,
) -> tuple[Mapping[str, Any], Mapping[str, Any], Mapping[str, Any]] | None:
    """Return campaign documents only when the campaign lane is enabled."""

    values = (documents.campaign, documents.campaign_plan, documents.readiness_control_set)
    if all(item is None for item in values):
        return None
    if any(item is None for item in values):
        raise ValueError("campaign execution requires campaign, plan and readiness-control-set documents")
    return values[0], values[1], values[2]  # type: ignore[return-value]


def validate_campaign_execution(
    documents: ExecutionDocuments,
    *,
    environment_release_digest: str | None,
    clawgym_revision: str,
    provider_revision: str,
) -> CampaignExecutionAdmission:
    """Close campaign and authorization chains before any runtime import."""

    campaign_chain = validate_campaign_chain(documents)
    admission: tuple[str, str] | None = None
    campaign_document: Mapping[str, Any] | None = None
    if campaign_chain is not None:
        campaign_document, plan_document, readiness_control_set = campaign_chain
        deployment_lock_digest = campaign_document.get("deployment_lock_digest")
        if not isinstance(environment_release_digest, str) or not isinstance(deployment_lock_digest, str):
            raise ValueError("campaign documents must contain immutable environment and lock digests")
        from clawgym.campaign_admission import verify_reference_campaign_admission

        admission = verify_reference_campaign_admission(
            campaign_document=campaign_document,
            plan_document=plan_document,
            readiness_control_set=readiness_control_set,
            expected_clawgym_revision=clawgym_revision,
            expected_provider_revision=provider_revision,
            expected_environment_release_digest=environment_release_digest,
            expected_deployment_lock_digest=deployment_lock_digest,
        )
    authorization = documents.campaign_authorization
    if authorization is not None:
        if documents.candidate is None or documents.trial is None or documents.approval is None:
            raise ValueError("campaign execution requires candidate, trial and approval documents")
        case_id = documents.run.get("case_id", documents.trial.get("case_id"))
        seed = documents.run.get("seed", documents.trial.get("seed"))
        partition = documents.trial.get("partition")
        purpose = authorization.get("purpose")
        if (
            not isinstance(case_id, str)
            or not isinstance(seed, int)
            or isinstance(seed, bool)
            or not isinstance(partition, str)
            or not isinstance(purpose, str)
        ):
            raise ValueError("campaign authorization is missing typed trial identity")
        candidate_digest = documents.candidate.get("candidate_digest")
        trial_digest = documents.trial.get("trial_digest")
        approval_digest = documents.approval.get("approval_record_digest")
        if not all(isinstance(value, str) for value in (candidate_digest, trial_digest, approval_digest)):
            raise ValueError("campaign execution requires typed candidate, trial and approval digests")
        verify_campaign_authorization(
            authorization,
            candidate_digest=cast(str, candidate_digest),
            trial_digest=cast(str, trial_digest),
            approval_digest=cast(str, approval_digest),
            case_id=case_id,
            seed=seed,
            partition=partition,
            purpose=purpose,
            campaign_digest=authorization.get("campaign_digest"),
            generation=authorization.get("generation"),
        )
    return CampaignExecutionAdmission(campaign_document, admission)


def validate_worker_admission(
    documents: ExecutionDocuments,
    *,
    materialization_bundle: str | Path | None,
    attempt_claim_root: str | Path | None,
    environment_lease_root: str | Path | None,
) -> MaterializedExecutionAdmission | LegacyExecutionAdmission:
    """Validate common identity and return a typed admission object."""

    identity = _require_run_identity(documents.run)
    materialized = validate_materialized_chain(
        documents,
        materialization_bundle=materialization_bundle,
        attempt_claim_root=attempt_claim_root,
        environment_lease_root=environment_lease_root,
    )
    if materialized is not None:
        return materialized
    return LegacyExecutionAdmission(documents, identity)
