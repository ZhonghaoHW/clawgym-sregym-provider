from __future__ import annotations

import pytest

from clawgym_overlay.environment_qualification import EnvironmentControlProfile, SREGymEnvironmentQualificationBackend, build_sregym_qualification_backend, default_environment_control_profile


def test_control_profile_is_closed_and_digest_bound() -> None:
    profile = default_environment_control_profile("a" * 40)
    assert profile["families"]["fault"] == ["ingress_egress", "ingress_only"]
    assert profile["target"] == {"kind": "NetworkPolicy", "namespace": "hotel-reservation", "name": "deny-all-recommendation"}
    assert len(profile["profile_digest"]) == 64
    assert EnvironmentControlProfile.from_dict(profile).to_dict() == profile
    with pytest.raises(ValueError):
        default_environment_control_profile("not-a-revision")


def test_backend_requires_exact_target_and_emits_expected_state_sequence() -> None:
    state = {"baseline": (True, "pass"), "injected": (False, "fail"), "recovered": (True, "pass"), "cleaned": (True, "pass")}
    calls: list[str] = []
    def observe(phase: str):
        calls.append("observe:" + phase)
        healthy, oracle = state[phase]
        return {"target_path": healthy, "non_target_healthy": True, "oracle": oracle, "cleanup": phase == "cleaned"}
    backend = SREGymEnvironmentQualificationBackend(
        reset=lambda: calls.append("reset"), provision=lambda trial: calls.append("provision"), inject=lambda trial: calls.append("inject"), observe=observe,
        recover=lambda trial: calls.append("recover"), cleanup=lambda trial: calls.append("cleanup"),
        tool_probe=lambda: {"passed": True}, isolation_probe=lambda: {"passed": True}, profile_digest="a" * 64, release_role="candidate",
    )
    result = backend.run(trial_id="candidate-01", seed=2026090111, attempt_id="attempt-1")
    assert result["status"] == "completed"
    assert all("command" not in receipt["result"] for receipt in result["receipts"])
    assert calls == ["reset", "observe:baseline", "provision", "inject", "observe:injected", "recover", "observe:recovered", "cleanup", "observe:cleaned"]

    with pytest.raises(ValueError):
        SREGymEnvironmentQualificationBackend(
            reset=lambda: {}, provision=lambda _: {}, inject=lambda _: {}, observe=lambda _: {}, recover=lambda _: {}, cleanup=lambda _: {}, tool_probe=lambda: {}, isolation_probe=lambda: {}, profile_digest="a" * 64, release_role="candidate", target={"kind": "Service"}
        )


def test_backend_receipts_redact_provider_payloads() -> None:
    def observe(phase: str):
        return {
            "target_path": phase != "injected",
            "non_target_healthy": True,
            "oracle": "fail" if phase == "injected" else "pass",
            "cleanup": phase == "cleaned",
            "command": "kubectl get secret gateway-token",
            "path": "/private/host/kubeconfig",
            "summary": {"pod": "recommendation", "output": "sensitive"},
        }
    backend = SREGymEnvironmentQualificationBackend(
        reset=lambda: None, provision=lambda _trial: None, inject=lambda _trial: None,
        observe=observe, recover=lambda _trial: None, cleanup=lambda _trial: None,
        tool_probe=lambda: {"passed": True}, isolation_probe=lambda: {"passed": True},
        profile_digest="b" * 64, release_role="candidate",
    )
    result = backend.run(trial_id="candidate-01", seed=1, attempt_id="a")
    rendered = str(result)
    assert "kubectl" not in rendered
    assert "kubeconfig" not in rendered
    assert "sensitive" not in rendered
    assert all(set(receipt["result"]) <= {"target_path", "non_target_healthy", "oracle", "cleanup", "summary_digest"} for receipt in result["receipts"])


def test_conductor_factory_uses_explicit_hooks_only() -> None:
    class FakeConductor:
        def prepare_problem(self): return None
        def inject_problem_fault(self): return None
        def recover_problem_fault(self): return None
        def cleanup_problem(self): return None
    backend = build_sregym_qualification_backend(
        FakeConductor(), profile_digest="a" * 64, release_role="candidate",
        observe=lambda phase: {"target_path": phase != "injected", "non_target_healthy": True, "oracle": "fail" if phase == "injected" else "pass", "cleanup": phase == "cleaned"},
        tool_probe=lambda: {"passed": True}, isolation_probe=lambda: {"passed": True},
    )
    assert backend.run(trial_id="candidate-01", seed=1, attempt_id="a")["status"] == "completed"
