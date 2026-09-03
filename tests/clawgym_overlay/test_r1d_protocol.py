from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace

import pytest

import clawgym_overlay.reference_driver_r1d as r1d
from clawgym_overlay.r1d_protocol import TARGET, R1dGate, conductor_transition, reduce_tool_events, validate_handoff
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
    call = {
        "messages": [
            {
                "tool_calls": [
                    {
                        "id": "c1",
                        "name": "exec_kubectl_cmd_safely",
                        "args": {"command": "kubectl patch netpol deny-all-recommendation -n hotel-reservation"},
                    }
                ]
            }
        ]
    }
    result = {"messages": [{"tool_call_id": "c1", "content": "patched"}]}
    reduced = reduce_tool_events([call, result])
    assert reduced[0]["operation"] == "mutate"
    assert reduced[0]["outcome"] == "executed"
    assert reduce_tool_events([result, call])[0]["outcome"] == "executed"


def test_r1d_gate_requires_one_mutation_reread_and_verification() -> None:
    gate = R1dGate().accept_handoff(_handoff(), run_manifest_digest="a" * 64, agent_release_digest="b" * 64)
    gate = gate.verify_preconditions(policy_exists=True).record_mutation().record_reread().record_verification()
    assert gate.may_submit
    with pytest.raises(ValueError):
        gate.record_mutation()


def test_historical_r1c_marker_is_not_accepted_by_r1d_parser() -> None:
    run = type("Run", (), {"manifest_digest": "a" * 64})()
    parsed = _extract_r1d_handoff(({"name": "diagnosis.jsonl", "text": 'R1C_HANDOFF_JSON {"status":"complete"}'},), run)
    assert parsed["schema_id"] == "clawgym.sregym_diagnosis_handoff.v2"
    assert parsed["status"] == "incomplete"


def test_fake_conductor_fail_closed_stage_transitions() -> None:
    assert conductor_transition("diagnosis", handoff_validated=False) == "awaiting_cleanup"
    assert conductor_transition("diagnosis", handoff_validated=True) == "mitigation"
    assert conductor_transition("timeout", handoff_validated=True) == "error"


def test_r1d_wait_fails_closed_until_projection_validates(monkeypatch: pytest.MonkeyPatch) -> None:
    async def upstream(**kwargs: object) -> str:
        assert kwargs["target_stages"] == {"mitigation", "awaiting_cleanup"}
        return "mitigation"

    monkeypatch.setattr(r1d, "_upstream_wait_for_stage_switch", upstream)
    r1d._r1d_handoff_validated = False

    import asyncio

    assert (
        asyncio.run(r1d._wait_preserving_host_stage(current_stage="diagnosis", target_stages={"mitigation"}))
        == "awaiting_cleanup"
    )
    r1d._r1d_handoff_validated = True
    assert (
        asyncio.run(
            r1d._wait_preserving_host_stage(
                current_stage="diagnosis", target_stages=["mitigation"], timeout=2, poll_interval=0.1
            )
        )
        == "mitigation"
    )


def test_r1d_bounded_submit_emits_incomplete_marker(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    async def submit(*, ans: str) -> None:
        calls.append(ans)

    monkeypatch.setattr(r1d, "manual_submit_tool", submit)

    class Logger:
        def warning(self, message: str) -> None:
            assert "budget exhausted" in message

    class Agent:
        logger = Logger()

    import asyncio

    result = asyncio.run(r1d._bounded_incomplete_submit(Agent(), object()))
    assert result["submitted"] is True
    assert calls == ['R1D_HANDOFF_JSON {"status":"incomplete"}']


def test_r1d_main_installs_host_stage_guards_without_starting_upstream(monkeypatch: pytest.MonkeyPatch) -> None:
    invoked: list[str] = []

    async def fake_main() -> None:
        invoked.append("driver.main")

    monkeypatch.setattr(r1d.driver, "main", fake_main)
    monkeypatch.setattr(r1d.asyncio, "run", lambda awaitable: (awaitable.close(), invoked.append("run"))[1])
    r1d.main()
    assert invoked == ["run"]
    assert r1d.DiagnosisAgent.force_submit is r1d._bounded_incomplete_submit
    assert r1d.driver.wait_for_stage_switch is r1d._wait_preserving_host_stage
    complete = SimpleNamespace(values={"messages": [SimpleNamespace(content='R1D_HANDOFF_JSON {"status":"complete"}')]})
    assert json.loads(r1d.driver.generate_run_summary(complete, None))["status"] == "complete"
    incomplete = SimpleNamespace(values={"messages": [SimpleNamespace(content="plain text")]})
    assert json.loads(r1d.driver.generate_run_summary(incomplete, None))["status"] == "incomplete"
