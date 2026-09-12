"""Stable, secret-free SREGym environment management projection."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from typing import Any

_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_REVISION = re.compile(r"^[0-9a-f]{40,64}$")
_IDENTIFIER = re.compile(r"^[a-z][a-z0-9._-]{0,127}$")
_SCHEMA_ID = "sregym.environment_projection.v1"


class EnvironmentProjectionError(ValueError):
    """Raised when a provider projection is unsafe or inconsistent."""


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _require_digest(value: Any, field: str) -> str:
    if not isinstance(value, str) or _DIGEST.fullmatch(value) is None:
        raise EnvironmentProjectionError(f"{field} must be a lowercase SHA-256 digest")
    return value


def _require_revision(value: Any, field: str) -> str:
    if not isinstance(value, str) or _REVISION.fullmatch(value) is None:
        raise EnvironmentProjectionError(f"{field} must be a Git revision")
    return value


def _require_identifier(value: Any, field: str) -> str:
    if not isinstance(value, str) or _IDENTIFIER.fullmatch(value) is None:
        raise EnvironmentProjectionError(f"{field} must be a lowercase identifier")
    return value


def _finish(document: dict[str, Any]) -> dict[str, Any]:
    document["projection_digest"] = _digest(document)
    return document


def validate_environment_projection(document: Mapping[str, Any]) -> None:
    expected = {
        "schema_id",
        "producer_repo",
        "producer_revision",
        "evidence_scope",
        "environment_release_digest",
        "provider_id",
        "readiness",
        "lifecycle_status",
        "tool_grants",
        "lease",
        "cleanup",
        "oracle_status",
        "evidence_digests",
        "projection_digest",
    }
    if (
        set(document) != expected
        or document.get("schema_id") != _SCHEMA_ID
        or document.get("producer_repo") != "clawgym-sregym-provider"
    ):
        raise EnvironmentProjectionError("environment projection fields or identity are invalid")
    _require_revision(document["producer_revision"], "producer_revision")
    _require_digest(document["environment_release_digest"], "environment_release_digest")
    _require_digest(document["projection_digest"], "projection_digest")
    _require_identifier(document["provider_id"], "provider_id")
    if document["evidence_scope"] not in {"local_fake", "local_live_model", "live_episode"}:
        raise EnvironmentProjectionError("invalid evidence_scope")
    if document["readiness"] not in {"ready", "not_ready", "unknown"}:
        raise EnvironmentProjectionError("invalid readiness")
    if document["lifecycle_status"] not in {"idle", "reset", "faulted", "recovering", "cleaned", "failed", "unknown"}:
        raise EnvironmentProjectionError("invalid lifecycle_status")
    if document["oracle_status"] not in {"not_run", "stubbed", "available", "unknown"}:
        raise EnvironmentProjectionError("invalid oracle_status")
    if document["projection_digest"] != _digest(
        {key: value for key, value in document.items() if key != "projection_digest"}
    ):
        raise EnvironmentProjectionError("projection_digest does not match canonical content")
    seen_grants: set[str] = set()
    for grant in document["tool_grants"]:
        if set(grant) != {"grant_id", "grant_digest", "status"}:
            raise EnvironmentProjectionError("tool grant projection fields are invalid")
        if not isinstance(grant["grant_id"], str) or not grant["grant_id"] or grant["grant_id"] in seen_grants:
            raise EnvironmentProjectionError("tool grant identity is invalid or duplicated")
        seen_grants.add(grant["grant_id"])
        _require_digest(grant["grant_digest"], "grant_digest")
        if grant["status"] not in {"granted", "revoked", "expired", "denied", "unknown"}:
            raise EnvironmentProjectionError("invalid tool grant status")
    lease = document["lease"]
    if set(lease) != {"status", "lease_digest"}:
        raise EnvironmentProjectionError("lease projection fields are invalid")
    if lease["status"] not in {"not_acquired", "active", "expired", "released", "unknown"}:
        raise EnvironmentProjectionError("invalid lease status")
    if lease["lease_digest"] is not None:
        _require_digest(lease["lease_digest"], "lease_digest")
    cleanup = document["cleanup"]
    if set(cleanup) != {"status", "evidence_digests"}:
        raise EnvironmentProjectionError("cleanup projection fields are invalid")
    if cleanup["status"] not in {"not_run", "succeeded", "failed", "unknown"}:
        raise EnvironmentProjectionError("invalid cleanup status")
    if list(cleanup["evidence_digests"]) != sorted(set(cleanup["evidence_digests"])):
        raise EnvironmentProjectionError("cleanup evidence digests must be sorted and unique")
    for value in cleanup["evidence_digests"]:
        _require_digest(value, "cleanup evidence digest")
    if list(document["evidence_digests"]) != sorted(set(document["evidence_digests"])):
        raise EnvironmentProjectionError("evidence digests must be sorted and unique")
    for value in document["evidence_digests"]:
        _require_digest(value, "evidence_digest")


def build_environment_projection(
    *,
    producer_revision: str,
    environment_release_digest: str,
    provider_id: str,
    readiness: str,
    lifecycle_status: str,
    tool_grants: Sequence[Mapping[str, str]] = (),
    lease_status: str = "not_acquired",
    lease_digest: str | None = None,
    cleanup_status: str = "not_run",
    cleanup_evidence_digests: Sequence[str] = (),
    oracle_status: str = "not_run",
    evidence_digests: Sequence[str] = (),
    evidence_scope: str = "local_fake",
) -> dict[str, Any]:
    grants = sorted((dict(grant) for grant in tool_grants), key=lambda item: item.get("grant_id", ""))
    document = {
        "schema_id": _SCHEMA_ID,
        "producer_repo": "clawgym-sregym-provider",
        "producer_revision": _require_revision(producer_revision, "producer_revision"),
        "evidence_scope": evidence_scope,
        "environment_release_digest": _require_digest(environment_release_digest, "environment_release_digest"),
        "provider_id": _require_identifier(provider_id, "provider_id"),
        "readiness": readiness,
        "lifecycle_status": lifecycle_status,
        "tool_grants": grants,
        "lease": {
            "status": lease_status,
            "lease_digest": None if lease_digest is None else _require_digest(lease_digest, "lease_digest"),
        },
        "cleanup": {
            "status": cleanup_status,
            "evidence_digests": sorted(
                set(_require_digest(value, "cleanup evidence digest") for value in cleanup_evidence_digests)
            ),
        },
        "oracle_status": oracle_status,
        "evidence_digests": sorted(set(_require_digest(value, "evidence_digest") for value in evidence_digests)),
    }
    result = _finish(document)
    validate_environment_projection(result)
    return result


__all__ = ["EnvironmentProjectionError", "build_environment_projection", "validate_environment_projection"]
