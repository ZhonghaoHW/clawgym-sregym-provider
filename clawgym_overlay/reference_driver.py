"""R1b-only compatibility launcher for host-controlled SREGym cleanup.

The upstream driver predates the conductor's ``awaiting_cleanup`` state.  This
module is mounted read-only only for the registered R1b profile; it leaves the
upstream source and R0/R1 commands untouched.
"""

from __future__ import annotations

import asyncio

from clients.stratus.stratus_agent.driver import driver


_upstream_wait_for_stage_switch = driver.wait_for_stage_switch


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
    driver.wait_for_stage_switch = _wait_for_host_controlled_terminal
    asyncio.run(driver.main())


if __name__ == "__main__":
    main()
