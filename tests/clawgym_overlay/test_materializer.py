import json
from pathlib import Path

import pytest

from clawgym.contracts import sha256_digest
from clawgym_overlay.materializer import MaterializationError, materialize_reference_profile
from clawgym_overlay.reference_profiles import load_materialized_reference_profile

D = "a" * 64
E = "b" * 64
F = "c" * 64
G = "d" * 64


def _write(path: Path, doc: dict):
    path.write_text(json.dumps(doc, ensure_ascii=False, sort_keys=True, separators=(",", ":")), encoding="utf-8")


def _inputs(tmp_path):
    parent = {"schema_id": "clawgym.sregym_reference_agent_profile.v1", "adapter_id": "sregym.reference-agent.v1", "lane": "agent_validation", "agent": "stratus", "artifact_id": "network_policy_block", "model_id": "openai/deepseek-v4-pro", "api_base": "https://gateway.example/v1", "command": ["python", "-m", "reference_driver_r1f"], "runtime_injection": "host-only-file", "runtime_variable": "AGENT_API_KEY", "agent_release_digest": F, "tool_policy_profile_bundle_digest": D}
    bundle = {"schema_id":"agent_evolution.reference_agent_component_bundle.v1","bundle_id":"b","experimental_baseline_digest":D,"base_agent_release_digest":F,"search_plan_digest":G,"train_partition_digest":D,"components":{"diagnosis":{"system":"Diagnose {app_name}","user":"Inspect {app_namespace}","summary":"Summarize"},"mitigation":{"system":"Fix {app_name}","user":"Apply minimal fix {faults_info}","retry_user":"Retry {last_result} {reflection}"}},"diagnosis_step_limit":8,"mitigation_step_limit":8}
    bundle["component_bundle_digest"] = sha256_digest({k:v for k,v in bundle.items() if k != "component_bundle_digest"})
    proposal = {"schema_id":"agent_evolution.agent_candidate_proposal.v1","candidate_id":"c","experimental_baseline_digest":D,"frozen_control_agent_release_digest":E,"base_agent_release_digest":F,"environment_release_digest":G,"search_plan_digest":D,"component_bundle_digest":bundle["component_bundle_digest"],"source_train_episode_digests":[D],"parent_lineage":[E,F],"change_class":"materializer_compatibility"}
    proposal["proposal_digest"] = sha256_digest({k:v for k,v in proposal.items() if k != "proposal_digest"})
    p = tmp_path / "proposal.json"; b = tmp_path / "bundle.json"; parent_path = tmp_path / "parent.json"
    _write(p, proposal); _write(b, bundle); _write(parent_path, parent)
    return p, b, parent_path


def test_materialization_is_deterministic_and_explicit(tmp_path):
    p, b, parent = _inputs(tmp_path)
    r1 = materialize_reference_profile(proposal_path=p, component_bundle_path=b, parent_profile_path=parent, output_dir=tmp_path/"one", runtime_reference="a"*40)
    r2 = materialize_reference_profile(proposal_path=p, component_bundle_path=b, parent_profile_path=parent, output_dir=tmp_path/"two", runtime_reference="a"*40)
    assert r1 == r2
    assert (tmp_path/"one"/"profile.json").read_bytes() == (tmp_path/"two"/"profile.json").read_bytes()
    assert load_materialized_reference_profile(tmp_path/"one", profile_digest=r1["profile_digest"])["schema_id"].endswith("v2")


def test_materializer_rejects_tamper_and_reuse(tmp_path):
    p, b, parent = _inputs(tmp_path)
    doc = json.loads(b.read_text()); doc["components"]["diagnosis"]["system"] = "bad {unknown}"; _write(b, doc)
    with pytest.raises(MaterializationError):
        materialize_reference_profile(proposal_path=p, component_bundle_path=b, parent_profile_path=parent, output_dir=tmp_path/"out", runtime_reference="a"*40)
