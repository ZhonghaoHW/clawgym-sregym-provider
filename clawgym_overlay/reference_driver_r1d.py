"""R1d protocol wrapper.

This intentionally preserves host-controlled lifecycle stages. In particular,
``awaiting_cleanup`` is not converted into an agent terminal state and no
transcript-tail summary is manufactured as a handoff.
"""

import asyncio

from clients.stratus.stratus_agent.driver import driver
from clients.stratus.stratus_agent.diagnosis_agent import DiagnosisAgent
from clients.stratus.tools.submit_tool import manual_submit_tool
from langchain_core.messages import HumanMessage


_upstream_wait_for_stage_switch = driver.wait_for_stage_switch


async def _bounded_incomplete_submit(self, state):
    self.logger.warning("R1d diagnosis budget exhausted; emitting explicit incomplete handoff.")
    await manual_submit_tool(ans='R1D_HANDOFF_JSON {"status":"incomplete"}')
    return {"submitted": True, "messages": [HumanMessage("R1d incomplete handoff submitted.")]}


async def _wait_preserving_host_stage(**kwargs):
    return await _upstream_wait_for_stage_switch(
        current_stage=kwargs["current_stage"],
        target_stages=set(kwargs["target_stages"]),
        timeout=kwargs.get("timeout", 300),
        poll_interval=kwargs.get("poll_interval", 1.0),
    )


def main() -> None:
    DiagnosisAgent.force_submit = _bounded_incomplete_submit
    driver.wait_for_stage_switch = _wait_preserving_host_stage
    # The upstream summary is never used as a typed handoff by the host.
    asyncio.run(driver.main())


if __name__ == "__main__":
    main()
