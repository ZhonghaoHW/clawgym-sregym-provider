from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from clawgym.contracts import ContractValidationError
from clawgym_overlay.deployment_lock import (
    deployment_lock_digest,
    load_deployment_lock,
    validate_deployment_lock,
)
from clawgym_overlay.worker import verify_formal_kind_topology, verify_release_revisions


ROOT = Path(__file__).resolve().parents[2]


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
        "promtail-chart": "chart",
        "runtime-image.application.recommendation": "image",
        "runtime-image.mcp-server": "image",
        "runtime-image.workload": "image",
        "runtime-image.probe": "image",
        "runtime-image.infrastructure.metrics": "image",
        "kind-bundled-image.coredns": "image",
        "kind-bundled-image.etcd": "image",
        "kind-bundled-image.kube-apiserver": "image",
        "kind-bundled-image.kube-controller-manager": "image",
        "kind-bundled-image.kube-scheduler": "image",
        "kind-bundled-image.local-path-provisioner": "image",
    }
    artifacts = []
    for index, (name, kind) in enumerate(names_and_kinds.items()):
        digest = f"sha256:{index + 1:064x}"
        source = f"https://artifacts.example.invalid/{name}/v1"
        if kind == "image":
            source = f"oci://registry.example.invalid/{name}@{digest}"
        artifacts.append(
            {
                "name": name,
                "kind": kind,
                "version": "v1.0.0",
                "source": source,
                "integrity": digest,
                "target": name,
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
        (
            lambda document: document.update(
                artifacts=[
                    item
                    for item in document["artifacts"]
                    if not item["name"].startswith("runtime-image.")
                ]
            ),
            "missing artifacts",
        ),
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
            "OCI identity",
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


def test_committed_wp4_lock_is_valid_and_bound_by_execution_profile() -> None:
    document = load_deployment_lock(ROOT / "clawgym_overlay/deployment.wp4.lock.json")
    profile = json.loads(
        (ROOT / "clawgym_overlay/manifests/execution.sregym-container.v1.json").read_text()
    )

    assert len(document["artifacts"]) == 50
    assert profile["deployment_lock_digest"] == deployment_lock_digest(document)
    openebs_images = [
        item
        for item in document["artifacts"]
        if item["name"].startswith("runtime-image.infrastructure.openebs-")
    ]
    assert len(openebs_images) == 5
    assert all(item["source"].startswith("oci://quay.io/openebs/") for item in openebs_images)
    assert all(item["integrity"] == item["source"].rsplit("@", 1)[1] for item in openebs_images)


def test_formal_kind_topology_is_content_addressed_by_execution_profile() -> None:
    import hashlib

    profile = json.loads(
        (ROOT / "clawgym_overlay/manifests/execution.sregym-container.v1.json").read_text()
    )
    topology = (ROOT / "clawgym_overlay/kind.wp4.formal.yaml").read_bytes()

    assert profile["kind_topology_sha256"] == hashlib.sha256(topology).hexdigest()
    assert topology.count(b"role: worker") == 3
    assert topology.count(b"role: control-plane") == 1
    assert b"disableDefaultCNI: true" in topology
    assert verify_formal_kind_topology(ROOT, profile).name == "kind.wp4.formal.yaml"

    profile["kind_topology_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="topology"):
        verify_formal_kind_topology(ROOT, profile)


def test_worker_accepts_wp1_runtime_reference_wire_field() -> None:
    revision = "a" * 40
    verify_release_revisions(
        {"runtime_reference": {"kind": "source_revision", "reference": revision}},
        {"overlay_revision": revision},
        revision,
    )


@pytest.mark.parametrize("field", ["value", "revision", "path"])
def test_worker_rejects_non_contract_runtime_reference_fields(field: str) -> None:
    revision = "a" * 40
    with pytest.raises(ValueError, match="AgentRelease"):
        verify_release_revisions(
            {"runtime_reference": {"kind": "source_revision", field: revision}},
            {"overlay_revision": revision},
            revision,
        )
