"""R1c bounded Reference launcher with an explicit structured handoff marker."""

from __future__ import annotations

import asyncio

from clients.stratus.stratus_agent.driver import driver
from clients.stratus.stratus_agent.diagnosis_agent import DiagnosisAgent
from clients.stratus.tools.submit_tool import manual_submit_tool
from langchain_core.messages import HumanMessage


_upstream_wait_for_stage_switch = driver.wait_for_stage_switch


async def _bounded_force_submit(self, state):
    self.logger.warning("R1c diagnosis budget exhausted; submitting incomplete handoff.")
    await manual_submit_tool(ans="R1C_HANDOFF_JSON {\"status\":\"incomplete\"}")
    return {"submitted": True, "messages": [HumanMessage("R1C incomplete handoff submitted.")]}


def _bounded_generate_run_summary(last_state, summary_system_prompt):
    messages = last_state.values.get("messages", None)
    if messages is None:
        raise RuntimeError("StateSnapshot must contain messages!")
    # The structured handoff must be emitted by submit_tool; this summary is
    # only a bounded diagnostic hint and is never parsed as a handoff.
    return "R1c bounded structured-handoff run (last 8 messages):\n" + str(messages[-8:])[:12000]


async def _wait_for_host_controlled_terminal(**kwargs):
    targets = set(kwargs["target_stages"])
    targets.add("awaiting_cleanup")
    result = await _upstream_wait_for_stage_switch(
        current_stage=kwargs["current_stage"], target_stages=targets,
        timeout=kwargs.get("timeout", 300), poll_interval=kwargs.get("poll_interval", 1.0),
    )
    return "done" if result == "awaiting_cleanup" else result


def main() -> None:
    DiagnosisAgent.force_submit = _bounded_force_submit
    driver.wait_for_stage_switch = _wait_for_host_controlled_terminal
    driver.generate_run_summary = _bounded_generate_run_summary
    asyncio.run(driver.main())


if __name__ == "__main__":
    main()
