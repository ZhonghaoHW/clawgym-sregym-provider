from __future__ import annotations

import pytest

from clawgym_overlay.projection import (
    EnvironmentProjectionError,
    build_environment_projection,
    validate_environment_projection,
)


def test_environment_projection_is_deterministic_and_secret_free() -> None:
    kwargs = {
        "producer_revision": "a" * 40,
        "environment_release_digest": "b" * 64,
        "provider_id": "sregym.environment.v1",
        "readiness": "ready",
        "lifecycle_status": "cleaned",
        "tool_grants": ({"grant_id": "grant-b", "grant_digest": "d" * 64, "status": "revoked"}, {"grant_id": "grant-a", "grant_digest": "c" * 64, "status": "granted"}),
        "evidence_digests": ("e" * 64,),
    }
    first = build_environment_projection(**kwargs)
    second = build_environment_projection(**kwargs)
    assert first == second
    assert "kubeconfig" not in str(first).lower()
    validate_environment_projection(first)


def test_environment_projection_rejects_digest_conflicts() -> None:
    document = build_environment_projection(
        producer_revision="a" * 40,
        environment_release_digest="b" * 64,
        provider_id="sregym.environment.v1",
        readiness="unknown",
        lifecycle_status="unknown",
    )
    document["readiness"] = "ready"
    with pytest.raises(EnvironmentProjectionError):
        validate_environment_projection(document)


def test_environment_projection_carries_lease_and_cleanup_summary() -> None:
    projection = build_environment_projection(
        producer_revision="a" * 40,
        environment_release_digest="b" * 64,
        provider_id="sregym.environment.v1",
        readiness="ready",
        lifecycle_status="cleaned",
        lease_status="released",
        lease_digest="c" * 64,
        cleanup_status="succeeded",
        cleanup_evidence_digests=["e" * 64],
    )
    assert projection["lease"]["status"] == "released"
    assert projection["cleanup"]["status"] == "succeeded"
