from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest

from clawgym_overlay.tool_grant import SREGymToolGrantDescriptor

RUN = "a" * 64
AGENT = "b" * 64
ENVIRONMENT = "c" * 64
ISSUED = "2026-01-01T00:00:00Z"


def _run(*, run: str = RUN, agent: str = AGENT, environment: str = ENVIRONMENT):
    return SimpleNamespace(
        manifest_digest=run,
        agent_release=SimpleNamespace(agent_release_digest=agent),
        environment_release=SimpleNamespace(environment_release_digest=environment),
    )


def _grant() -> SREGymToolGrantDescriptor:
    return SREGymToolGrantDescriptor.issue(
        _run(),
        provider_id="sregym.tools.v1",
        capabilities=("read.metrics", "write.namespace"),
        audience="run-scoped",
        issued_at=ISSUED,
        ttl_seconds=60,
        selector={"namespace": "default"},
    )


def test_issue_is_run_bound_and_secret_free() -> None:
    grant = _grant()
    document = grant.to_public_document()

    assert grant.matches_run(_run())
    assert not grant.matches_run(_run(run="d" * 64))
    assert not grant.matches_run(_run(agent="d" * 64))
    assert not grant.matches_run(_run(environment="d" * 64))
    assert document["grant_digest"] == grant.grant_digest
    assert document["scope_digest"]
    assert document["capabilities"] == ["read.metrics", "write.namespace"]
    assert "secret" not in str(document).lower()
    assert "kubeconfig" not in str(document).lower()
    assert grant.to_public_document(include_digest=False).get("grant_digest") is None


def test_revocation_is_immutable_and_idempotent() -> None:
    grant = _grant()
    revoked = grant.revoked("2026-01-01T00:01:00Z")

    assert grant.status == "granted"
    assert revoked.status == "revoked"
    assert revoked.revoked_at == "2026-01-01T00:01:00Z"
    assert revoked.grant_digest != grant.grant_digest
    assert revoked.revoked("2026-01-01T00:02:00Z") is revoked

    with pytest.raises(ValueError, match="canonical UTC"):
        grant.revoked("2026-01-01T00:01:00.000Z")


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"provider_id": "SREGYM"}, "identifier"),
        ({"audience": "bad audience"}, "identifier"),
        ({"capabilities": ()}, "non-empty"),
        ({"capabilities": ("read.metrics", "read.metrics")}, "non-empty"),
        ({"capabilities": ("Read",)}, "capability"),
        ({"selector": []}, "mapping"),
        ({"ttl_seconds": True}, "integer"),
        ({"ttl_seconds": 0}, "outside"),
        ({"ttl_seconds": 86401}, "outside"),
        ({"issued_at": "2026-01-01"}, "canonical UTC"),
    ],
)
def test_issue_rejects_invalid_boundary_values(kwargs: dict[str, object], message: str) -> None:
    values: dict[str, object] = {
        "provider_id": "sregym.tools.v1",
        "capabilities": ("read.metrics",),
        "audience": "run-scoped",
        "issued_at": ISSUED,
        "ttl_seconds": 60,
        "selector": {"namespace": "default"},
    }
    values.update(kwargs)
    with pytest.raises(ValueError, match=message):
        SREGymToolGrantDescriptor.issue(_run(), **values)


def test_issue_requires_all_release_identities() -> None:
    with pytest.raises(ValueError, match="agent_release_digest"):
        SREGymToolGrantDescriptor.issue(
            SimpleNamespace(
                manifest_digest=RUN,
                agent_release=SimpleNamespace(),
                environment_release=SimpleNamespace(environment_release_digest=ENVIRONMENT),
            ),
            provider_id="sregym.tools.v1",
            capabilities=("read.metrics",),
            audience="run-scoped",
            issued_at=ISSUED,
            ttl_seconds=60,
            selector={"namespace": "default"},
        )


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"status": "unknown"}, "status"),
        ({"status": "granted", "revoked_at": "2026-01-01T00:01:00Z"}, "granted"),
        ({"status": "revoked", "revoked_at": None}, "requires"),
        ({"scope": {"kind": "namespace", "audience": "run-scoped"}}, "scope"),
        ({"expires_at": ISSUED}, "expires_at"),
        ({"grant_digest": "not-a-digest"}, "grant_digest"),
    ],
)
def test_descriptor_rejects_inconsistent_state(changes: dict[str, object], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        replace(_grant(), **changes)
