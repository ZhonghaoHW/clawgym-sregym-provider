"""Build immutable ClawGym EnvironmentRelease identities from overlay manifests."""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Final

from clawgym.contracts import EnvironmentRelease, sha256_digest
from clawgym.contracts.validation import ContractValidationError, reject_forbidden_content


UPSTREAM_REVISION: Final = "ba07faf1a322f9b6d4a279643bb796aa2f36f64b"
ENVIRONMENT_PROVIDER_ID: Final = "sregym.environment.v1"

MANIFEST_FILENAMES: Final[dict[str, str]] = {
    "suite": "suite.sregym-lite.v1.json",
    "problem": "problem.network-policy-block.v1.json",
    "partition": "partition.sregym-lite-train-integration.v1.json",
    "fault": "fault.network-policy-block.v1.json",
    "oracle": "oracle.network-policy-mitigation.v1.json",
    "tool": "tool.filtered-kubernetes-mcp.v1.json",
    "observation": "observation.sregym-telemetry.v1.json",
    "execution": "execution.sregym-container.v1.json",
}

_SCHEMAS: Final[dict[str, tuple[str, frozenset[str]]]] = {
    "suite": (
        "clawgym.sregym_suite_manifest.v1",
        frozenset({"schema_id", "suite_id", "upstream_revision", "problems", "submodules"}),
    ),
    "problem": (
        "clawgym.sregym_problem_manifest.v1",
        frozenset(
            {
                "schema_id",
                "suite_id",
                "problem_id",
                "application_id",
                "task_stages",
            }
        ),
    ),
    "partition": (
        "clawgym.sregym_partition_manifest.v1",
        frozenset({"schema_id", "partition_id", "partition", "purpose", "problems"}),
    ),
    "fault": (
        "clawgym.sregym_fault_profile.v1",
        frozenset(
            {
                "schema_id",
                "fault_id",
                "problem_id",
                "mechanism",
                "target_component",
                "seed_behavior",
                "steady_state",
                "max_experiment_duration_seconds",
                "abort_conditions",
                "rollback_order",
                "cleanup_failure_policy",
            }
        ),
    ),
    "oracle": (
        "clawgym.sregym_oracle_profile.v1",
        frozenset(
            {
                "schema_id",
                "oracle_id",
                "problem_id",
                "required_stages",
                "verdict_policy",
            }
        ),
    ),
    "tool": (
        "clawgym.sregym_tool_profile.v1",
        frozenset(
            {
                "schema_id",
                "tool_profile_id",
                "interfaces",
                "capabilities",
                "denied_namespaces",
                "trajectory_durability",
            }
        ),
    ),
    "observation": (
        "clawgym.sregym_observation_profile.v1",
        frozenset(
            {
                "schema_id",
                "observation_profile_id",
                "sources",
                "capture_windows",
                "causal_signal",
            }
        ),
    ),
    "execution": (
        "clawgym.sregym_execution_profile.v1",
        frozenset(
            {
                "schema_id",
                "execution_profile_id",
                "backend",
                "isolation",
                "timeout_seconds",
                "deployment_cache_policy",
                "runtime_image_policy",
                "deployment_lock_digest",
            }
        ),
    ),
}


def _string(value: Any, location: str) -> str:
    if not isinstance(value, str) or not value:
        raise ContractValidationError(f"{location} must be a non-empty string")
    return value


def _string_list(value: Any, location: str) -> list[str]:
    if not isinstance(value, list) or not value or any(not isinstance(item, str) or not item for item in value):
        raise ContractValidationError(f"{location} must be a non-empty string array")
    if len(value) != len(set(value)):
        raise ContractValidationError(f"{location} must not contain duplicates")
    return value


def _validate_manifest(kind: str, document: Mapping[str, Any]) -> None:
    schema_id, expected_fields = _SCHEMAS[kind]
    if set(document) != expected_fields:
        missing = sorted(expected_fields - set(document))
        extra = sorted(set(document) - expected_fields)
        raise ContractValidationError(
            f"{kind} manifest fields mismatch; missing={missing}, extra={extra}"
        )
    if document["schema_id"] != schema_id:
        raise ContractValidationError(f"{kind} manifest schema_id is invalid")
    reject_forbidden_content(document)
    sha256_digest(document)
    if kind == "suite":
        _string(document["suite_id"], "suite_id")
        if not re.fullmatch(r"[0-9a-f]{40}", _string(document["upstream_revision"], "upstream_revision")):
            raise ContractValidationError("upstream_revision must be a full commit SHA")
        _string_list(document["problems"], "problems")
        submodules = document["submodules"]
        if not isinstance(submodules, dict) or not submodules:
            raise ContractValidationError("submodules must be a non-empty object")
        if any(
            not isinstance(path, str)
            or not re.fullmatch(r"[0-9a-f]{40}", revision)
            for path, revision in submodules.items()
        ):
            raise ContractValidationError("submodule revisions must be full commit SHAs")
    elif kind == "problem":
        _string(document["suite_id"], "suite_id")
        _string(document["problem_id"], "problem_id")
        _string(document["application_id"], "application_id")
        stages = _string_list(document["task_stages"], "task_stages")
        if any(stage not in {"diagnosis", "mitigation"} for stage in stages):
            raise ContractValidationError("task_stages contains an unsupported stage")
    elif kind == "partition":
        _string(document["partition_id"], "partition_id")
        if document["partition"] not in {"train", "validation", "transfer"}:
            raise ContractValidationError("partition is invalid for an overlay release")
        _string(document["purpose"], "purpose")
        _string_list(document["problems"], "problems")
    elif kind == "fault":
        for field in ("fault_id", "problem_id", "mechanism", "target_component", "seed_behavior"):
            _string(document[field], field)
        steady = document["steady_state"]
        if not isinstance(steady, Mapping) or set(steady) != {
            "signal",
            "baseline_window_seconds",
            "minimum_success_ratio",
        }:
            raise ContractValidationError("steady_state has invalid fields")
        _string(steady["signal"], "steady_state.signal")
        baseline_window = steady["baseline_window_seconds"]
        if (
            isinstance(baseline_window, bool)
            or not isinstance(baseline_window, int)
            or baseline_window <= 0
        ):
            raise ContractValidationError("baseline_window_seconds must be positive")
        if steady["minimum_success_ratio"] != "1.0":
            raise ContractValidationError("WP4 requires a complete steady-state baseline")
        duration = document["max_experiment_duration_seconds"]
        if isinstance(duration, bool) or not isinstance(duration, int) or duration <= baseline_window:
            raise ContractValidationError("max experiment duration must exceed baseline window")
        if document["abort_conditions"] != [
            "kind-node-not-ready",
            "non-target-namespace-impact",
            "telemetry-unavailable",
        ]:
            raise ContractValidationError("fault abort conditions are not the reviewed WP4 set")
        if document["rollback_order"] != [
            "recover-fault",
            "revoke-tool-access",
            "cleanup-application",
            "verify-cluster-ready",
        ]:
            raise ContractValidationError("fault rollback order is invalid")
        if document["cleanup_failure_policy"] != "halt-and-require-operator":
            raise ContractValidationError("cleanup failure must require an operator")
    elif kind == "oracle":
        _string(document["oracle_id"], "oracle_id")
        _string(document["problem_id"], "problem_id")
        _string_list(document["required_stages"], "required_stages")
        if document["verdict_policy"] != "all-required-stages-success":
            raise ContractValidationError("oracle verdict policy is unsupported")
    elif kind == "tool":
        _string(document["tool_profile_id"], "tool_profile_id")
        _string_list(document["interfaces"], "interfaces")
        _string_list(document["capabilities"], "capabilities")
        _string_list(document["denied_namespaces"], "denied_namespaces")
        _string(document["trajectory_durability"], "trajectory_durability")
    elif kind == "observation":
        _string(document["observation_profile_id"], "observation_profile_id")
        _string_list(document["sources"], "sources")
        if document["capture_windows"] != ["baseline", "fault", "mitigation", "recovery"]:
            raise ContractValidationError("observation capture windows are invalid")
        _string(document["causal_signal"], "causal_signal")
    elif kind == "execution":
        _string(document["execution_profile_id"], "execution_profile_id")
        _string(document["backend"], "backend")
        _string(document["isolation"], "isolation")
        timeout = document["timeout_seconds"]
        if isinstance(timeout, bool) or not isinstance(timeout, int) or timeout <= 0:
            raise ContractValidationError("timeout_seconds must be a positive integer")
        if document["deployment_cache_policy"] != "empty-per-formal-run":
            raise ContractValidationError("formal deployment requires an empty cache")
        if document["runtime_image_policy"] != "declared-subset-only":
            raise ContractValidationError("runtime images must be a declared subset")
        if not re.fullmatch(r"[0-9a-f]{64}", _string(document["deployment_lock_digest"], "deployment_lock_digest")):
            raise ContractValidationError("deployment_lock_digest must be a SHA-256 digest")


def load_release_manifests(root: str | Path) -> dict[str, dict[str, Any]]:
    manifest_root = Path(root)
    documents: dict[str, dict[str, Any]] = {}
    for kind, filename in MANIFEST_FILENAMES.items():
        path = manifest_root / filename
        with path.open(encoding="utf-8") as handle:
            document = json.load(handle)
        if not isinstance(document, dict):
            raise ContractValidationError(f"{kind} manifest must be an object")
        _validate_manifest(kind, document)
        documents[kind] = document

    suite = documents["suite"]
    problem = documents["problem"]
    partition = documents["partition"]
    problem_id = problem["problem_id"]
    if suite["upstream_revision"] != UPSTREAM_REVISION:
        raise ContractValidationError("suite manifest does not identify the pinned upstream")
    if problem["suite_id"] != suite["suite_id"] or problem_id not in suite["problems"]:
        raise ContractValidationError("problem manifest is not a member of the selected suite")
    if problem_id not in partition["problems"]:
        raise ContractValidationError("problem manifest is not a member of the selected partition")
    if documents["fault"]["problem_id"] != problem_id:
        raise ContractValidationError("fault profile targets a different problem")
    if documents["oracle"]["problem_id"] != problem_id:
        raise ContractValidationError("oracle profile targets a different problem")
    if documents["oracle"]["required_stages"] != problem["task_stages"]:
        raise ContractValidationError("problem stages and oracle profile disagree")
    return documents


def build_environment_release(
    *, overlay_revision: str, manifests: Mapping[str, Mapping[str, Any]]
) -> EnvironmentRelease:
    for kind in MANIFEST_FILENAMES:
        if kind not in manifests:
            raise ContractValidationError(f"missing {kind} manifest")
        _validate_manifest(kind, manifests[kind])
    return EnvironmentRelease.create(
        environment_provider_id=ENVIRONMENT_PROVIDER_ID,
        upstream_revision=UPSTREAM_REVISION,
        overlay_revision=overlay_revision,
        suite_manifest_digest=sha256_digest(manifests["suite"]),
        problem_manifest_digest=sha256_digest(manifests["problem"]),
        partition_manifest_digest=sha256_digest(manifests["partition"]),
        fault_profile_digest=sha256_digest(manifests["fault"]),
        oracle_profile_digest=sha256_digest(manifests["oracle"]),
        tool_profile_digest=sha256_digest(manifests["tool"]),
        observation_profile_digest=sha256_digest(manifests["observation"]),
        execution_profile_digest=sha256_digest(manifests["execution"]),
    )


def provider_configuration_digests(
    manifests: Mapping[str, Mapping[str, Any]],
) -> dict[str, str]:
    return {
        "sregym.environment.v1": sha256_digest(
            {
                kind: manifests[kind]
                for kind in ("suite", "problem", "partition", "fault")
            }
        ),
        "sregym.oracle.v1": sha256_digest(manifests["oracle"]),
        "sregym.filtered-tools.v1": sha256_digest(manifests["tool"]),
        "sregym.observation.v1": sha256_digest(manifests["observation"]),
        "sregym.container-execution.v1": sha256_digest(manifests["execution"]),
    }


class SREGymReleaseBuilder:
    """Verify a clean provider checkout and build its immutable release identity."""

    def __init__(self, repository_root: str | Path) -> None:
        self.repository_root = Path(repository_root).resolve(strict=True)

    def _git(self, *arguments: str) -> str:
        completed = subprocess.run(
            ["git", *arguments],
            cwd=self.repository_root,
            check=True,
            capture_output=True,
            text=True,
        )
        return completed.stdout.rstrip("\n")

    def verify_repository(self) -> str:
        if self._git("status", "--porcelain"):
            raise ContractValidationError("release export requires a clean provider checkout")
        overlay_revision = self._git("rev-parse", "--verify", "HEAD^{commit}")
        if len(overlay_revision) != 40:
            raise ContractValidationError("overlay revision must be a full commit SHA")
        ancestry = subprocess.run(
            ["git", "merge-base", "--is-ancestor", UPSTREAM_REVISION, overlay_revision],
            cwd=self.repository_root,
            capture_output=True,
        )
        if ancestry.returncode != 0:
            raise ContractValidationError("provider checkout does not descend from pinned upstream")
        submodules = self._git("submodule", "status", "--recursive").splitlines()
        if any(not line or line[0] != " " for line in submodules):
            raise ContractValidationError("recursive submodule checkout is not at its pinned revision")
        with (self.repository_root / "clawgym_overlay" / "upstream-baseline.json").open(
            encoding="utf-8"
        ) as handle:
            baseline = json.load(handle)
        expected_submodules = {
            item["path"]: item["revision"] for item in baseline["submodules"]
        }
        actual_submodules = {
            line[1:].split(maxsplit=2)[1]: line[1:].split(maxsplit=2)[0]
            for line in submodules
        }
        if actual_submodules != expected_submodules:
            raise ContractValidationError("recursive submodule revisions differ from provenance")
        return overlay_revision

    def build(self) -> EnvironmentRelease:
        overlay_revision = self.verify_repository()
        manifests = load_release_manifests(self.repository_root / "clawgym_overlay" / "manifests")
        return build_environment_release(
            overlay_revision=overlay_revision,
            manifests=manifests,
        )
