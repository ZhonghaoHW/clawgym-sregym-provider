"""Shared R1c handoff protocol used inside and outside the agent container."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any, cast

MARKER = "R1C_HANDOFF_JSON"
TARGET = {
    "kind": "NetworkPolicy",
    "namespace": "hotel-reservation",
    "name": "deny-all-recommendation",
}
REQUIRED_FIELDS = (
    "symptom",
    "target_component",
    "evidence",
    "root_cause_hypothesis",
    "candidate_resource",
    "minimal_remediation",
    "verification_plan",
)
_CANONICAL_FIELDS = {
    "schema_id",
    "status",
    "run_manifest_digest",
    "agent_release_digest",
    "stage",
    *REQUIRED_FIELDS,
    "handoff_digest",
}


def _is_digest(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(char in "0123456789abcdef" for char in value)


def _digest_document(document: Mapping[str, Any], field: str) -> str:
    payload = {key: value for key, value in document.items() if key != field}
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


def marker_payload(value: str) -> dict[str, Any] | None:
    """Parse one complete marker-prefixed JSON payload."""

    candidate = value.strip()
    if not candidate.startswith(MARKER):
        return None
    payload = candidate[len(MARKER) :].lstrip(" \t\r\n:")
    try:
        parsed, end = json.JSONDecoder().raw_decode(payload)
    except json.JSONDecodeError:
        return None
    if payload[end:].strip():
        return None
    return cast(dict[str, Any], parsed) if isinstance(parsed, Mapping) else None


def string_list(value: object) -> list[str] | None:
    if not isinstance(value, list) or not value:
        return None
    items = cast(list[Any], value)
    if not all(isinstance(item, str) and item.strip() for item in items):
        return None
    return [item.strip() for item in items]


def normalise_payload(
    value: Mapping[str, Any], *, run_manifest_digest: str, agent_release_digest: str
) -> dict[str, Any] | None:
    """Bind one model payload to the current run and exact remediation target."""

    if (
        set(value) != set(REQUIRED_FIELDS)
        or not _is_digest(run_manifest_digest)
        or not _is_digest(agent_release_digest)
    ):
        return None
    text_fields = ("symptom", "target_component", "root_cause_hypothesis", "minimal_remediation")
    if not all(isinstance(value[field], str) and value[field].strip() for field in text_fields):
        return None
    candidate_resource = value.get("candidate_resource")
    if not isinstance(candidate_resource, Mapping):
        return None
    candidate_resource_object = cast(Mapping[str, Any], candidate_resource)
    if dict(candidate_resource_object) != TARGET:
        return None
    evidence = string_list(value["evidence"])
    verification_plan = string_list(value["verification_plan"])
    if evidence is None or verification_plan is None:
        return None
    document: dict[str, Any] = {
        "schema_id": "clawgym.sregym_diagnosis_handoff.v1",
        "status": "complete",
        "run_manifest_digest": run_manifest_digest,
        "agent_release_digest": agent_release_digest,
        "stage": "diagnosis",
        "symptom": cast(str, value["symptom"]).strip(),
        "target_component": cast(str, value["target_component"]).strip(),
        "evidence": evidence,
        "root_cause_hypothesis": cast(str, value["root_cause_hypothesis"]).strip(),
        "candidate_resource": dict(TARGET),
        "minimal_remediation": cast(str, value["minimal_remediation"]).strip(),
        "verification_plan": verification_plan,
    }
    document["handoff_digest"] = _digest_document(document, "handoff_digest")
    return document


def normalise_submission(
    submission: str, *, run_manifest_digest: str, agent_release_digest: str
) -> dict[str, Any] | None:
    payload = marker_payload(submission)
    return (
        normalise_payload(
            payload,
            run_manifest_digest=run_manifest_digest,
            agent_release_digest=agent_release_digest,
        )
        if payload is not None
        else None
    )


def incomplete_handoff(*, run_manifest_digest: str, agent_release_digest: str) -> dict[str, Any]:
    document: dict[str, Any] = {
        "schema_id": "clawgym.sregym_diagnosis_handoff.v1",
        "status": "incomplete",
        "run_manifest_digest": run_manifest_digest,
        "agent_release_digest": agent_release_digest,
        "stage": "diagnosis",
        "symptom": "",
        "target_component": "",
        "evidence": [],
        "root_cause_hypothesis": "",
        "candidate_resource": {"kind": "", "namespace": "", "name": ""},
        "minimal_remediation": "",
        "verification_plan": [],
    }
    document["handoff_digest"] = _digest_document(document, "handoff_digest")
    return document


def validate_document(document: Mapping[str, Any], *, run_manifest_digest: str, agent_release_digest: str) -> bool:
    """Validate a canonical host-bound handoff without accepting extra fields."""

    if set(document) != _CANONICAL_FIELDS:
        return False
    if (
        document.get("schema_id") != "clawgym.sregym_diagnosis_handoff.v1"
        or document.get("status") != "complete"
        or document.get("stage") != "diagnosis"
        or document.get("run_manifest_digest") != run_manifest_digest
        or document.get("agent_release_digest") != agent_release_digest
        or not _is_digest(run_manifest_digest)
        or not _is_digest(agent_release_digest)
    ):
        return False
    expected = normalise_payload(
        {field: document[field] for field in REQUIRED_FIELDS},
        run_manifest_digest=run_manifest_digest,
        agent_release_digest=agent_release_digest,
    )
    return expected is not None and dict(document) == expected
