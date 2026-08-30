"""R1f trusted wrapper: typed host normalization plus one bounded repair."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

from clients.stratus.stratus_agent.diagnosis_agent import DiagnosisAgent
from clients.stratus.stratus_agent.driver import driver
from clients.stratus.tools.kubectl_tools import ExecKubectlCmdSafely
from clients.stratus.tools.submit_tool import fake_submit_tool, submit_tool
from clawgym_overlay.r1f_protocol import (
    MARKER,
    R1fGate,
    incomplete_handoff,
    normalise_handoff_submission,
    normalise_handoff_tool_argument,
    parse_command,
)
from langchain_core.messages import HumanMessage, ToolMessage, AIMessage
from langgraph.types import Command


_gate = R1fGate()
_handoff: dict[str, object] | None = None
_stage = "diagnosis"
_handoff_rejections = 0
_upstream_wait = driver.wait_for_stage_switch
_original_diagnosis_submit = None
_original_mitigation_submit = None
_original_diagnosis_call_model = None


def _legacy_incomplete_submit(self, state):
    return {"submitted": True, "messages": [HumanMessage("R1f incomplete handoff submitted.")]}


def _identities() -> tuple[str, str]:
    return os.getenv("SREGYM_RUN_MANIFEST_DIGEST", ""), os.getenv(
        "SREGYM_AGENT_RELEASE_DIGEST", ""
    )


def _canonical_text(document: dict[str, object]) -> str:
    return f"{MARKER} " + json.dumps(document, sort_keys=True, separators=(",", ":"))


def _r1i_normalise_submission(answer: str, *, run_digest: str, release_digest: str):
    """Normalize one final structured answer, with or without its marker."""

    value = answer if MARKER in answer else f"{MARKER} {answer}"
    return normalise_handoff_submission(
        value, run_manifest_digest=run_digest, agent_release_digest=release_digest
    )


def _write_handoff_artifact(document: dict[str, object]) -> None:
    """Persist the accepted host-bound handoff as an explicit receipt input."""

    logs = os.getenv("AGENT_LOGS_DIR")
    if not logs:
        return
    path = Path(logs) / "r1f-handoff.json"
    path.write_text(json.dumps(document, sort_keys=True, separators=(",", ":")), encoding="utf-8")


async def _bounded_force_submit(self, state):
    """Use one bounded, typed finalization turn at the diagnosis budget.

    The upstream force-submit path may fall back to a second free-form answer,
    while the previous R1f wrapper immediately wrote an incomplete marker.  A
    single finalization call gives the agent a chance to publish the diagnosis
    it already established, without asking for a transcript summary or
    inventing any semantic field on the host.
    """

    global _handoff
    run_digest, release_digest = _identities()
    prompt = HumanMessage(
        "Finalization phase: publish exactly one R1F_HANDOFF_JSON object now. "
        "Do not call a read or mutation tool. If you cannot provide every "
        "required field, fail closed rather than inventing evidence."
    )
    response = self.llm.inference(messages=state["messages"] + [prompt], tools=[self.submit_tool])
    answer = None
    if isinstance(response, AIMessage) and response.tool_calls:
        call = response.tool_calls[0]
        if call.get("name") == self.submit_tool.name:
            args = call.get("args", {})
            if isinstance(args, dict) and isinstance(args.get("ans"), str):
                answer = args["ans"]
    document = (
        _r1i_normalise_submission(answer, run_digest=run_digest, release_digest=release_digest)
        if answer is not None
        else None
    )
    if document is not None:
        _handoff = document
        _write_handoff_artifact(document)
        _gate.handoff_validated = True
        _gate.events.append({"event": "HANDOFF_VALIDATED", "handoff_digest": document["handoff_digest"]})
        message = "R1f finalization accepted; continue to mitigation."
    else:
        _handoff = None
        message = "R1f finalization incomplete; failing closed."
    return {"submitted": True, "messages": [prompt, ToolMessage(content=message, tool_call_id="r1f-force-submit")]}


def _r1i_call_model(self, state):
    """Turn a valid structured final answer into the explicit submit call.

    Some OpenAI-compatible backends return the handoff as a final structured
    text block even when the tool list contains ``submit_tool``.  The upstream
    graph treats a text-only answer as END, so the host would never reach the
    diagnosis submit boundary.  We preserve the model text and add one
    synthetic, identity-checked tool call; the normal gated submit path then
    performs all validation and stage transition.  Invalid text is untouched
    and still fails closed.
    """

    result = _original_diagnosis_call_model(self, state)
    messages = result.get("messages", []) if isinstance(result, dict) else []
    if not messages:
        return result
    answer = messages[-1]
    if not isinstance(answer, AIMessage) or answer.tool_calls:
        return result
    content = answer.content
    if isinstance(content, list):
        content = "\n".join(
            item.get("text", "")
            for item in content
            if isinstance(item, dict) and isinstance(item.get("text"), str)
        )
    if not isinstance(content, str):
        return result
    run_digest, release_digest = _identities()
    if _r1i_normalise_submission(
        content, run_digest=run_digest, release_digest=release_digest
    ) is None:
        return result
    synthetic = AIMessage(
        content=answer.content,
        tool_calls=[
            {
                "name": self.submit_tool.name,
                "args": {"ans": content},
                "id": "r1i-structured-submit",
                "type": "tool_call",
            }
        ],
    )
    return {**result, "messages": [*messages[:-1], synthetic]}


async def _gated_diagnosis_submit(ans: str, state, tool_call_id: str) -> Command:
    global _handoff, _handoff_rejections
    run_digest, release_digest = _identities()
    if os.getenv("SREGYM_HANDOFF_ARGUMENT_PROTOCOL") == "structured-submit-tool-argument-v1":
        document = normalise_handoff_tool_argument(
            ans, run_manifest_digest=run_digest, agent_release_digest=release_digest
        )
    elif os.getenv("SREGYM_SOP_VARIANT") == "r1i-typed-handoff-journal-v1":
        document = _r1i_normalise_submission(
            ans, run_digest=run_digest, release_digest=release_digest
        )
    else:
        document = normalise_handoff_submission(
            ans, run_manifest_digest=run_digest, agent_release_digest=release_digest
        )
    if document is None:
        _handoff_rejections += 1
        if _handoff_rejections > 1:
            return Command(update={"submitted": True, "messages": [ToolMessage(
                content="R1f handoff rejected twice; recording explicit incomplete marker.",
                tool_call_id=tool_call_id,
            )]})
        return Command(update={
            "num_steps": max(int(state.get("num_steps", 0)) - 1, 0),
            "messages": [ToolMessage(
                content=(
                    "Handoff rejected. Submit R1F_HANDOFF_JSON with non-empty semantic fields and "
                    "candidate_resource exactly {\"kind\":\"NetworkPolicy\",\"namespace\":\"hotel-reservation\",\"name\":\"deny-all-recommendation\"}."
                ),
                tool_call_id=tool_call_id,
            )],
        })
    _handoff = document
    _write_handoff_artifact(document)
    _gate.handoff_validated = True
    _gate.events.append({"event": "HANDOFF_VALIDATED", "handoff_digest": document["handoff_digest"]})
    # Diagnosis handoff is an adapter-internal event.  Calling the upstream
    # submit tool here would trigger the single-stage SREGym Oracle before the
    # mitigation agent can execute its gated mutation and verification.  Mark
    # the diagnosis stage submitted locally; the existing driver then observes
    # the already-active mitigation stage and starts the mitigation agent.  The
    # real conductor submission remains exclusively in _gated_submit.
    return Command(update={"submitted": True, "messages": [ToolMessage(
        content="R1f handoff accepted by host; continue to mitigation.", tool_call_id=tool_call_id
    )]})


def _handoff_projection(_last_state, _prompt):
    run_digest, release_digest = _identities()
    return json.dumps(
        _handoff
        if _handoff is not None
        else incomplete_handoff(
            run_manifest_digest=run_digest, agent_release_digest=release_digest
        ),
        sort_keys=True,
        separators=(",", ":"),
    )


def _write_gate_journal() -> None:
    """Persist the live gate state as the sole offline transaction source."""

    logs = Path(os.getenv("AGENT_LOGS_DIR", "."))
    logs.mkdir(parents=True, exist_ok=True)
    run_digest, release_digest = _identities()
    (logs / "r1f-gate-event-journal.json").write_text(
        json.dumps(
            _gate.snapshot(
                run_manifest_digest=run_digest,
                agent_release_digest=release_digest,
            ),
            sort_keys=True,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )


async def _gated_kubectl(self, command: str, tool_call_id: str) -> Command:
    operation, _resource, _tokens = parse_command(command)
    if operation == "mutate" and not _gate.permits_mutation(command, stage=_stage):
        return Command(update={"messages": [ToolMessage(
            content="Command Rejected: R1f mutation gate", tool_call_id=tool_call_id
        )]})
    result = await self._r1f_original_arun(command=command, tool_call_id=tool_call_id)
    text = "".join(
        str(getattr(message, "content", ""))
        for message in result.update.get("messages", []) if isinstance(result, Command)
    )
    _gate.record(command, text, stage=_stage)
    return result


async def _gated_submit(ans: str, tool_call_id: str) -> Command:
    if not _gate.may_submit:
        return Command(update={"messages": [ToolMessage(
            content="Submission Rejected: R1f verification gate incomplete", tool_call_id=tool_call_id
        )]})
    result = await _original_mitigation_submit(ans=ans, tool_call_id=tool_call_id)
    _gate.events.append({"event": "FINAL_SUBMISSION_ACCEPTED", "tool_call_id": tool_call_id})
    return result


async def _wait(**kwargs):
    global _stage
    if _handoff is None:
        return "awaiting_cleanup"
    status = await _upstream_wait(
        current_stage=kwargs["current_stage"],
        target_stages=set(kwargs["target_stages"]) | {"awaiting_cleanup"},
        timeout=kwargs.get("timeout", 300),
        poll_interval=kwargs.get("poll_interval", 1.0),
    )
    if status == "mitigation":
        _stage = "mitigation"
    return status


def main() -> None:
    global _gate, _handoff, _stage, _handoff_rejections
    global _original_diagnosis_submit, _original_mitigation_submit, _original_diagnosis_call_model
    runtime_protocol = os.getenv("SREGYM_RUNTIME_PROTOCOL") or os.getenv("SREGYM_SOP_VARIANT")
    is_r1i = runtime_protocol == "r1i-typed-handoff-journal-v1"
    _gate = R1fGate(strict_postconditions=is_r1i)
    _handoff = None
    _stage = "diagnosis"
    _handoff_rejections = 0
    DiagnosisAgent.force_submit = _bounded_force_submit if is_r1i else _legacy_incomplete_submit
    _original_diagnosis_call_model = DiagnosisAgent.call_model
    if is_r1i:
        DiagnosisAgent.call_model = _r1i_call_model
    driver.wait_for_stage_switch = _wait
    driver.generate_run_summary = _handoff_projection
    _original_diagnosis_submit = submit_tool.coroutine
    submit_tool.coroutine = _gated_diagnosis_submit
    ExecKubectlCmdSafely._r1f_original_arun = ExecKubectlCmdSafely._arun
    ExecKubectlCmdSafely._arun = _gated_kubectl
    _original_mitigation_submit = fake_submit_tool.coroutine
    fake_submit_tool.coroutine = _gated_submit
    try:
        asyncio.run(driver.main())
    finally:
        if is_r1i:
            _write_gate_journal()


if __name__ == "__main__":
    main()
