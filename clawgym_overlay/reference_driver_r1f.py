"""R1f trusted wrapper: typed host normalization plus one bounded repair."""

from __future__ import annotations

import asyncio
import json
import os

from clients.stratus.stratus_agent.diagnosis_agent import DiagnosisAgent
from clients.stratus.stratus_agent.driver import driver
from clients.stratus.tools.kubectl_tools import ExecKubectlCmdSafely
from clients.stratus.tools.submit_tool import fake_submit_tool, manual_submit_tool, submit_tool
from clawgym_overlay.r1f_protocol import (
    MARKER,
    R1fGate,
    incomplete_handoff,
    normalise_handoff_submission,
    parse_command,
)
from langchain_core.messages import HumanMessage, ToolMessage
from langgraph.types import Command


_gate = R1fGate()
_handoff: dict[str, object] | None = None
_stage = "diagnosis"
_handoff_rejections = 0
_upstream_wait = driver.wait_for_stage_switch
_original_diagnosis_submit = None
_original_mitigation_submit = None


def _identities() -> tuple[str, str]:
    return os.getenv("SREGYM_RUN_MANIFEST_DIGEST", ""), os.getenv(
        "SREGYM_AGENT_RELEASE_DIGEST", ""
    )


def _canonical_text(document: dict[str, object]) -> str:
    return f"{MARKER} " + json.dumps(document, sort_keys=True, separators=(",", ":"))


async def _incomplete_submit(self, state):
    run_digest, release_digest = _identities()
    await manual_submit_tool(ans=_canonical_text(incomplete_handoff(
        run_manifest_digest=run_digest, agent_release_digest=release_digest
    )))
    return {"submitted": True, "messages": [HumanMessage("R1f incomplete handoff submitted.")]}


async def _gated_diagnosis_submit(ans: str, state, tool_call_id: str) -> Command:
    global _handoff, _handoff_rejections
    run_digest, release_digest = _identities()
    document = normalise_handoff_submission(
        ans, run_manifest_digest=run_digest, agent_release_digest=release_digest
    )
    if document is None:
        _handoff_rejections += 1
        if _handoff_rejections > 1:
            await manual_submit_tool(ans=_canonical_text(incomplete_handoff(
                run_manifest_digest=run_digest, agent_release_digest=release_digest
            )))
            return Command(update={"submitted": True, "messages": [ToolMessage(
                content="R1f handoff rejected twice; submitting explicit incomplete marker.",
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
    _gate.handoff_validated = True
    return await _original_diagnosis_submit(
        ans=_canonical_text(document), state=state, tool_call_id=tool_call_id
    )


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
    return await _original_mitigation_submit(ans=ans, tool_call_id=tool_call_id)


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
    global _original_diagnosis_submit, _original_mitigation_submit
    _gate = R1fGate()
    _handoff = None
    _stage = "diagnosis"
    _handoff_rejections = 0
    DiagnosisAgent.force_submit = _incomplete_submit
    driver.wait_for_stage_switch = _wait
    driver.generate_run_summary = _handoff_projection
    _original_diagnosis_submit = submit_tool.coroutine
    submit_tool.coroutine = _gated_diagnosis_submit
    ExecKubectlCmdSafely._r1f_original_arun = ExecKubectlCmdSafely._arun
    ExecKubectlCmdSafely._arun = _gated_kubectl
    _original_mitigation_submit = fake_submit_tool.coroutine
    fake_submit_tool.coroutine = _gated_submit
    asyncio.run(driver.main())


if __name__ == "__main__":
    main()
