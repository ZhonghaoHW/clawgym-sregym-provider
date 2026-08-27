from __future__ import annotations

import asyncio
from types import SimpleNamespace

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


def test_r1b_summary_is_bounded_without_unbounded_llm_call() -> None:
    state = SimpleNamespace(values={"messages": [f"message-{i}" for i in range(20)]})
    summary = reference_driver._bounded_generate_run_summary(state, "ignored")
    assert "message-12" in summary
    assert "message-11" not in summary
    assert len(summary) < 12000


def test_r1b_force_submit_does_not_make_an_extra_llm_request(monkeypatch) -> None:
    submitted: list[str] = []

    async def submit(*, ans):
        submitted.append(ans)

    monkeypatch.setattr(reference_driver, "manual_submit_tool", submit)
    agent = SimpleNamespace(max_step=8, logger=SimpleNamespace(warning=lambda *args: None))
    result = asyncio.run(reference_driver._bounded_force_submit(agent, SimpleNamespace()))
    assert submitted == ["R1b bounded diagnosis handoff"]
    assert result["submitted"] is True
