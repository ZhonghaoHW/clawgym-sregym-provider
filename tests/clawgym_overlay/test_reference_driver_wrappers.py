from __future__ import annotations

import asyncio
import hashlib
import json


def test_r1c_bounded_force_submit_and_summary(monkeypatch) -> None:
    import clawgym_overlay.reference_driver_r1c as driver

    calls: list[str] = []

    async def submit(*, ans: str) -> None:
        calls.append(ans)

    monkeypatch.setattr(driver, "manual_submit_tool", submit)

    class Logger:
        def warning(self, _message: str) -> None:
            return None

    class State:
        values = {"messages": ["a", "b"]}

    result = asyncio.run(driver._bounded_force_submit(type("Agent", (), {"logger": Logger()})(), {}))
    assert result["submitted"] is True
    assert calls == ['R1C_HANDOFF_JSON {"status":"incomplete"}']
    assert "R1c bounded" in driver._bounded_generate_run_summary(State(), None)


def test_r1c_host_stage_wait_is_bounded(monkeypatch) -> None:
    import clawgym_overlay.reference_driver_r1c as driver

    observed: dict[str, object] = {}

    async def upstream(**kwargs):
        observed.update(kwargs)
        return "awaiting_cleanup"

    monkeypatch.setattr(driver, "_upstream_wait_for_stage_switch", upstream)
    result = asyncio.run(
        driver._wait_for_host_controlled_terminal(
            current_stage="mitigation", target_stages=["done"], timeout=3, poll_interval=0.5
        )
    )
    assert result == "done"
    assert observed["target_stages"] == {"done", "awaiting_cleanup"}


def test_r1d_incomplete_submit_fails_closed(monkeypatch) -> None:
    import clawgym_overlay.reference_driver_r1d as driver

    calls: list[str] = []

    async def submit(*, ans: str) -> None:
        calls.append(ans)

    monkeypatch.setattr(driver, "manual_submit_tool", submit)

    class Logger:
        def warning(self, _message: str) -> None:
            return None

    result = asyncio.run(driver._bounded_incomplete_submit(type("Agent", (), {"logger": Logger()})(), {}))
    assert result["submitted"] is True
    assert calls == ['R1D_HANDOFF_JSON {"status":"incomplete"}']


def test_r1d_wait_preserves_host_terminal_before_handoff(monkeypatch) -> None:
    import clawgym_overlay.reference_driver_r1d as driver

    monkeypatch.setattr(driver, "_r1d_handoff_validated", False)
    assert asyncio.run(driver._wait_preserving_host_stage(current_stage="diagnosis", target_stages=[])) == (
        "awaiting_cleanup"
    )

    async def upstream(**_kwargs):
        return "mitigation"

    monkeypatch.setattr(driver, "_upstream_wait_for_stage_switch", upstream)
    monkeypatch.setattr(driver, "_r1d_handoff_validated", True)
    assert asyncio.run(driver._wait_preserving_host_stage(current_stage="diagnosis", target_stages=[])) == "mitigation"


def test_r1e_projection_accepts_identity_bound_marker(monkeypatch) -> None:
    import clawgym_overlay.reference_driver_r1e as driver

    run = "a" * 64
    release = "b" * 64
    payload = {
        "status": "complete",
        "symptom": "recommendation unavailable",
        "target_component": "NetworkPolicy/hotel-reservation/deny-all-recommendation",
        "evidence": ["policy blocks recommendation"],
        "root_cause_hypothesis": "deny policy",
        "candidate_resource": {
            "kind": "NetworkPolicy",
            "namespace": "hotel-reservation",
            "name": "deny-all-recommendation",
        },
        "minimal_remediation": "delete policy",
        "verification_plan": ["reread policy"],
    }
    monkeypatch.setenv("SREGYM_RUN_MANIFEST_DIGEST", run)
    monkeypatch.setenv("SREGYM_AGENT_RELEASE_DIGEST", release)
    document = {
        "schema_id": "clawgym.sregym_diagnosis_handoff.v2",
        **payload,
        "run_manifest_digest": run,
        "agent_release_digest": release,
    }
    document["handoff_digest"] = hashlib.sha256(
        json.dumps(document, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    state = type("State", (), {"values": {"messages": [{"content": "R1E_HANDOFF_JSON " + json.dumps(payload)}]}})()
    result = json.loads(driver._handoff_projection(state, None))
    assert result["status"] == "complete"
    assert driver._handoff_validated is True


def test_r1e_projection_rejects_missing_marker(monkeypatch) -> None:
    import clawgym_overlay.reference_driver_r1e as driver

    monkeypatch.setenv("SREGYM_RUN_MANIFEST_DIGEST", "a" * 64)
    monkeypatch.setenv("SREGYM_AGENT_RELEASE_DIGEST", "b" * 64)
    state = type("State", (), {"values": {"messages": [{"content": "plain text"}]}})()
    result = json.loads(driver._handoff_projection(state, None))
    assert result["status"] == "incomplete"
    assert driver._handoff_validated is False


def test_r1f_handoff_artifact_writer_is_explicit(monkeypatch, tmp_path) -> None:
    import clawgym_overlay.reference_driver_r1f as driver

    monkeypatch.setenv("AGENT_LOGS_DIR", str(tmp_path))
    driver._write_handoff_artifact({"status": "complete", "handoff_digest": "a" * 64})
    assert json.loads((tmp_path / "r1f-handoff.json").read_text())["status"] == "complete"
