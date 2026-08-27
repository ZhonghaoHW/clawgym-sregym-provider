from __future__ import annotations

import asyncio

from clawgym_overlay import reference_driver


def test_r1b_wrapper_normalizes_host_controlled_terminal(monkeypatch) -> None:
    async def upstream(**kwargs):
        assert "awaiting_cleanup" in kwargs["target_stages"]
        return "awaiting_cleanup"

    monkeypatch.setattr(reference_driver, "_upstream_wait_for_stage_switch", upstream)
    result = asyncio.run(
        reference_driver._wait_for_host_controlled_terminal(
            current_stage="diagnosis", target_stages={"done"}
        )
    )
    assert result == "done"
