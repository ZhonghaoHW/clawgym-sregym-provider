from __future__ import annotations

import copy

import pytest

from clawgym.contracts import ContractValidationError
from clawgym_overlay.deployment_lock import deployment_lock_digest, validate_deployment_lock


def lock_fixture():
    names_and_kinds = {
        "docker-engine": "package",
        "kind": "binary",
        "kubectl": "binary",
        "helm": "binary",
        "uv": "binary",
        "kind-node": "image",
        "calico-manifest": "manifest",
        "metrics-server-manifest": "manifest",
        "openebs-manifest": "manifest",
        "loki-chart": "chart",
        "runtime-image.recommendation": "image",
    }
    artifacts = []
    for index, (name, kind) in enumerate(names_and_kinds.items()):
        digest = f"sha256:{index + 1:064x}"
        source = f"https://artifacts.example.invalid/{name}/v1"
        if kind == "image":
            source = f"https://registry.example.invalid/{name}@{digest}"
        artifacts.append(
            {
                "name": name,
                "kind": kind,
                "version": "v1.0.0",
                "source": source,
                "integrity": digest,
            }
        )
    return {
        "schema_id": "clawgym.sregym_deployment_lock.v1",
        "platform": "linux-amd64",
        "artifacts": artifacts,
    }


def test_complete_lock_has_stable_digest() -> None:
    document = lock_fixture()
    validate_deployment_lock(document)
    assert deployment_lock_digest(document) == deployment_lock_digest(copy.deepcopy(document))


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda document: document["artifacts"].pop(), "runtime images"),
        (
            lambda document: document["artifacts"][0].update(version="latest"),
            "immutable",
        ),
        (
            lambda document: document["artifacts"][0].update(integrity="not-a-digest"),
            "SHA-256",
        ),
        (
            lambda document: document["artifacts"][5].update(source="https://image/v1"),
            "image source",
        ),
        (
            lambda document: document["artifacts"][0].update(source="http://mutable.invalid"),
            "HTTPS",
        ),
    ],
)
def test_lock_rejects_mutable_or_incomplete_identity(mutation, message) -> None:
    document = lock_fixture()
    mutation(document)
    with pytest.raises(ContractValidationError, match=message):
        validate_deployment_lock(document)


def test_each_artifact_changes_lock_digest() -> None:
    document = lock_fixture()
    baseline = deployment_lock_digest(document)
    for index in range(len(document["artifacts"])):
        candidate = copy.deepcopy(document)
        candidate["artifacts"][index]["version"] += ".1"
        assert deployment_lock_digest(candidate) != baseline
