from __future__ import annotations

import hashlib
import json

import pytest

from clawgym_overlay.r1d_protocol import R1dGate, TARGET, reduce_tool_events, validate_handoff
from clawgym_overlay.reference_runner import _extract_r1d_handoff


def _handoff(run: str = "a" * 64, release: str = "b" * 64) -> dict:
    doc = {
        "schema_id": "clawgym.sregym_diagnosis_handoff.v2",
        "status": "complete",
        "run_manifest_digest": run,
        "agent_release_digest": release,
        "symptom": "recommendation unavailable",
        "target_component": "recommendation",
        "evidence": ["policy blocks frontend"],
        "root_cause_hypothesis": "deny-all policy",
        "candidate_resource": dict(TARGET),
        "minimal_remediation": "remove the blocking policy",
        "verification_plan": ["reread policy", "check endpoint"],
    }
    doc["handoff_digest"] = hashlib.sha256(json.dumps(doc, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return doc


def test_handoff_gate_rejects_natural_language_or_wrong_identity() -> None:
    doc = _handoff()
    assert validate_handoff(doc, run_manifest_digest="a" * 64, agent_release_digest="b" * 64)["status"] == "complete"
    doc["candidate_resource"]["name"] = "other"
    with pytest.raises(ValueError):
        validate_handoff(doc, run_manifest_digest="a" * 64, agent_release_digest="b" * 64)


def test_tool_reducer_joins_result_arriving_in_later_snapshot() -> None:
    call = {"messages": [{"tool_calls": [{"id": "c1", "name": "exec_kubectl_cmd_safely", "args": {"command": "kubectl patch netpol deny-all-recommendation -n hotel-reservation"}}]}]}
    result = {"messages": [{"tool_call_id": "c1", "content": "patched"}]}
    reduced = reduce_tool_events([call, result])
    assert reduced[0]["operation"] == "mutate"
    assert reduced[0]["outcome"] == "executed"


def test_r1d_gate_requires_one_mutation_reread_and_verification() -> None:
    gate = R1dGate().accept_handoff(_handoff(), run_manifest_digest="a" * 64, agent_release_digest="b" * 64)
    gate = gate.verify_preconditions(policy_exists=True).record_mutation().record_reread().record_verification()
    assert gate.may_submit
    with pytest.raises(ValueError):
        gate.record_mutation()


def test_historical_r1c_marker_is_not_accepted_by_r1d_parser() -> None:
    run = type("Run", (), {"manifest_digest": "a" * 64})()
    parsed = _extract_r1d_handoff(
        ({"name": "diagnosis.jsonl", "text": 'R1C_HANDOFF_JSON {"status":"complete"}'},), run
    )
    assert parsed["schema_id"] == "clawgym.sregym_diagnosis_handoff.v2"
    assert parsed["status"] == "incomplete"
