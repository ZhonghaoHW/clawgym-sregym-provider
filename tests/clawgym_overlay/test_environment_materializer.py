from __future__ import annotations

import json

from clawgym.contracts.canonical import sha256_digest
from clawgym.contracts.models import EnvironmentRelease
from clawgym_overlay.environment_materializer import materialize_environment_recipe


def _write(path, value):
    path.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")), encoding="utf-8")


def _doc_without_digest(payload, field):
    value = dict(payload)
    value[field] = sha256_digest(payload)
    return value


def test_all_recipe_families_compile_and_output_is_deterministic(tmp_path):
    base = EnvironmentRelease.create(
        environment_provider_id="sregym.environment.v1", upstream_revision="a" * 40, overlay_revision="b" * 40,
        suite_manifest_digest="1" * 64, problem_manifest_digest="2" * 64, partition_manifest_digest="3" * 64,
        fault_profile_digest="4" * 64, oracle_profile_digest="5" * 64, tool_profile_digest="6" * 64,
        observation_profile_digest="7" * 64, execution_profile_digest="8" * 64,
    ).to_dict()
    for family, variant, field in (("fault", "ingress_only", "fault_profile_digest"), ("workload", "low", "problem_manifest_digest"), ("observability", "high_frequency", "observation_profile_digest")):
        recipe = _doc_without_digest({"schema_id": "agent_evolution.environment_recipe_bundle.v1", "bundle_id": family, "epoch_digest": "9" * 64, "base_environment_release_digest": base["environment_release_digest"], "recipe_family": family, "recipe_id": family + ".v1", "base_component_digest": base[field], "variant": variant, "requested_ttl_seconds": 3600}, "recipe_bundle_digest")
        proposal = _doc_without_digest({"schema_id": "agent_evolution.environment_candidate_proposal.v1", "proposal_id": family, "epoch_digest": "9" * 64, "base_environment_release_digest": base["environment_release_digest"], "recipe_bundle_digest": recipe["recipe_bundle_digest"], "recipe_family": family, "change_class": "recipe_evolution", "panel_class": "reference_family_experimental", "promotion_scope": "reference_environment_only"}, "proposal_digest")
        request = _doc_without_digest({"schema_id": "agent_evolution.environment_materialization_request.v1", "request_id": family, "epoch_digest": "9" * 64, "proposal_digest": proposal["proposal_digest"], "base_environment_release_digest": base["environment_release_digest"], "expected_provider_id": "sregym.environment.v1", "expected_materializer_revision": "c" * 40, "execution_scope": "reference_environment_only", "execution_authority": "none"}, "materialization_request_digest")
        paths = []
        for index in (1, 2):
            out = tmp_path / f"{family}-{index}"
            paths.append(out)
            _write(tmp_path / f"base-{family}-{index}.json", base)
            _write(tmp_path / f"recipe-{family}-{index}.json", recipe)
            _write(tmp_path / f"proposal-{family}-{index}.json", proposal)
            _write(tmp_path / f"request-{family}-{index}.json", request)
            materialize_environment_recipe(proposal_path=tmp_path / f"proposal-{family}-{index}.json", recipe_bundle_path=tmp_path / f"recipe-{family}-{index}.json", request_path=tmp_path / f"request-{family}-{index}.json", base_environment_release_path=tmp_path / f"base-{family}-{index}.json", output_dir=out, runtime_reference="c" * 40)
        assert sorted(p.read_bytes() for p in paths[0].rglob("*.json")) == sorted(p.read_bytes() for p in paths[1].rglob("*.json"))


def test_materializer_rejects_unknown_variant(tmp_path):
    base = EnvironmentRelease.create(
        environment_provider_id="sregym.environment.v1", upstream_revision="a" * 40, overlay_revision="b" * 40,
        suite_manifest_digest="1" * 64, problem_manifest_digest="2" * 64, partition_manifest_digest="3" * 64,
        fault_profile_digest="4" * 64, oracle_profile_digest="5" * 64, tool_profile_digest="6" * 64,
        observation_profile_digest="7" * 64, execution_profile_digest="8" * 64,
    ).to_dict()
    recipe = _doc_without_digest({"schema_id": "agent_evolution.environment_recipe_bundle.v1", "bundle_id": "bad", "epoch_digest": "9" * 64, "base_environment_release_digest": base["environment_release_digest"], "recipe_family": "fault", "recipe_id": "fault.v1", "base_component_digest": base["fault_profile_digest"], "variant": "kubectl-command", "requested_ttl_seconds": 3600}, "recipe_bundle_digest")
    proposal = _doc_without_digest({"schema_id": "agent_evolution.environment_candidate_proposal.v1", "proposal_id": "bad", "epoch_digest": "9" * 64, "base_environment_release_digest": base["environment_release_digest"], "recipe_bundle_digest": recipe["recipe_bundle_digest"], "recipe_family": "fault", "change_class": "recipe_evolution", "panel_class": "reference_family_experimental", "promotion_scope": "reference_environment_only"}, "proposal_digest")
    request = _doc_without_digest({"schema_id": "agent_evolution.environment_materialization_request.v1", "request_id": "bad", "epoch_digest": "9" * 64, "proposal_digest": proposal["proposal_digest"], "base_environment_release_digest": base["environment_release_digest"], "expected_provider_id": "sregym.environment.v1", "expected_materializer_revision": "c" * 40, "execution_scope": "reference_environment_only", "execution_authority": "none"}, "materialization_request_digest")
    for name, value in (("base.json", base), ("recipe.json", recipe), ("proposal.json", proposal), ("request.json", request)):
        _write(tmp_path / name, value)
    import pytest
    with pytest.raises(ValueError):
        materialize_environment_recipe(proposal_path=tmp_path / "proposal.json", recipe_bundle_path=tmp_path / "recipe.json", request_path=tmp_path / "request.json", base_environment_release_path=tmp_path / "base.json", output_dir=tmp_path / "out", runtime_reference="c" * 40)
