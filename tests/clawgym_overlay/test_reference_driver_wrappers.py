from __future__ import annotations

import asyncio
import hashlib
import json

import pytest
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.types import Command


def _r1c_payload() -> dict[str, object]:
    return {
        "symptom": "recommendation unavailable",
        "target_component": "recommendation",
        "evidence": ["endpoint unhealthy"],
        "root_cause_hypothesis": "network policy",
        "candidate_resource": {
            "kind": "NetworkPolicy",
            "namespace": "hotel-reservation",
            "name": "deny-all-recommendation",
        },
        "minimal_remediation": "delete the policy",
        "verification_plan": ["reread policy", "check endpoint"],
    }


def _r1c_submission() -> str:
    return "R1C_HANDOFF_JSON " + json.dumps(_r1c_payload(), separators=(",", ":"))


def test_r1c_bounded_force_submit_requires_a_validated_model_handoff(monkeypatch, tmp_path) -> None:
    import clawgym_overlay.reference_driver_r1c as driver

    calls: list[str] = []

    async def submit(*, ans: str, state: object, tool_call_id: str) -> Command:
        calls.append(ans)
        return Command(
            update={
                "submitted": True,
                "messages": [ToolMessage(content="accepted", tool_call_id=tool_call_id)],
            }
        )

    monkeypatch.setattr(driver, "_original_diagnosis_submit", submit)
    monkeypatch.setattr(driver, "_handoff", None)
    monkeypatch.setenv("SREGYM_RUN_MANIFEST_DIGEST", "a" * 64)
    monkeypatch.setenv("SREGYM_AGENT_RELEASE_DIGEST", "b" * 64)
    monkeypatch.setenv("AGENT_LOGS_DIR", str(tmp_path))

    class Llm:
        def inference(self, *, messages, tools):
            return AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "submit_tool",
                        "args": {"ans": _r1c_submission()},
                        "id": "force-1",
                        "type": "tool_call",
                    }
                ],
            )

    class Submit:
        name = "submit_tool"

    agent = type("Agent", (), {"llm": Llm(), "submit_tool": Submit()})()
    result = asyncio.run(driver._bounded_force_submit(agent, {"messages": [], "num_steps": 4}))
    assert result["submitted"] is True
    assert calls == [_r1c_submission()]
    document = json.loads((tmp_path / "r1c-handoff.json").read_text())
    assert document["status"] == "complete"
    assert json.loads(driver._validated_summary(None, None))["status"] == "complete"


def test_r1c_bounded_force_submit_fails_closed_without_a_tool_call(monkeypatch) -> None:
    import clawgym_overlay.reference_driver_r1c as driver

    calls: list[str] = []

    async def submit(*, ans: str, state: object, tool_call_id: str) -> Command:
        calls.append(ans)
        return Command(update={"submitted": True})

    class Llm:
        def inference(self, *, messages, tools):
            return AIMessage(content="plain answer")

    class Submit:
        name = "submit_tool"

    monkeypatch.setattr(driver, "_original_diagnosis_submit", submit)
    monkeypatch.setattr(driver, "_handoff", None)
    agent = type("Agent", (), {"llm": Llm(), "submit_tool": Submit()})()
    with pytest.raises(driver.R1cHandoffError, match="structured handoff"):
        asyncio.run(driver._bounded_force_submit(agent, {"messages": [], "num_steps": 4}))
    assert calls == []


def test_r1c_valid_structured_text_uses_the_gated_submit_tool(monkeypatch) -> None:
    import clawgym_overlay.reference_driver_r1c as driver

    monkeypatch.setenv("SREGYM_RUN_MANIFEST_DIGEST", "a" * 64)
    monkeypatch.setenv("SREGYM_AGENT_RELEASE_DIGEST", "b" * 64)

    def original_call_model(_agent, _state):
        return {"messages": [AIMessage(content=_r1c_submission())]}

    class Submit:
        name = "submit_tool"

    monkeypatch.setattr(driver, "_original_diagnosis_call_model", original_call_model)
    result = driver._r1c_call_model(type("Agent", (), {"submit_tool": Submit()})(), {"messages": []})
    message = result["messages"][-1]
    assert isinstance(message, AIMessage)
    assert message.tool_calls[0]["name"] == "submit_tool"
    assert message.tool_calls[0]["args"]["ans"] == _r1c_submission()


def test_r1c_plain_final_answer_enters_explicit_finalization(monkeypatch) -> None:
    import clawgym_overlay.reference_driver_r1c as driver

    monkeypatch.setattr(driver, "_original_diagnosis_should_continue", lambda _agent, _state: "__end__")
    result = driver._r1c_should_continue(
        object(),
        {"messages": [AIMessage(content="The policy blocks recommendation traffic.")], "submitted": False},
    )
    assert result == "force_submit"


def test_r1c_non_answer_terminal_paths_are_not_rewritten(monkeypatch) -> None:
    import clawgym_overlay.reference_driver_r1c as driver

    monkeypatch.setattr(driver, "_original_diagnosis_should_continue", lambda _agent, _state: "__end__")
    tool_call = AIMessage(
        content="",
        tool_calls=[{"name": "get_services", "args": {}, "id": "read-1", "type": "tool_call"}],
    )
    assert driver._r1c_should_continue(object(), {"messages": [tool_call], "submitted": False}) == "__end__"
    assert (
        driver._r1c_should_continue(object(), {"messages": [AIMessage(content="done")], "submitted": True}) == "__end__"
    )


def test_r1c_gated_submit_rejects_invalid_payload_before_upstream(monkeypatch) -> None:
    import clawgym_overlay.reference_driver_r1c as driver

    calls: list[str] = []

    async def submit(*, ans: str, state: object, tool_call_id: str) -> Command:
        calls.append(ans)
        return Command(update={"submitted": True})

    monkeypatch.setattr(driver, "_original_diagnosis_submit", submit)
    monkeypatch.setattr(driver, "_handoff", None)
    monkeypatch.setenv("SREGYM_RUN_MANIFEST_DIGEST", "a" * 64)
    monkeypatch.setenv("SREGYM_AGENT_RELEASE_DIGEST", "b" * 64)
    result = asyncio.run(
        driver._gated_diagnosis_submit('R1C_HANDOFF_JSON {"status":"incomplete"}', {"num_steps": 4}, "call-1")
    )
    assert isinstance(result, Command)
    assert result.update.get("submitted") is not True
    assert calls == []
    assert driver._handoff is None


def test_r1c_host_stage_wait_is_bounded(monkeypatch) -> None:
    import clawgym_overlay.reference_driver_r1c as driver

    monkeypatch.setattr(driver, "_handoff", {"status": "complete"})
    observed: dict[str, object] = {}

    async def upstream(**kwargs):
        observed.update(kwargs)
        return "awaiting_cleanup"

    monkeypatch.setattr(driver, "_upstream_wait_for_stage_switch", upstream)
    result = asyncio.run(
        driver._wait_for_host_controlled_terminal(
            current_stage="mitigation", target_stages=["done"], timeout=3, poll_interval=0.5
        )
    )
    assert result == "done"
    assert observed["target_stages"] == {"done", "awaiting_cleanup"}


def test_r1d_incomplete_submit_fails_closed(monkeypatch) -> None:
    import clawgym_overlay.reference_driver_r1d as driver

    calls: list[str] = []

    async def submit(*, ans: str) -> None:
        calls.append(ans)

    monkeypatch.setattr(driver, "manual_submit_tool", submit)

    class Logger:
        def warning(self, _message: str) -> None:
            return None

    result = asyncio.run(driver._bounded_incomplete_submit(type("Agent", (), {"logger": Logger()})(), {}))
    assert result["submitted"] is True
    assert calls == ['R1D_HANDOFF_JSON {"status":"incomplete"}']


def test_r1d_wait_preserves_host_terminal_before_handoff(monkeypatch) -> None:
    import clawgym_overlay.reference_driver_r1d as driver

    monkeypatch.setattr(driver, "_r1d_handoff_validated", False)
    assert asyncio.run(driver._wait_preserving_host_stage(current_stage="diagnosis", target_stages=[])) == (
        "awaiting_cleanup"
    )

    async def upstream(**_kwargs):
        return "mitigation"

    monkeypatch.setattr(driver, "_upstream_wait_for_stage_switch", upstream)
    monkeypatch.setattr(driver, "_r1d_handoff_validated", True)
    assert asyncio.run(driver._wait_preserving_host_stage(current_stage="diagnosis", target_stages=[])) == "mitigation"


def test_r1e_projection_accepts_identity_bound_marker(monkeypatch) -> None:
    import clawgym_overlay.reference_driver_r1e as driver

    run = "a" * 64
    release = "b" * 64
    payload = {
        "status": "complete",
        "symptom": "recommendation unavailable",
        "target_component": "NetworkPolicy/hotel-reservation/deny-all-recommendation",
        "evidence": ["policy blocks recommendation"],
        "root_cause_hypothesis": "deny policy",
        "candidate_resource": {
            "kind": "NetworkPolicy",
            "namespace": "hotel-reservation",
            "name": "deny-all-recommendation",
        },
        "minimal_remediation": "delete policy",
        "verification_plan": ["reread policy"],
    }
    monkeypatch.setenv("SREGYM_RUN_MANIFEST_DIGEST", run)
    monkeypatch.setenv("SREGYM_AGENT_RELEASE_DIGEST", release)
    document = {
        "schema_id": "clawgym.sregym_diagnosis_handoff.v2",
        **payload,
        "run_manifest_digest": run,
        "agent_release_digest": release,
    }
    document["handoff_digest"] = hashlib.sha256(
        json.dumps(document, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    state = type("State", (), {"values": {"messages": [{"content": "R1E_HANDOFF_JSON " + json.dumps(payload)}]}})()
    result = json.loads(driver._handoff_projection(state, None))
    assert result["status"] == "complete"
    assert driver._handoff_validated is True


def test_r1e_projection_rejects_missing_marker(monkeypatch) -> None:
    import clawgym_overlay.reference_driver_r1e as driver

    monkeypatch.setenv("SREGYM_RUN_MANIFEST_DIGEST", "a" * 64)
    monkeypatch.setenv("SREGYM_AGENT_RELEASE_DIGEST", "b" * 64)
    state = type("State", (), {"values": {"messages": [{"content": "plain text"}]}})()
    result = json.loads(driver._handoff_projection(state, None))
    assert result["status"] == "incomplete"
    assert driver._handoff_validated is False


def test_r1f_handoff_artifact_writer_is_explicit(monkeypatch, tmp_path) -> None:
    import clawgym_overlay.reference_driver_r1f as driver

    monkeypatch.setenv("AGENT_LOGS_DIR", str(tmp_path))
    driver._write_handoff_artifact({"status": "complete", "handoff_digest": "a" * 64})
    assert json.loads((tmp_path / "r1f-handoff.json").read_text())["status"] == "complete"
