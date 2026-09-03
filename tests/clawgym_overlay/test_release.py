from __future__ import annotations

import copy
import runpy
from pathlib import Path

import pytest
from clawgym.contracts import ContractValidationError

from clawgym_overlay.release import (
    MANIFEST_FILENAMES,
    SREGymReleaseBuilder,
    build_environment_release,
    load_release_manifests,
    provider_configuration_digests,
)

ROOT = Path(__file__).resolve().parents[2]
MANIFEST_ROOT = ROOT / "clawgym_overlay" / "manifests"
SREGYM_LITE_PROBLEMS = runpy.run_path(ROOT / "sregym" / "conductor" / "problem_sets.py")["SREGYM_LITE_PROBLEMS"]


def manifests():
    return load_release_manifests(MANIFEST_ROOT)


def test_suite_manifest_matches_pinned_sregym_lite_registry() -> None:
    documents = manifests()
    assert tuple(documents["suite"]["problems"]) == SREGYM_LITE_PROBLEMS
    assert documents["problem"]["problem_id"] == "network_policy_block"
    assert documents["partition"]["partition"] == "train"
    assert documents["problem"]["task_stages"] == ["mitigation"]


def test_release_identity_is_stable_and_configuration_is_content_addressed() -> None:
    documents = manifests()
    left = build_environment_release(overlay_revision="a" * 40, manifests=documents)
    right = build_environment_release(overlay_revision="a" * 40, manifests=documents)
    assert left.to_dict() == right.to_dict()
    digests = provider_configuration_digests(documents)
    assert set(digests) == {
        "sregym.environment.v1",
        "sregym.oracle.v1",
        "sregym.filtered-tools.v1",
        "sregym.observation.v1",
        "sregym.container-execution.v1",
    }
    assert all(len(digest) == 64 for digest in digests.values())


@pytest.mark.parametrize("kind", tuple(MANIFEST_FILENAMES))
def test_each_environment_dimension_changes_release_identity(kind: str) -> None:
    documents = manifests()
    baseline = build_environment_release(overlay_revision="a" * 40, manifests=documents)
    candidate = copy.deepcopy(documents)
    if kind == "suite":
        candidate[kind]["suite_id"] = "sregym-lite-candidate"
    elif kind == "problem":
        candidate[kind]["application_id"] = "hotel-reservation-candidate"
    elif kind == "partition":
        candidate[kind]["purpose"] = "provider-contract-candidate"
    elif kind == "fault":
        candidate[kind]["mechanism"] = "candidate-network-policy"
    elif kind == "oracle":
        candidate[kind]["oracle_id"] = "candidate-mitigation"
    elif kind == "tool":
        candidate[kind]["trajectory_durability"] = "candidate-host-retained-json"
    elif kind == "observation":
        candidate[kind]["causal_signal"] = "candidate-signal"
    else:
        candidate[kind]["timeout_seconds"] += 1
    evolved = build_environment_release(overlay_revision="a" * 40, manifests=candidate)
    assert evolved.environment_release_digest != baseline.environment_release_digest


def test_manifest_rejects_extra_and_forbidden_content(tmp_path: Path) -> None:
    for filename in MANIFEST_FILENAMES.values():
        (tmp_path / filename).write_bytes((MANIFEST_ROOT / filename).read_bytes())
    problem_path = tmp_path / MANIFEST_FILENAMES["problem"]
    document = __import__("json").loads(problem_path.read_text())
    document["credential_path"] = "/tmp/secret"
    problem_path.write_text(__import__("json").dumps(document))
    with pytest.raises(ContractValidationError):
        load_release_manifests(tmp_path)


@pytest.mark.parametrize(
    ("kind", "mutate"),
    [
        ("suite", lambda d: d.update(upstream_revision="bad")),
        ("suite", lambda d: d.update(problems=["network_policy_block", "network_policy_block"])),
        ("suite", lambda d: d.update(submodules={})),
        ("problem", lambda d: d.update(task_stages=["unsupported"])),
        ("partition", lambda d: d.update(partition="hidden")),
        ("fault", lambda d: d.update(steady_state={})),
        (
            "fault",
            lambda d: d.update(
                steady_state={"signal": "x", "baseline_window_seconds": 1, "minimum_success_ratio": "0.5"}
            ),
        ),
        ("fault", lambda d: d.update(max_experiment_duration_seconds=1)),
        ("fault", lambda d: d.update(abort_conditions=[])),
        ("fault", lambda d: d.update(rollback_order=[])),
        ("fault", lambda d: d.update(cleanup_failure_policy="continue")),
        ("oracle", lambda d: d.update(verdict_policy="agent-reported")),
        ("tool", lambda d: d.update(interfaces=[])),
        ("observation", lambda d: d.update(capture_windows=["baseline"])),
        ("execution", lambda d: d.update(timeout_seconds=0)),
        ("execution", lambda d: d.update(deployment_cache_policy="reuse")),
        ("execution", lambda d: d.update(runtime_image_policy="any")),
        ("execution", lambda d: d.update(deployment_lock_digest="bad")),
        ("execution", lambda d: d.update(kind_topology_sha256="bad")),
    ],
)
def test_manifest_rejects_invalid_dimension_values(kind: str, mutate) -> None:
    """Every release dimension must fail closed on its reviewed grammar."""
    documents = manifests()
    mutate(documents[kind])
    with pytest.raises(ContractValidationError):
        build_environment_release(overlay_revision="a" * 40, manifests=documents)


def test_repository_builder_rejects_dirty_checkout(monkeypatch) -> None:
    builder = SREGymReleaseBuilder(ROOT)
    monkeypatch.setattr(builder, "_git", lambda *arguments: " M tracked.py")
    with pytest.raises(ContractValidationError, match="clean provider checkout"):
        builder.verify_repository()


def test_git_output_preserves_submodule_status_prefix(monkeypatch) -> None:
    builder = SREGymReleaseBuilder(ROOT)

    class Completed:
        stdout = " abc123 submodule\n"

    monkeypatch.setattr("clawgym_overlay.release.subprocess.run", lambda *args, **kwargs: Completed())
    assert builder._git("submodule", "status") == " abc123 submodule"
