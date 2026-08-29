"""R1e trusted adapter wrapper with a real, fail-closed remediation gate."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os

from clients.stratus.stratus_agent.diagnosis_agent import DiagnosisAgent
from clients.stratus.stratus_agent.driver import driver
from clients.stratus.tools.kubectl_tools import ExecKubectlCmdSafely
from clients.stratus.tools.submit_tool import fake_submit_tool, manual_submit_tool
from clawgym_overlay.r1e_protocol import R1eGate, TARGET, parse_command, validate_handoff
from langchain_core.messages import HumanMessage, ToolMessage
from langgraph.types import Command


_gate = R1eGate()
_handoff_validated = False
_stage = "diagnosis"
_upstream_wait = driver.wait_for_stage_switch
_original_submit_coroutine = None


def _digest(document: dict[str, object], field: str) -> str:
    payload = {key: value for key, value in document.items() if key != field}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


async def _incomplete_submit(self, state):
    await manual_submit_tool(ans='R1E_HANDOFF_JSON {"status":"incomplete"}')
    return {"submitted": True, "messages": [HumanMessage("R1e incomplete handoff submitted.")]}


def _handoff_projection(last_state, _prompt):
    global _handoff_validated, _stage
    values = getattr(last_state, "values", {})
    messages = values.get("messages", []) if isinstance(values, dict) else []
    run_digest = os.getenv("SREGYM_RUN_MANIFEST_DIGEST", "")
    release_digest = os.getenv("SREGYM_AGENT_RELEASE_DIGEST", "")
    for message in messages if isinstance(messages, list) else []:
        content = message.get("content", "") if isinstance(message, dict) else getattr(message, "content", "")
        candidates = [str(content)]
        calls = message.get("tool_calls", []) if isinstance(message, dict) else getattr(message, "tool_calls", [])
        for call in calls if isinstance(calls, list) else []:
            args = call.get("args", {}) if isinstance(call, dict) else {}
            if isinstance(args, dict):
                candidates.append(str(args.get("ans", "")))
        if not any("R1E_HANDOFF_JSON" in item for item in candidates):
            continue
        try:
            raw = next(item for item in candidates if "R1E_HANDOFF_JSON" in item)
            value = json.loads(raw.split("R1E_HANDOFF_JSON", 1)[-1].lstrip(" :"))
        except json.JSONDecodeError:
            continue
        if not isinstance(value, dict):
            continue
        resource = value.get("candidate_resource") if isinstance(value.get("candidate_resource"), dict) else {}
        document = {
            "schema_id": "clawgym.sregym_diagnosis_handoff.v2", "status": value.get("status", "incomplete"),
            "run_manifest_digest": run_digest, "agent_release_digest": release_digest,
            "symptom": str(value.get("symptom", "")), "target_component": str(value.get("target_component", "")),
            "evidence": value.get("evidence", []) if isinstance(value.get("evidence"), list) else [],
            "root_cause_hypothesis": str(value.get("root_cause_hypothesis", "")),
            "candidate_resource": {"kind": str(resource.get("kind", "")), "namespace": str(resource.get("namespace", "")), "name": str(resource.get("name", ""))},
            "minimal_remediation": str(value.get("minimal_remediation", "")),
            "verification_plan": value.get("verification_plan", []) if isinstance(value.get("verification_plan"), list) else [],
        }
        document["handoff_digest"] = _digest(document, "handoff_digest")
        if validate_handoff(document, run_manifest_digest=run_digest, agent_release_digest=release_digest):
            _handoff_validated = True
            _gate.handoff_validated = True
            _stage = "mitigation"
            return json.dumps(document, sort_keys=True, separators=(",", ":"))
    _handoff_validated = False
    return json.dumps({"schema_id": "clawgym.sregym_diagnosis_handoff.v2", "status": "incomplete"}, sort_keys=True, separators=(",", ":"))


async def _gated_kubectl(self, command: str, tool_call_id: str) -> Command:
    operation, resource = parse_command(command)
    if operation == "mutate":
        if not (_stage == "mitigation" and _handoff_validated and _gate.precondition_read and resource == TARGET and command.strip().startswith("kubectl delete") and _gate.mutation_count == 0):
            return Command(update={"messages": [ToolMessage(content="Command Rejected: R1e mutation gate", tool_call_id=tool_call_id)]})
    result = await self._r1e_original_arun(command=command, tool_call_id=tool_call_id)
    text = ""
    for message in result.update.get("messages", []) if isinstance(result, Command) else []:
        text += str(getattr(message, "content", ""))
    _gate.record(command, text, stage=_stage)
    return result


async def _gated_submit(ans: str, tool_call_id: str) -> Command:
    if not _gate.may_submit:
        return Command(update={"messages": [ToolMessage(content="Submission Rejected: R1e verification gate incomplete", tool_call_id=tool_call_id)]})
    return await _original_submit_coroutine(ans=ans, tool_call_id=tool_call_id)


async def _wait(**kwargs):
    if not _handoff_validated:
        return "awaiting_cleanup"
    targets = set(kwargs["target_stages"])
    targets.add("awaiting_cleanup")
    return await _upstream_wait(current_stage=kwargs["current_stage"], target_stages=targets, timeout=kwargs.get("timeout", 300), poll_interval=kwargs.get("poll_interval", 1.0))


def main() -> None:
    global _stage, _original_submit_coroutine
    _stage = "diagnosis"
    DiagnosisAgent.force_submit = _incomplete_submit
    driver.wait_for_stage_switch = _wait
    driver.generate_run_summary = _handoff_projection
    ExecKubectlCmdSafely._r1e_original_arun = ExecKubectlCmdSafely._arun
    ExecKubectlCmdSafely._arun = _gated_kubectl
    _original_submit_coroutine = fake_submit_tool.coroutine
    fake_submit_tool.coroutine = _gated_submit
    asyncio.run(driver.main())


if __name__ == "__main__":
    main()
