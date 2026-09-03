"""Typed, read-only host observation for WP8.4A.

The collector consumes already-sanitized booleans supplied by an explicit
host integration.  It does not execute kubectl, inspect credentials, or infer
readiness from a caller-provided attestation.  This keeps the provider side
safe to use in offline tests while giving ClawGym a signed observation source
for live readiness checks.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any, cast

from clawgym.contracts import sha256_digest


class PlatformObservationError(ValueError):
    """Raised when a host observation is not safe to attest."""


_REVISION = re.compile(r"^[a-f0-9]{40,64}$")
_DIGEST = re.compile(r"^[a-f0-9]{64}$")
_CHECKS = (
    "nodes_ready",
    "baseline_namespaces_only",
    "agent_containers_absent",
    "leases_absent",
    "candidate_resources_absent",
    "temporary_access_material_absent",
)


def build_platform_host_observation(
    *,
    collector_revision: str,
    source_digest: str,
    checks: Mapping[str, bool],
    observed_at: str | None = None,
) -> dict[str, Any]:
    """Create canonical observation data from an explicit sanitized check set."""
    if not _REVISION.fullmatch(collector_revision):
        raise PlatformObservationError("collector revision is not a pinned revision")
    if not _DIGEST.fullmatch(source_digest):
        raise PlatformObservationError("source digest is not a SHA-256 digest")
    if set(checks) != set(_CHECKS) or any(type(checks[name]) is not bool for name in _CHECKS):
        raise PlatformObservationError("host checks must be the fixed boolean set")
    timestamp = observed_at or datetime.now().astimezone().isoformat(timespec="seconds")
    if not timestamp or "\r" in timestamp or "\n" in timestamp:
        raise PlatformObservationError("observation timestamp is invalid")
    value: dict[str, Any] = {
        "schema_id": "clawgym.platform_host_observation.v1",
        "collector_revision": collector_revision,
        "observed_at": timestamp,
        "source_digest": source_digest,
        "checks": {name: checks[name] for name in _CHECKS},
    }
    value["observation_digest"] = sha256_digest(value)
    return value


def require_clean_observation(observation: Mapping[str, Any]) -> dict[str, Any]:
    """Fail closed unless every required host check is explicitly true."""
    if observation.get("schema_id") != "clawgym.platform_host_observation.v1":
        raise PlatformObservationError("observation schema mismatch")
    if not isinstance(observation.get("checks"), Mapping):
        raise PlatformObservationError("observation checks are missing")
    expected = build_platform_host_observation(
        collector_revision=observation.get("collector_revision", ""),
        source_digest=observation.get("source_digest", ""),
        checks=observation["checks"],
        observed_at=observation.get("observed_at"),
    )
    if observation.get("observation_digest") != expected["observation_digest"]:
        raise PlatformObservationError("observation digest mismatch")
    if not all(observation["checks"].values()):
        raise PlatformObservationError("host is not clean")
    return dict(observation)


def collect_platform_host_observation(
    *,
    source_path: str | Path,
    collector_revision: str,
    observed_at: str | None = None,
) -> dict[str, Any]:
    """Convert one explicit, sanitized host-status JSON into an observation.

    The caller supplies a file produced by the pinned host collector.  This
    function deliberately does not execute kubectl or inspect credentials;
    it validates the bounded input shape, hashes the source bytes, and then
    lets :func:`build_platform_host_observation` create the attested record.
    """
    source = Path(source_path)
    # Check the explicit source before resolving it; otherwise a symlink would
    # lose its identity and become an apparently safe regular file.
    if source.is_symlink():
        raise PlatformObservationError("host observation source must be a regular file")
    try:
        path = source.resolve(strict=True)
    except OSError as exc:
        raise PlatformObservationError("host observation source is unavailable") from exc
    if path.is_symlink() or not path.is_file():
        raise PlatformObservationError("host observation source must be a regular file")
    payload = path.read_bytes()
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PlatformObservationError("host observation source is invalid JSON") from exc
    if not isinstance(value, Mapping):
        raise PlatformObservationError("host observation source is not the fixed sanitized check set")
    typed_value = cast(Mapping[str, Any], value)
    if set(typed_value) != set(_CHECKS) or any(type(typed_value[name]) is not bool for name in _CHECKS):
        raise PlatformObservationError("host observation source is not the fixed sanitized check set")
    return build_platform_host_observation(
        collector_revision=collector_revision,
        source_digest=hashlib.sha256(payload).hexdigest(),
        checks={name: bool(typed_value[name]) for name in _CHECKS},
        observed_at=observed_at,
    )


__all__ = [
    "PlatformObservationError",
    "build_platform_host_observation",
    "collect_platform_host_observation",
    "require_clean_observation",
]
