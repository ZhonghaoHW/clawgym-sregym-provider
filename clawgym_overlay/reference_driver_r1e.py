"""R1e trusted adapter wrapper with a real, fail-closed remediation gate."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from collections.abc import Callable, Mapping
from typing import Any, cast

from langchain_core.messages import HumanMessage, ToolMessage
from langgraph.types import Command

from clawgym_overlay.r1e_protocol import TARGET, R1eGate, parse_command, validate_handoff
from clients.stratus.stratus_agent.diagnosis_agent import DiagnosisAgent
from clients.stratus.stratus_agent.driver import driver
from clients.stratus.tools.kubectl_tools import ExecKubectlCmdSafely
from clients.stratus.tools.submit_tool import fake_submit_tool, manual_submit_tool

_gate = R1eGate()
_handoff_validated = False
_stage = "diagnosis"
_upstream_wait = driver.wait_for_stage_switch
_original_submit_coroutine = None


def _digest(document: Mapping[str, Any], field: str) -> str:
    payload = {key: value for key, value in document.items() if key != field}
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


async def _incomplete_submit(self: Any, state: Mapping[str, Any]) -> dict[str, Any]:
    await manual_submit_tool(ans='R1E_HANDOFF_JSON {"status":"incomplete"}')
    return {"submitted": True, "messages": [HumanMessage("R1e incomplete handoff submitted.")]}


def _handoff_projection(last_state: Any, _prompt: Any) -> str:
    global _handoff_validated, _stage
    raw_values: Any = getattr(last_state, "values", {})
    values: dict[str, Any] = cast(dict[str, Any], raw_values) if isinstance(raw_values, dict) else {}
    raw_messages: Any = values.get("messages", [])
    messages: list[Any] = cast(list[Any], raw_messages) if isinstance(raw_messages, list) else []
    run_digest = os.getenv("SREGYM_RUN_MANIFEST_DIGEST", "")
    release_digest = os.getenv("SREGYM_AGENT_RELEASE_DIGEST", "")
    for raw_message in messages:
        message = cast(dict[str, Any], raw_message) if isinstance(raw_message, dict) else {}
        content = message.get("content", "")
        candidates = [str(content)]
        raw_calls: Any = message.get("tool_calls", [])
        calls: list[Any] = cast(list[Any], raw_calls) if isinstance(raw_calls, list) else []
        for raw_call in calls:
            call = cast(dict[str, Any], raw_call) if isinstance(raw_call, dict) else {}
            args: Any = call.get("args", {})
            if isinstance(args, dict):
                candidates.append(str(cast(dict[str, Any], args).get("ans", "")))
        if not any("R1E_HANDOFF_JSON" in item for item in candidates):
            continue
        try:
            raw = next(item for item in candidates if "R1E_HANDOFF_JSON" in item)
            value: Any = json.loads(raw.split("R1E_HANDOFF_JSON", 1)[-1].lstrip(" :"))
        except json.JSONDecodeError:
            continue
        if not isinstance(value, dict):
            continue
        value_object = cast(dict[str, Any], value)
        resource: dict[str, Any] = (
            cast(dict[str, Any], value_object.get("candidate_resource"))
            if isinstance(value_object.get("candidate_resource"), dict)
            else {}
        )
        document: dict[str, Any] = {
            "schema_id": "clawgym.sregym_diagnosis_handoff.v2",
            "status": value_object.get("status", "incomplete"),
            "run_manifest_digest": run_digest,
            "agent_release_digest": release_digest,
            "symptom": str(value_object.get("symptom", "")),
            "target_component": str(value_object.get("target_component", "")),
            "evidence": value_object.get("evidence", []) if isinstance(value_object.get("evidence"), list) else [],
            "root_cause_hypothesis": str(value_object.get("root_cause_hypothesis", "")),
            "candidate_resource": {
                "kind": str(resource.get("kind", "")),
                "namespace": str(resource.get("namespace", "")),
                "name": str(resource.get("name", "")),
            },
            "minimal_remediation": str(value_object.get("minimal_remediation", "")),
            "verification_plan": value_object.get("verification_plan", [])
            if isinstance(value_object.get("verification_plan"), list)
            else [],
        }
        document["handoff_digest"] = _digest(document, "handoff_digest")
        if validate_handoff(document, run_manifest_digest=run_digest, agent_release_digest=release_digest):
            _handoff_validated = True
            _gate.handoff_validated = True
            _stage = "mitigation"
            return json.dumps(document, sort_keys=True, separators=(",", ":"))
    _handoff_validated = False
    return json.dumps(
        {"schema_id": "clawgym.sregym_diagnosis_handoff.v2", "status": "incomplete"},
        sort_keys=True,
        separators=(",", ":"),
    )


async def _gated_kubectl(self: Any, command: str, tool_call_id: str) -> Command[Any]:
    operation, resource = parse_command(command)
    if operation == "mutate":
        if not (
            _stage == "mitigation"
            and _handoff_validated
            and _gate.precondition_read
            and resource == TARGET
            and command.strip().startswith("kubectl delete")
            and _gate.mutation_count == 0
        ):
            return Command(
                update={
                    "messages": [ToolMessage(content="Command Rejected: R1e mutation gate", tool_call_id=tool_call_id)]
                }
            )
    result: Any = await self._r1e_original_arun(command=command, tool_call_id=tool_call_id)
    text = ""
    result_update: Mapping[str, Any] = cast(Mapping[str, Any], result.update) if isinstance(result, Command) else {}
    for message in result_update.get("messages", []):
        text += str(getattr(message, "content", ""))
    _gate.record(command, text, stage=_stage)
    return cast(Command[Any], result)


async def _gated_submit(ans: str, tool_call_id: str) -> Command[Any]:
    if not _gate.may_submit:
        return Command(
            update={
                "messages": [
                    ToolMessage(
                        content="Submission Rejected: R1e verification gate incomplete", tool_call_id=tool_call_id
                    )
                ]
            }
        )
    submit = cast(Callable[..., Any], _original_submit_coroutine)
    return cast(Command[Any], await submit(ans=ans, tool_call_id=tool_call_id))


async def _wait(**kwargs: Any) -> Any:
    if not _handoff_validated:
        return "awaiting_cleanup"
    targets = set(kwargs["target_stages"])
    targets.add("awaiting_cleanup")
    wait_for_stage = cast(Callable[..., Any], _upstream_wait)
    return await wait_for_stage(
        current_stage=kwargs["current_stage"],
        target_stages=targets,
        timeout=kwargs.get("timeout", 300),
        poll_interval=kwargs.get("poll_interval", 1.0),
    )


def main() -> None:
    global _stage, _original_submit_coroutine
    _stage = "diagnosis"
    DiagnosisAgent.force_submit = _incomplete_submit  # pyright: ignore[reportAttributeAccessIssue] -- upstream agent has an untyped monkey-patched hook
    driver.wait_for_stage_switch = _wait
    driver.generate_run_summary = _handoff_projection
    ExecKubectlCmdSafely._r1e_original_arun = ExecKubectlCmdSafely._arun  # pyright: ignore[reportAttributeAccessIssue, reportPrivateUsage, reportUnknownMemberType] -- upstream private hook is intentionally wrapped for the frozen compatibility driver
    ExecKubectlCmdSafely._arun = _gated_kubectl  # pyright: ignore[reportAttributeAccessIssue, reportPrivateUsage] -- upstream private hook is intentionally wrapped for the frozen compatibility driver
    _original_submit_coroutine = getattr(fake_submit_tool, "coroutine", None)
    fake_submit_tool.coroutine = _gated_submit  # pyright: ignore[reportAttributeAccessIssue] -- upstream tool exposes a dynamic coroutine attribute
    asyncio.run(driver.main())


if __name__ == "__main__":
    main()
