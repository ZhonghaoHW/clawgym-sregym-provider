"""R1b-only compatibility launcher for host-controlled SREGym cleanup.

The upstream driver predates the conductor's ``awaiting_cleanup`` state.  This
module is mounted read-only only for the registered R1b profile; it leaves the
upstream source and R0/R1 commands untouched.
"""

from __future__ import annotations

import asyncio

from clients.stratus.stratus_agent.driver import driver
from clients.stratus.stratus_agent.diagnosis_agent import DiagnosisAgent
from clients.stratus.tools.submit_tool import manual_submit_tool
from langchain_core.messages import HumanMessage


_upstream_wait_for_stage_switch = driver.wait_for_stage_switch


async def _bounded_force_submit(self, state):
    """Submit a deterministic handoff when the bounded diagnosis budget ends.

    The upstream ``BaseAgent.force_submit`` makes another LLM request to ask
    for a tool call.  That request can block after the step budget is already
    exhausted, leaving the conductor in ``mitigation`` forever.  R1b has a
    host-controlled terminal boundary, so it submits a fixed marker directly
    and lets the existing lifecycle/oracle decide the outcome.
    """
    self.logger.warning("Agent reached step limit (%s), using bounded direct submission.", self.max_step)
    await manual_submit_tool(ans="R1b bounded diagnosis handoff")
    return {
        "submitted": True,
        "messages": [HumanMessage("R1b bounded diagnosis handoff submitted.")],
    }


def _bounded_generate_run_summary(last_state, summary_system_prompt):
    """Keep the R1b handoff bounded without another unbounded LLM call.

    The upstream driver serializes the complete diagnosis transcript for a
    second summary request.  R1b deliberately caps this handoff to the final
    eight messages; the host Oracle remains authoritative for the verdict.
    """
    messages = last_state.values.get("messages", None)
    if messages is None:
        raise RuntimeError("StateSnapshot must contain messages!")
    tail = messages[-8:]
    return "R1b bounded diagnosis handoff (last 8 messages):\n" + str(tail)[:12000]


async def _wait_for_host_controlled_terminal(**kwargs):
    targets = set(kwargs["target_stages"])
    targets.add("awaiting_cleanup")
    result = await _upstream_wait_for_stage_switch(
        current_stage=kwargs["current_stage"],
        target_stages=targets,
        timeout=kwargs.get("timeout", 300),
        poll_interval=kwargs.get("poll_interval", 1.0),
    )
    return "done" if result == "awaiting_cleanup" else result


def main() -> None:
    DiagnosisAgent.force_submit = _bounded_force_submit
    driver.wait_for_stage_switch = _wait_for_host_controlled_terminal
    driver.generate_run_summary = _bounded_generate_run_summary
    asyncio.run(driver.main())


if __name__ == "__main__":
    main()
