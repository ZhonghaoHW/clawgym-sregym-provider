"""Offline compiler for WP8.0 declarative environment recipes.

Only closed, enumerated recipe variants are accepted.  The compiler emits
canonical JSON and a receipt; it never contacts Kubernetes or creates a
namespace.  ClawGym later verifies the receipt and constructs the temporary
EnvironmentRelease.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from clawgym.contracts.canonical import sha256_digest

_FAMILIES = {"fault", "workload", "observability"}
_VARIANTS = {
    "fault": {"ingress_egress", "ingress_only"},
    "workload": {"baseline", "low", "high"},
    "observability": {"standard", "high_frequency"},
}
_PROFILES = {
    "fault": {
        "ingress_egress": {"policy_scope": "ingress_egress"},
        "ingress_only": {"policy_scope": "ingress_only"},
    },
    "workload": {
        "baseline": {"rate": 100, "connections": 100, "duration_seconds": 30, "threads": 3},
        "low": {"rate": 25, "connections": 25, "duration_seconds": 30, "threads": 1},
        "high": {"rate": 250, "connections": 250, "duration_seconds": 30, "threads": 4},
    },
    "observability": {
        "standard": {"sample_interval_seconds": 15, "capture_window_seconds": 60},
        "high_frequency": {"sample_interval_seconds": 5, "capture_window_seconds": 120},
    },
}
_COMPONENT_FIELD = {"fault": "fault_profile_digest", "workload": "problem_manifest_digest", "observability": "observation_profile_digest"}
_HEX = set("0123456789abcdef")


class EnvironmentMaterializationError(ValueError):
    pass


def _read(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise EnvironmentMaterializationError("input must be a regular file")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise EnvironmentMaterializationError("input is not valid JSON") from exc
    if not isinstance(value, dict):
        raise EnvironmentMaterializationError("input must be a JSON object")
    return value


def _verify(document: dict[str, Any], field: str) -> str:
    value = document.get(field)
    payload = dict(document)
    payload.pop(field, None)
    if not isinstance(value, str) or len(value) != 64 or any(char not in _HEX for char in value) or sha256_digest(payload) != value:
        raise EnvironmentMaterializationError(f"{field} digest mismatch")
    return value


def _revision(value: Any) -> str:
    if not isinstance(value, str) or len(value) != 40 or any(char not in _HEX for char in value):
        raise EnvironmentMaterializationError("materializer revision must be a commit SHA")
    return value


def _write_json(path: Path, value: dict[str, Any]) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(payload)
    return hashlib.sha256(payload).hexdigest()


def materialize_environment_recipe(
    *,
    proposal_path: str | Path,
    recipe_bundle_path: str | Path,
    request_path: str | Path,
    base_environment_release_path: str | Path,
    output_dir: str | Path,
    runtime_reference: str,
) -> dict[str, Any]:
    proposal = _read(Path(proposal_path))
    recipe = _read(Path(recipe_bundle_path))
    request = _read(Path(request_path))
    base = _read(Path(base_environment_release_path))
    if proposal.get("schema_id") != "agent_evolution.environment_candidate_proposal.v1" or request.get("schema_id") != "agent_evolution.environment_materialization_request.v1" or recipe.get("schema_id") != "agent_evolution.environment_recipe_bundle.v1":
        raise EnvironmentMaterializationError("WP8.0 input schema mismatch")
    _verify(proposal, "proposal_digest")
    _verify(recipe, "recipe_bundle_digest")
    _verify(request, "materialization_request_digest")
    if base.get("schema_id") != "agent_env.environment_release.v1":
        raise EnvironmentMaterializationError("base release schema mismatch")
    _verify(base, "environment_release_digest")
    if proposal.get("recipe_bundle_digest") != recipe["recipe_bundle_digest"] or request.get("proposal_digest") != proposal["proposal_digest"] or proposal.get("epoch_digest") != recipe.get("epoch_digest") or request.get("epoch_digest") != recipe.get("epoch_digest"):
        raise EnvironmentMaterializationError("proposal/request/recipe chain mismatch")
    if proposal.get("base_environment_release_digest") != base["environment_release_digest"] or recipe.get("base_environment_release_digest") != base["environment_release_digest"] or request.get("base_environment_release_digest") != base["environment_release_digest"]:
        raise EnvironmentMaterializationError("base environment identity mismatch")
    family, variant = recipe.get("recipe_family"), recipe.get("variant")
    if family not in _FAMILIES or variant not in _VARIANTS[family] or proposal.get("recipe_family") != family:
        raise EnvironmentMaterializationError("recipe variant is not allowlisted")
    if proposal.get("change_class") != "recipe_evolution" or request.get("execution_authority") != "none" or request.get("execution_scope") != "reference_environment_only":
        raise EnvironmentMaterializationError("materialization request is not artifact-only")
    runtime_reference = _revision(runtime_reference)
    if request.get("expected_provider_id") != "sregym.environment.v1" or request.get("expected_materializer_revision") != runtime_reference:
        raise EnvironmentMaterializationError("materialization provider identity mismatch")
    target = Path(output_dir)
    if target.exists() or target.is_symlink():
        raise EnvironmentMaterializationError("output directory already exists")
    target.mkdir(parents=True, exist_ok=False)

    base_field = _COMPONENT_FIELD[family]
    base_component = base[base_field]
    if not isinstance(base_component, str) or len(base_component) != 64 or any(char not in _HEX for char in base_component):
        raise EnvironmentMaterializationError("base component digest is invalid")
    if recipe.get("base_component_digest") != base_component:
        raise EnvironmentMaterializationError("recipe base component mismatch")
    candidate_component = sha256_digest({"schema_id": "clawgym.sregym_environment_component.v1", "family": family, "base_component_digest": base_component, "variant": variant, "runtime_reference": runtime_reference})
    component_document = {
        "schema_id": "clawgym.sregym_environment_component.v1",
        "family": family,
        "recipe_id": recipe["recipe_id"],
        "variant": variant,
        "base_component_digest": base_component,
        "candidate_component_digest": candidate_component,
        "target_namespace": "hotel-reservation",
        "target_resource": "deny-all-recommendation",
        "profile": _PROFILES[family][variant],
    }
    component_path = target / "environment-materialized" / f"{family}-component.json"
    component_file_digest = _write_json(component_path, component_document)
    inherited = {
        key: base[key]
        for key in ("suite_manifest_digest", "problem_manifest_digest", "partition_manifest_digest", "fault_profile_digest", "oracle_profile_digest", "tool_profile_digest", "observation_profile_digest", "execution_profile_digest")
    }
    candidate_components = dict(inherited)
    candidate_components[base_field] = candidate_component
    semantic_diff = [{"component": family, "field": base_field, "from_digest": base_component, "to_digest": candidate_component, "variant": variant}]
    receipt = {
        "schema_id": "clawgym.environment_materialization_receipt.v1",
        "proposal_digest": proposal["proposal_digest"],
        "recipe_bundle_digest": recipe["recipe_bundle_digest"],
        "materialization_request_digest": request["materialization_request_digest"],
        "base_environment_release_digest": base["environment_release_digest"],
        "materializer_id": "sregym.environment-recipe-materializer.v1",
        "materializer_revision": runtime_reference,
        "provider_id": "sregym.environment.v1",
        "control_component_digests": inherited,
        "candidate_component_digests": candidate_components,
        "semantic_diff": semantic_diff,
        "target_namespace": "hotel-reservation",
        "control_namespace_policy": "host-derived-exclusive-lease",
        "ttl_seconds": recipe["requested_ttl_seconds"],
        "file_summary": [{"path": "environment-materialized/" + component_path.name, "sha256_digest": component_file_digest, "bytes": component_path.stat().st_size}],
        "invariants": {"upstream_revision": base["upstream_revision"], "oracle_profile_digest": base["oracle_profile_digest"], "tool_profile_digest": base["tool_profile_digest"], "execution_profile_digest": base["execution_profile_digest"]},
    }
    receipt["receipt_digest"] = sha256_digest(receipt)
    _write_json(target / "materialization-receipt.json", receipt)
    component_bundle = {"schema_id": "clawgym.sregym_environment_component_bundle.v1", "receipt_digest": receipt["receipt_digest"], "component_digest": candidate_component, "component": component_document}
    component_bundle["component_bundle_digest"] = sha256_digest(component_bundle)
    _write_json(target / "component-bundle.json", component_bundle)
    return receipt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="provider materialize-environment-recipe")
    parser.add_argument("--proposal", required=True, type=Path)
    parser.add_argument("--recipe-bundle", required=True, type=Path)
    parser.add_argument("--request", required=True, type=Path)
    parser.add_argument("--base-environment-release", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--runtime-reference", required=True)
    args = parser.parse_args(argv)
    receipt = materialize_environment_recipe(proposal_path=args.proposal, recipe_bundle_path=args.recipe_bundle, request_path=args.request, base_environment_release_path=args.base_environment_release, output_dir=args.output, runtime_reference=args.runtime_reference)
    print(json.dumps(receipt, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
