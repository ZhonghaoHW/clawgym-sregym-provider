"""R1d protocol wrapper.

This intentionally preserves host-controlled lifecycle stages. In particular,
``awaiting_cleanup`` is not converted into an agent terminal state and no
transcript-tail summary is manufactured as a handoff.
"""

import asyncio
import json
from collections.abc import Mapping
from typing import Any, cast

from langchain_core.messages import HumanMessage

from clients.stratus.stratus_agent.diagnosis_agent import DiagnosisAgent
from clients.stratus.stratus_agent.driver import driver
from clients.stratus.tools.submit_tool import manual_submit_tool

_upstream_wait_for_stage_switch = driver.wait_for_stage_switch
_r1d_handoff_validated = False


def _object(value: Any) -> Mapping[str, Any]:
    return cast(Mapping[str, Any], value) if isinstance(value, Mapping) else {}


async def _bounded_incomplete_submit(self: Any, state: Any) -> dict[str, Any]:
    self.logger.warning("R1d diagnosis budget exhausted; emitting explicit incomplete handoff.")
    await manual_submit_tool(ans='R1D_HANDOFF_JSON {"status":"incomplete"}')
    return {"submitted": True, "messages": [HumanMessage("R1d incomplete handoff submitted.")]}


async def _wait_preserving_host_stage(**kwargs: Any) -> str:
    if not _r1d_handoff_validated:
        # Fail closed: an unvalidated diagnosis cannot enter mitigation.
        return "awaiting_cleanup"
    targets = {str(stage) for stage in cast(set[Any] | list[Any] | tuple[Any, ...], kwargs["target_stages"])}
    targets.add("awaiting_cleanup")
    return await _upstream_wait_for_stage_switch(
        current_stage=kwargs["current_stage"],
        target_stages=targets,
        timeout=kwargs.get("timeout", 300),
        poll_interval=kwargs.get("poll_interval", 1.0),
    )


def main() -> None:
    DiagnosisAgent.force_submit = _bounded_incomplete_submit
    driver.wait_for_stage_switch = _wait_preserving_host_stage

    # Disable transcript-tail summaries: only the explicit R1D marker can
    # become a handoff, and no extra LLM summarization request is made.
    def _handoff_projection(last_state: Any, _prompt: Any) -> str:
        global _r1d_handoff_validated
        values_object = _object(getattr(last_state, "values", {}))
        messages = values_object.get("messages", [])
        for message in cast(list[Any], messages) if isinstance(messages, list) else []:
            content = (
                _object(message).get("content", "") if isinstance(message, Mapping) else getattr(message, "content", "")
            )
            if "R1D_HANDOFF_JSON" not in str(content):
                continue
            try:
                payload = json.loads(str(content).split("R1D_HANDOFF_JSON", 1)[-1].lstrip(" :"))
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict) and _object(payload).get("status") == "complete":
                _r1d_handoff_validated = True
                return json.dumps(payload, sort_keys=True, separators=(",", ":"))
        _r1d_handoff_validated = False
        return json.dumps({"status": "incomplete"}, sort_keys=True, separators=(",", ":"))

    driver.generate_run_summary = _handoff_projection
    asyncio.run(driver.main())


if __name__ == "__main__":
    main()
