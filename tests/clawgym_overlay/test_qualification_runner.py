from __future__ import annotations

import pytest

from clawgym_overlay.qualification_runner import (
    QualificationRunnerError,
    _create_variant_policy,
    _verify_trial,
)


class _Networking:
    def __init__(self):
        self.created = []

    def create_namespaced_network_policy(self, namespace, body):
        self.created.append((namespace, body))


def _trial(**overrides):
    value = {
        "schema_id": "clawgym.environment_qualification_trial.v1",
        "trial_id": "candidate-01",
        "attempt_id": "attempt-1",
        "partition": "environment_qualification",
        "target": {"kind": "NetworkPolicy", "namespace": "hotel-reservation", "name": "deny-all-recommendation"},
        "release_role": "candidate",
        "seed": 2026090111,
        "profile_digest": "a" * 64,
    }
    value.update(overrides)
    return value


def test_variant_policy_is_closed_and_deterministic():
    networking = _Networking()
    _create_variant_policy(networking, "ingress_only")
    assert networking.created[0][0] == "hotel-reservation"
    assert networking.created[0][1]["spec"]["policyTypes"] == ["Ingress"]
    assert "egress" not in networking.created[0][1]["spec"]

    networking = _Networking()
    _create_variant_policy(networking, "ingress_egress")
    assert networking.created[0][1]["spec"]["policyTypes"] == ["Ingress", "Egress"]
    assert networking.created[0][1]["spec"]["egress"] == []


@pytest.mark.parametrize(
    "change",
    [
        {"target": {"kind": "Service"}},
        {"partition": "validation"},
        {"profile_digest": "not-a-digest"},
        {"seed": True},
    ],
)
def test_trial_scope_is_fail_closed(change):
    with pytest.raises(QualificationRunnerError):
        _verify_trial(_trial(**change))


def test_unknown_fault_variant_is_rejected():
    with pytest.raises(QualificationRunnerError):
        _create_variant_policy(_Networking(), "delete-everything")
