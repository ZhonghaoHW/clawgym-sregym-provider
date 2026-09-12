"""Secret-free, run-bound projections of SREGym tool grants.

The live kubeconfig remains inside the provider's opaque access handle.  Only
this validated projection may cross the provider boundary or be written as
run evidence.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from clawgym.contracts import RunManifest, sha256_digest

_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_IDENTIFIER = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$")
_CAPABILITY = re.compile(r"^[a-z][a-z0-9._:-]{0,127}$")
_MAX_TTL_SECONDS = 24 * 60 * 60


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_timestamp(value: object, field: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a UTC timestamp")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError as exc:
        raise ValueError(f"{field} must be a canonical UTC timestamp") from exc
    if parsed.strftime("%Y-%m-%dT%H:%M:%SZ") != value:
        raise ValueError(f"{field} must be a canonical UTC timestamp")
    return parsed


def _digest(value: Any, field: str) -> str:
    if not isinstance(value, str) or _DIGEST.fullmatch(value) is None:
        raise ValueError(f"{field} must be a lowercase SHA-256 digest")
    return value


def _identifier(value: Any, field: str) -> str:
    if not isinstance(value, str) or _IDENTIFIER.fullmatch(value) is None:
        raise ValueError(f"{field} is not a valid identifier")
    return value


def _capabilities(values: tuple[str, ...]) -> tuple[str, ...]:
    if not values or len(values) != len(set(values)):
        raise ValueError("tool grant capabilities must be non-empty and unique")
    if any(_CAPABILITY.fullmatch(value) is None for value in values):
        raise ValueError("tool grant capability is invalid")
    return values


@dataclass(frozen=True, slots=True)
class SREGymToolGrantDescriptor:
    """The public ``clawgym.tool_grant.v1`` projection of one grant."""

    grant_id: str
    run_manifest_digest: str
    agent_release_digest: str
    environment_release_digest: str
    provider_id: str
    capabilities: tuple[str, ...]
    scope: Mapping[str, str]
    issued_at: str
    expires_at: str
    status: str = "granted"
    revoked_at: str | None = None
    grant_digest: str = ""

    @classmethod
    def issue(
        cls,
        run_manifest: RunManifest,
        *,
        provider_id: str,
        capabilities: tuple[str, ...],
        audience: str,
        issued_at: str,
        ttl_seconds: object,
        selector: object,
    ) -> SREGymToolGrantDescriptor:
        run_digest = _digest(getattr(run_manifest, "manifest_digest", None), "run_manifest_digest")
        agent_release = getattr(run_manifest, "agent_release", None)
        environment_release = getattr(run_manifest, "environment_release", None)
        agent_digest = _digest(getattr(agent_release, "agent_release_digest", None), "agent_release_digest")
        environment_digest = _digest(
            getattr(environment_release, "environment_release_digest", None),
            "environment_release_digest",
        )
        provider_id = _identifier(provider_id, "provider_id")
        audience = _identifier(audience, "scope.audience")
        capabilities = _capabilities(tuple(capabilities))
        if not isinstance(selector, Mapping):
            raise ValueError("tool grant selector must be a mapping")
        if isinstance(ttl_seconds, bool) or not isinstance(ttl_seconds, int):
            raise ValueError("tool grant TTL must be an integer")
        if not 1 <= ttl_seconds <= _MAX_TTL_SECONDS:
            raise ValueError("tool grant TTL is outside the bounded range")
        issued = _parse_timestamp(issued_at, "issued_at")
        selector_mapping = cast(Mapping[str, Any], selector)
        scope = {
            "kind": "namespace",
            "audience": audience,
            "selector_digest": sha256_digest(dict(selector_mapping)),
        }
        descriptor = cls(
            grant_id=f"grant-{run_digest[:24]}",
            run_manifest_digest=run_digest,
            agent_release_digest=agent_digest,
            environment_release_digest=environment_digest,
            provider_id=provider_id,
            capabilities=capabilities,
            scope=scope,
            issued_at=issued_at,
            expires_at=(issued + timedelta(seconds=ttl_seconds)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        )
        return descriptor._with_digest()

    def __post_init__(self) -> None:
        _identifier(self.grant_id, "grant_id")
        _digest(self.run_manifest_digest, "run_manifest_digest")
        _digest(self.agent_release_digest, "agent_release_digest")
        _digest(self.environment_release_digest, "environment_release_digest")
        _identifier(self.provider_id, "provider_id")
        _capabilities(self.capabilities)
        if set(self.scope) != {"kind", "audience", "selector_digest"}:
            raise ValueError("tool grant scope has invalid fields")
        if self.scope["kind"] not in {"environment", "namespace", "resource"}:
            raise ValueError("tool grant scope kind is invalid")
        _identifier(self.scope["audience"], "scope.audience")
        _digest(self.scope["selector_digest"], "scope.selector_digest")
        issued = _parse_timestamp(self.issued_at, "issued_at")
        expires = _parse_timestamp(self.expires_at, "expires_at")
        if expires <= issued:
            raise ValueError("tool grant expires_at must be after issued_at")
        if self.status not in {"granted", "revoked"}:
            raise ValueError("tool grant status is invalid")
        if self.status == "granted" and self.revoked_at is not None:
            raise ValueError("granted tool grant cannot have revoked_at")
        if self.status == "revoked":
            if self.revoked_at is None:
                raise ValueError("revoked tool grant requires revoked_at")
            _parse_timestamp(self.revoked_at, "revoked_at")
        if self.grant_digest:
            _digest(self.grant_digest, "grant_digest")

    def matches_run(self, run_manifest: RunManifest) -> bool:
        return (
            self.run_manifest_digest == getattr(run_manifest, "manifest_digest", None)
            and self.agent_release_digest
            == getattr(getattr(run_manifest, "agent_release", None), "agent_release_digest", None)
            and self.environment_release_digest
            == getattr(
                getattr(run_manifest, "environment_release", None),
                "environment_release_digest",
                None,
            )
        )

    def revoked(self, revoked_at: str) -> SREGymToolGrantDescriptor:
        if self.status == "revoked":
            return self
        _parse_timestamp(revoked_at, "revoked_at")
        return SREGymToolGrantDescriptor(
            grant_id=self.grant_id,
            run_manifest_digest=self.run_manifest_digest,
            agent_release_digest=self.agent_release_digest,
            environment_release_digest=self.environment_release_digest,
            provider_id=self.provider_id,
            capabilities=self.capabilities,
            scope=self.scope,
            issued_at=self.issued_at,
            expires_at=self.expires_at,
            status="revoked",
            revoked_at=revoked_at,
        )._with_digest()

    def _with_digest(self) -> SREGymToolGrantDescriptor:
        document = self.to_public_document(include_digest=False)
        return SREGymToolGrantDescriptor(
            grant_id=self.grant_id,
            run_manifest_digest=self.run_manifest_digest,
            agent_release_digest=self.agent_release_digest,
            environment_release_digest=self.environment_release_digest,
            provider_id=self.provider_id,
            capabilities=self.capabilities,
            scope=dict(self.scope),
            issued_at=self.issued_at,
            expires_at=self.expires_at,
            status=self.status,
            revoked_at=self.revoked_at,
            grant_digest=sha256_digest(document),
        )

    def to_public_document(self, *, include_digest: bool = True) -> dict[str, Any]:
        document: dict[str, Any] = {
            "schema_id": "clawgym.tool_grant.v1",
            "grant_id": self.grant_id,
            "run_manifest_digest": self.run_manifest_digest,
            "agent_release_digest": self.agent_release_digest,
            "environment_release_digest": self.environment_release_digest,
            "provider_id": self.provider_id,
            "capabilities": list(self.capabilities),
            "scope": dict(self.scope),
            "scope_digest": sha256_digest(dict(self.scope)),
            "issued_at": self.issued_at,
            "expires_at": self.expires_at,
            "status": self.status,
            "revoked_at": self.revoked_at,
        }
        if include_digest:
            document["grant_digest"] = self.grant_digest
        return document


__all__ = ["SREGymToolGrantDescriptor", "utc_now"]
