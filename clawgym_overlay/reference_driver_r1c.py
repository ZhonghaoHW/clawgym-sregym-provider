"""R1c bounded Reference driver with a host-validated diagnosis handoff."""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, cast

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.constants import END
from langgraph.types import Command

from clients.stratus.stratus_agent.diagnosis_agent import DiagnosisAgent
from clients.stratus.stratus_agent.driver import driver
from clients.stratus.tools.submit_tool import submit_tool

try:
    from clawgym_overlay.r1c_protocol import normalise_submission
except ModuleNotFoundError:  # The frozen driver is mounted as a top-level module in the container.
    from r1c_protocol import normalise_submission

_upstream_wait_for_stage_switch = driver.wait_for_stage_switch
_original_diagnosis_submit: Callable[..., Any] | None = None
_original_diagnosis_call_model: Callable[..., Any] | None = None
_original_diagnosis_force_submit: Any = None
_original_diagnosis_should_continue: Callable[..., Any] | None = None
_original_driver_wait: Callable[..., Any] | None = None
_original_driver_summary: Callable[[Any, Any], str] | None = None
_handoff: dict[str, Any] | None = None
_handoff_rejections = 0


class R1cHandoffError(RuntimeError):
    """Raised when a bounded diagnosis cannot produce a valid handoff."""


def _command_update(command: Command[Any]) -> Mapping[str, Any] | None:
    update: Any = command.update
    return cast(Mapping[str, Any], update) if isinstance(update, Mapping) else None


def _identities() -> tuple[str, str]:
    return os.getenv("SREGYM_RUN_MANIFEST_DIGEST", ""), os.getenv("SREGYM_AGENT_RELEASE_DIGEST", "")


def _message_text(message: Any) -> str:
    raw_content = getattr(message, "content", "")
    if isinstance(raw_content, str):
        return raw_content
    if isinstance(raw_content, list):
        return "\n".join(
            str(cast(Mapping[str, Any], item).get("text", ""))
            for item in cast(list[Any], raw_content)
            if isinstance(item, Mapping) and isinstance(cast(Mapping[str, Any], item).get("text"), str)
        )
    return ""


def _tool_answer(response: Any, tool_name: str) -> str | None:
    if not isinstance(response, AIMessage):
        return None
    for raw_call in response.tool_calls:
        call = cast(Mapping[str, Any], raw_call)
        if call.get("name") != tool_name:
            continue
        raw_args: Any = call.get("args", {})
        if isinstance(raw_args, str):
            try:
                raw_args = json.loads(raw_args)
            except json.JSONDecodeError:
                return None
        if isinstance(raw_args, Mapping):
            args = cast(Mapping[str, Any], raw_args)
            if isinstance(args.get("ans"), str):
                return cast(str, args["ans"])
    return None


def _write_handoff_artifact(document: Mapping[str, Any]) -> None:
    """Persist only the canonical, identity-bound handoff in the agent logs."""

    logs = os.getenv("AGENT_LOGS_DIR")
    if not logs:
        raise R1cHandoffError("R1c agent log directory is unavailable")
    root = Path(logs)
    if not root.is_absolute() or root.is_symlink() or not root.is_dir():
        raise R1cHandoffError("R1c agent log directory is not a safe existing directory")
    current = root
    while current != current.parent:
        if current.is_symlink():
            raise R1cHandoffError("R1c agent log directory crosses a symlink")
        current = current.parent
    path = root / "r1c-handoff.json"
    if path.is_symlink() or path.exists():
        raise R1cHandoffError("R1c handoff artifact already exists")
    with path.open("x", encoding="utf-8") as handle:
        json.dump(document, handle, sort_keys=True, separators=(",", ":"))
        handle.flush()
        os.fsync(handle.fileno())


async def _gated_diagnosis_submit(ans: str, state: Mapping[str, Any], tool_call_id: str) -> Command[Any]:
    """Validate before the benchmark sees a diagnosis submission."""

    global _handoff, _handoff_rejections
    run_digest, release_digest = _identities()
    document = normalise_submission(ans, run_manifest_digest=run_digest, agent_release_digest=release_digest)
    if document is None:
        _handoff_rejections += 1
        return Command(
            update={
                "num_steps": max(int(state.get("num_steps", 0)) - 1, 0),
                "messages": [
                    ToolMessage(
                        content=(
                            "R1c handoff rejected. Submit one marker with all non-empty fields and the exact "
                            "NetworkPolicy target."
                        ),
                        tool_call_id=tool_call_id,
                    )
                ],
            }
        )
    if _original_diagnosis_submit is None:
        raise R1cHandoffError("R1c diagnosis submit hook is unavailable")
    result: Any = await _original_diagnosis_submit(ans=ans, state=state, tool_call_id=tool_call_id)
    if not isinstance(result, Command):
        raise R1cHandoffError("R1c diagnosis submit returned an invalid result")
    typed_result = cast(Command[Any], result)
    update = _command_update(typed_result)
    if update is None or update.get("submitted") is not True:
        return typed_result
    _write_handoff_artifact(document)
    _handoff = document
    return typed_result


async def _bounded_force_submit(self: Any, state: Mapping[str, Any]) -> dict[str, Any]:
    """Give the model one explicit typed finalization turn, then fail closed."""

    prompt = HumanMessage(
        "Finalization phase: call submit_tool exactly once with a complete "
        "R1C_HANDOFF_JSON object now. Do not call a read or mutation tool. "
        "If every required field is not supported by observations, fail closed."
    )
    response = self.llm.inference(messages=list(state.get("messages", [])) + [prompt], tools=[self.submit_tool])
    answer = _tool_answer(response, self.submit_tool.name)
    if answer is None:
        raise R1cHandoffError("R1c diagnosis did not produce a structured handoff")
    result = await _gated_diagnosis_submit(answer, state, "r1c-force-submit")
    update = _command_update(result)
    if update is None or update.get("submitted") is not True or _handoff is None:
        raise R1cHandoffError("R1c diagnosis handoff was not accepted")
    raw_messages = update.get("messages", [])
    messages = list(cast(list[Any], raw_messages)) if isinstance(raw_messages, list) else []
    return {"submitted": True, "messages": [prompt, *messages]}


def _r1c_call_model(self: Any, state: Mapping[str, Any]) -> dict[str, Any]:
    """Turn a valid structured text answer into the normal submit-tool path."""

    if _original_diagnosis_call_model is None:
        raise R1cHandoffError("R1c diagnosis model hook is unavailable")
    result: Any = _original_diagnosis_call_model(self, state)
    if not isinstance(result, Mapping):
        return cast(dict[str, Any], result)
    result_object = cast(Mapping[str, Any], result)
    raw_messages: Any = result_object.get("messages", [])
    messages = list(cast(list[Any], raw_messages)) if isinstance(raw_messages, list) else []
    if not messages:
        return cast(dict[str, Any], result)
    last = messages[-1]
    if not isinstance(last, AIMessage) or last.tool_calls:
        return cast(dict[str, Any], result)
    run_digest, release_digest = _identities()
    content = _message_text(last)
    if normalise_submission(content, run_manifest_digest=run_digest, agent_release_digest=release_digest) is None:
        return cast(dict[str, Any], result)
    synthetic = AIMessage(
        content=cast(Any, last.content),
        tool_calls=[
            {
                "name": self.submit_tool.name,
                "args": {"ans": content},
                "id": "r1c-structured-submit",
                "type": "tool_call",
            }
        ],
    )
    return {**result, "messages": [*messages[:-1], synthetic]}


def _r1c_should_continue(self: Any, state: Mapping[str, Any]) -> Any:
    """Reserve an explicit finalization turn for a non-submission answer.

    The upstream graph treats an assistant message without tool calls as a
    terminal answer.  That is correct for a conversational agent, but it is
    not sufficient for this bounded remediation protocol: a diagnosis may be
    expressed as ordinary text after a read-only tool call, and ending there
    would silently skip the typed handoff gate.  Route that one case to the
    existing, explicit ``force_submit`` node.  The node still requires a
    model-authored, identity-bound handoff and fails closed if it cannot get
    one; this does not synthesize a diagnosis or invoke the environment.
    """

    if _original_diagnosis_should_continue is None:
        raise R1cHandoffError("R1c diagnosis continuation hook is unavailable")
    route = _original_diagnosis_should_continue(self, state)
    if route != END or state.get("submitted") is True:
        return route
    raw_messages = state.get("messages", [])
    messages = cast(list[Any], raw_messages) if isinstance(raw_messages, list) else []
    if messages and isinstance(messages[-1], AIMessage) and not messages[-1].tool_calls:
        return "force_submit"
    return route


def _validated_summary(_last_state: Any, _summary_system_prompt: Any) -> str:
    if _handoff is None:
        raise R1cHandoffError("R1c mitigation cannot start without a validated handoff")
    return json.dumps(_handoff, sort_keys=True, separators=(",", ":"))


async def _wait_for_host_controlled_terminal(**kwargs: Any) -> str:
    if _handoff is None:
        return "done"
    targets = {str(stage) for stage in cast(set[Any] | list[Any] | tuple[Any, ...], kwargs["target_stages"])}
    targets.add("awaiting_cleanup")
    result = await _upstream_wait_for_stage_switch(
        current_stage=kwargs["current_stage"],
        target_stages=targets,
        timeout=kwargs.get("timeout", 300),
        poll_interval=kwargs.get("poll_interval", 1.0),
    )
    return "done" if result == "awaiting_cleanup" else result


def main() -> None:
    global _original_diagnosis_submit, _original_diagnosis_call_model, _original_diagnosis_force_submit
    global _original_diagnosis_should_continue
    global _original_driver_wait, _original_driver_summary, _handoff, _handoff_rejections
    _handoff = None
    _handoff_rejections = 0
    _original_diagnosis_submit = getattr(submit_tool, "coroutine", None)
    _original_diagnosis_call_model = DiagnosisAgent.call_model
    _original_diagnosis_force_submit = DiagnosisAgent.force_submit
    _original_diagnosis_should_continue = DiagnosisAgent.should_continue
    _original_driver_wait = cast(Callable[..., Any], driver.wait_for_stage_switch)
    _original_driver_summary = cast(Callable[[Any, Any], str], vars(driver)["generate_run_summary"])
    DiagnosisAgent.call_model = _r1c_call_model  # pyright: ignore[reportAttributeAccessIssue]
    DiagnosisAgent.force_submit = _bounded_force_submit  # pyright: ignore[reportAttributeAccessIssue]
    DiagnosisAgent.should_continue = _r1c_should_continue  # pyright: ignore[reportAttributeAccessIssue]
    submit_tool.coroutine = _gated_diagnosis_submit  # pyright: ignore[reportAttributeAccessIssue]
    driver.wait_for_stage_switch = _wait_for_host_controlled_terminal
    driver.generate_run_summary = _validated_summary
    try:
        asyncio.run(driver.main())
    finally:
        DiagnosisAgent.call_model = _original_diagnosis_call_model  # pyright: ignore[reportAttributeAccessIssue]
        DiagnosisAgent.force_submit = _original_diagnosis_force_submit  # pyright: ignore[reportAttributeAccessIssue]
        DiagnosisAgent.should_continue = _original_diagnosis_should_continue  # pyright: ignore[reportAttributeAccessIssue]
        submit_tool.coroutine = _original_diagnosis_submit  # pyright: ignore[reportAttributeAccessIssue]
        driver.wait_for_stage_switch = _original_driver_wait
        driver.generate_run_summary = _original_driver_summary


if __name__ == "__main__":
    main()
