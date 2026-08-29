import hashlib
import json

from clawgym_overlay.r1e_protocol import R1eGate, TARGET, parse_command, validate_handoff


def _handoff(run="a" * 64, release="b" * 64):
    value = {"schema_id": "clawgym.sregym_diagnosis_handoff.v2", "status": "complete", "run_manifest_digest": run, "agent_release_digest": release, "symptom": "blocked", "target_component": "recommendation", "evidence": ["policy"], "root_cause_hypothesis": "deny policy", "candidate_resource": TARGET, "minimal_remediation": "delete policy", "verification_plan": ["reread"]}
    value["handoff_digest"] = hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return value


def test_parse_rejects_compound_command():
    assert parse_command("kubectl delete networkpolicy deny-all-recommendation -n hotel-reservation; kubectl get pods")[0] == "unknown"


def test_gate_requires_exact_transaction():
    gate = R1eGate(handoff_validated=True)
    assert gate.record("kubectl get networkpolicy deny-all-recommendation -n hotel-reservation", "policy exists", stage="mitigation")
    assert gate.record("kubectl delete networkpolicy deny-all-recommendation -n hotel-reservation", "deleted", stage="mitigation")
    assert not gate.record("kubectl delete service recommendation -n hotel-reservation", "deleted", stage="mitigation")
    assert gate.record("kubectl get networkpolicy deny-all-recommendation -n hotel-reservation", "Error from server (NotFound)", stage="mitigation")
    assert gate.record("kubectl get endpoints recommendation -n hotel-reservation", "addresses: 10.0.0.1", stage="mitigation")
    assert gate.may_submit


def test_handoff_identity_and_target_are_bound():
    value = _handoff()
    assert validate_handoff(value, run_manifest_digest="a" * 64, agent_release_digest="b" * 64)
    value["candidate_resource"] = {"kind": "Service", "namespace": "hotel-reservation", "name": "recommendation"}
    assert not validate_handoff(value, run_manifest_digest="a" * 64, agent_release_digest="b" * 64)
