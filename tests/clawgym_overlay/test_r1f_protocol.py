from __future__ import annotations

import asyncio
import json

from langgraph.types import Command
from langchain_core.messages import AIMessage

from clawgym_overlay.r1f_protocol import (
    R1fGate,
    TARGET,
    endpoint_result_ready,
    handoff_from_trajectory_records,
    normalise_handoff_submission,
)


RUN = "a" * 64
RELEASE = "b" * 64


def _attempt10_style_submission() -> str:
    return """R1F_HANDOFF_JSON
{
  "symptom": "recommendation is unreachable",
  "target_component": "NetworkPolicy/hotel-reservation/deny-all-recommendation",
  "evidence": ["policy denies ingress and egress"],
  "root_cause_hypothesis": "deny policy isolates recommendation",
  "candidate_resource": "NetworkPolicy/hotel-reservation/deny-all-recommendation",
  "minimal_remediation": "delete the deny policy",
  "verification_plan": ["reread the policy", "read recommendation endpoints"]
}"""


def test_attempt10_style_multiline_submission_normalises_to_host_bound_handoff() -> None:
    handoff = normalise_handoff_submission(
        _attempt10_style_submission(), run_manifest_digest=RUN, agent_release_digest=RELEASE
    )
    assert handoff is not None
    assert handoff["status"] == "complete"
    assert handoff["candidate_resource"] == TARGET
    assert handoff["run_manifest_digest"] == RUN
    assert handoff["agent_release_digest"] == RELEASE


def test_real_r1f_submission_with_narrative_evidence_normalises() -> None:
    submission = _attempt10_style_submission().replace(
        '["policy denies ingress and egress"]', '"policy denies ingress and egress"'
    ).replace(
        '["reread the policy", "read recommendation endpoints"]',
        '"reread the policy and read recommendation endpoints"',
    )
    handoff = normalise_handoff_submission(
        submission, run_manifest_digest=RUN, agent_release_digest=RELEASE
    )
    assert handoff is not None
    assert handoff["evidence"] == ["policy denies ingress and egress"]
    assert handoff["verification_plan"] == [
        "reread the policy and read recommendation endpoints"
    ]


def test_handoff_rejects_non_target_or_missing_semantic_evidence() -> None:
    wrong_target = _attempt10_style_submission().replace(
        "NetworkPolicy/hotel-reservation/deny-all-recommendation",
        "Service/hotel-reservation/recommendation",
    )
    assert normalise_handoff_submission(
        wrong_target, run_manifest_digest=RUN, agent_release_digest=RELEASE
    ) is None
    missing_evidence = _attempt10_style_submission().replace(
        '["policy denies ingress and egress"]', "[]"
    )
    assert normalise_handoff_submission(
        missing_evidence, run_manifest_digest=RUN, agent_release_digest=RELEASE
    ) is None


def test_trajectory_replay_uses_structured_tool_arguments_not_transcript_lines() -> None:
    records = (
        {
            "name": "trajectory.jsonl",
            "text": json.dumps(
                {
                    "messages": [
                        {
                            "tool_calls": [
                                {
                                    "name": "submit_tool",
                                    "args": {"ans": _attempt10_style_submission()},
                                }
                            ]
                        }
                    ]
                }
            ),
        },
    )
    handoff = handoff_from_trajectory_records(
        records, run_manifest_digest=RUN, agent_release_digest=RELEASE
    )
    assert handoff["status"] == "complete"
    assert handoff["candidate_resource"] == TARGET


def test_gate_requires_exact_single_delete_and_verification() -> None:
    gate = R1fGate(handoff_validated=True)
    assert gate.record(
        "kubectl get networkpolicy deny-all-recommendation -n hotel-reservation",
        "NAME AGE\ndeny-all-recommendation 1m",
        stage="mitigation",
    )
    assert not gate.permits_mutation(
        "kubectl delete service recommendation -n hotel-reservation", stage="mitigation"
    )
    assert not gate.record(
        "kubectl delete networkpolicy deny-all-recommendation -n hotel-reservation --ignore-not-found",
        "deleted",
        stage="mitigation",
    )
    assert gate.record(
        "kubectl delete networkpolicy deny-all-recommendation -n hotel-reservation",
        "networkpolicy.networking.k8s.io/deny-all-recommendation deleted",
        stage="mitigation",
    )
    assert gate.record(
        "kubectl get networkpolicy deny-all-recommendation -n hotel-reservation",
        "Error from server (NotFound): networkpolicies.networking.k8s.io \"deny-all-recommendation\" not found",
        stage="mitigation",
    )
    assert gate.record(
        "kubectl get endpoints recommendation -n hotel-reservation",
        "NAME ENDPOINTS AGE\nrecommendation 10.244.0.10:8085 1m",
        stage="mitigation",
    )
    assert gate.may_submit


def test_endpoint_ready_parser_accepts_sanitized_table_and_rejects_empty() -> None:
    assert endpoint_result_ready("NAME ENDPOINTS\nrecommendation [REDACTED]:8085 1m")
    assert endpoint_result_ready("NAME ENDPOINTS PORTS\nrecommendation [REDACTED] 8085/TCP")
    assert endpoint_result_ready("addresses: 10.244.0.10\nports:\n- port: 8085\n  protocol: TCP")
    assert not endpoint_result_ready("NAME ENDPOINTS\nrecommendation <none> 1m")
    assert not endpoint_result_ready("ports: 8085/TCP")


def test_strict_gate_requires_post_mutation_order() -> None:
    gate = R1fGate(handoff_validated=True, strict_postconditions=True)
    assert gate.record(
        "kubectl get networkpolicy deny-all-recommendation -n hotel-reservation",
        "deny-all-recommendation",
        stage="mitigation",
    )
    assert gate.record(
        "kubectl get endpoints recommendation -n hotel-reservation",
        "recommendation [REDACTED]:8085",
        stage="mitigation",
    )
    assert not gate.may_submit
    assert gate.record(
        "kubectl delete networkpolicy deny-all-recommendation -n hotel-reservation",
        "deleted",
        stage="mitigation",
    )
    assert gate.record(
        "kubectl get networkpolicy deny-all-recommendation -n hotel-reservation",
        'NotFound: networkpolicy "deny-all-recommendation" not found',
        stage="mitigation",
    )
    assert gate.record(
        "kubectl get endpoints recommendation -n hotel-reservation",
        "recommendation [REDACTED]:8085",
        stage="mitigation",
    )
    assert gate.may_submit


def test_gate_snapshot_is_identity_bound() -> None:
    gate = R1fGate(handoff_validated=True, strict_postconditions=True)
    snapshot = gate.snapshot(run_manifest_digest=RUN, agent_release_digest=RELEASE)
    assert snapshot["schema_id"] == "clawgym.sregym_gate_event_journal.v1"
    assert snapshot["state"]["may_submit"] is False
    assert len(snapshot["journal_digest"]) == 64


def test_runtime_submit_hook_normalises_without_triggering_conductor(monkeypatch) -> None:
    import clawgym_overlay.reference_driver_r1f as driver

    observed: dict[str, object] = {}

    async def fake_submit(*, ans, state, tool_call_id):
        observed.update({"ans": ans, "state": state, "tool_call_id": tool_call_id})
        return "submitted"

    monkeypatch.setenv("SREGYM_RUN_MANIFEST_DIGEST", RUN)
    monkeypatch.setenv("SREGYM_AGENT_RELEASE_DIGEST", RELEASE)
    monkeypatch.setattr(driver, "_original_diagnosis_submit", fake_submit)
    monkeypatch.setattr(driver, "_handoff", None)
    monkeypatch.setattr(driver, "_handoff_rejections", 0)
    monkeypatch.setattr(driver, "_gate", R1fGate())

    result = asyncio.run(
        driver._gated_diagnosis_submit(_attempt10_style_submission(), {"num_steps": 4}, "call-1")
    )

    assert isinstance(result, Command)
    assert not observed
    assert driver._handoff is not None
    assert driver._handoff["status"] == "complete"
    assert driver._gate.handoff_validated


def test_r1i_text_handoff_is_promoted_to_explicit_submit_tool_call(monkeypatch) -> None:
    import clawgym_overlay.reference_driver_r1f as driver

    class Submit:
        name = "submit_tool"

    class Agent:
        submit_tool = Submit()

    answer = _attempt10_style_submission()
    original = lambda self, state: {
        "messages": [AIMessage(content=[{"type": "text", "text": answer}])]
    }
    monkeypatch.setenv("SREGYM_RUN_MANIFEST_DIGEST", RUN)
    monkeypatch.setenv("SREGYM_AGENT_RELEASE_DIGEST", RELEASE)
    monkeypatch.setattr(driver, "_original_diagnosis_call_model", original)

    result = driver._r1i_call_model(Agent(), {"messages": []})
    call = result["messages"][0].tool_calls[0]
    assert call["name"] == "submit_tool"
    assert call["args"]["ans"] == answer


def test_r1i_plain_json_submit_argument_is_accepted(monkeypatch) -> None:
    import clawgym_overlay.reference_driver_r1f as driver

    monkeypatch.setenv("SREGYM_SOP_VARIANT", "r1i-typed-handoff-journal-v1")
    monkeypatch.setenv("SREGYM_RUN_MANIFEST_DIGEST", RUN)
    monkeypatch.setenv("SREGYM_AGENT_RELEASE_DIGEST", RELEASE)
    monkeypatch.setattr(driver, "_handoff", None)
    monkeypatch.setattr(driver, "_handoff_rejections", 0)
    monkeypatch.setattr(driver, "_gate", R1fGate())

    result = asyncio.run(
        driver._gated_diagnosis_submit(
            _attempt10_style_submission().removeprefix("R1F_HANDOFF_JSON\n"),
            {"num_steps": 4},
            "call-plain",
        )
    )
    assert isinstance(result, Command)
    assert driver._handoff is not None
    assert driver._handoff["status"] == "complete"
