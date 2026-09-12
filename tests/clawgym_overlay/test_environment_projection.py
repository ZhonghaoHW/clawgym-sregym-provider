from __future__ import annotations

import pytest
from clawgym.contracts import sha256_digest

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
        "tool_grants": (
            {"grant_id": "grant-b", "grant_digest": "d" * 64, "status": "revoked"},
            {"grant_id": "grant-a", "grant_digest": "c" * 64, "status": "granted"},
        ),
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


def _refresh(document: dict) -> dict:
    document["projection_digest"] = sha256_digest(
        {key: value for key, value in document.items() if key != "projection_digest"}
    )
    return document


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("evidence_scope", "not-a-scope", "evidence_scope"),
        ("readiness", "broken", "readiness"),
        ("lifecycle_status", "broken", "lifecycle_status"),
        ("oracle_status", "broken", "oracle_status"),
        ("provider_id", "", "identifier"),
    ],
)
def test_environment_projection_rejects_invalid_scalar_fields(field: str, value: str, message: str) -> None:
    document = build_environment_projection(
        producer_revision="a" * 40,
        environment_release_digest="b" * 64,
        provider_id="sregym.environment.v1",
        readiness="ready",
        lifecycle_status="cleaned",
    )
    document[field] = value
    _refresh(document)
    with pytest.raises(EnvironmentProjectionError, match=message):
        validate_environment_projection(document)


def test_environment_projection_rejects_unknown_top_level_fields() -> None:
    document = build_environment_projection(
        producer_revision="a" * 40,
        environment_release_digest="b" * 64,
        provider_id="sregym.environment.v1",
        readiness="ready",
        lifecycle_status="idle",
    )
    document["unexpected"] = True
    with pytest.raises(EnvironmentProjectionError, match="fields"):
        validate_environment_projection(document)


@pytest.mark.parametrize(
    ("section", "value", "message"),
    [
        ("tool_grants", [{"grant_id": "g", "grant_digest": "bad", "status": "granted"}], "grant_digest"),
        ("tool_grants", [{"grant_id": "g", "grant_digest": "a" * 64, "status": "wrong"}], "tool grant status"),
        (
            "tool_grants",
            [{"grant_id": "g"}, {"grant_id": "g", "grant_digest": "a" * 64, "status": "granted"}],
            "fields",
        ),
        ("lease", {"status": "wrong", "lease_digest": None}, "lease status"),
        ("lease", {"status": "active", "lease_digest": "bad"}, "lease_digest"),
        ("cleanup", {"status": "wrong", "evidence_digests": []}, "cleanup status"),
        ("cleanup", {"status": "failed", "evidence_digests": ["b" * 64, "a" * 64]}, "sorted"),
        ("evidence_digests", ["bad"], "evidence_digest"),
    ],
)
def test_environment_projection_rejects_nested_inconsistency(section: str, value: object, message: str) -> None:
    document = build_environment_projection(
        producer_revision="a" * 40,
        environment_release_digest="b" * 64,
        provider_id="sregym.environment.v1",
        readiness="ready",
        lifecycle_status="idle",
    )
    document[section] = value
    _refresh(document)
    with pytest.raises(EnvironmentProjectionError, match=message):
        validate_environment_projection(document)
