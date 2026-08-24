from __future__ import annotations

import ast
import asyncio
import logging
import sys
import time
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from types import MethodType, ModuleType, SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[2]


class StartProblemResult(StrEnum):
    SUCCESS = "success"
    SKIPPED_KHAOS_REQUIRED = "skipped_khaos_required"


def load_conductor_classes():
    """Execute the actual class bodies without importing SREGym's heavy services."""

    def ordered_subset(left, right):
        iterator = iter(right)
        return all(item in iterator for item in left)

    source = (ROOT / "sregym" / "conductor" / "conductor.py").read_text()
    parsed = ast.parse(source)
    selected = [
        node
        for node in parsed.body
        if isinstance(node, ast.ImportFrom) and node.module == "__future__"
        or isinstance(node, ast.ClassDef) and node.name in {"ConductorConfig", "Conductor"}
    ]
    module = ast.fix_missing_locations(ast.Module(body=selected, type_ignores=[]))
    module_name = "isolated_conductor_test"
    runtime_module = ModuleType(module_name)
    namespace = runtime_module.__dict__
    namespace.update({
        "__name__": module_name,
        "asyncio": asyncio,
        "dataclass": dataclass,
        "StartProblemResult": StartProblemResult,
        "time": time,
        "is_ordered_subset": ordered_subset,
    })
    sys.modules[module_name] = runtime_module
    exec(compile(module, "sregym/conductor/conductor.py", "exec"), namespace)
    return namespace["ConductorConfig"], namespace["Conductor"]


ConductorConfig, Conductor = load_conductor_classes()


def bare_conductor(*, defer_cleanup: bool = False):
    conductor = object.__new__(Conductor)
    conductor.config = ConductorConfig(defer_cleanup=defer_cleanup)
    conductor.problem_id = "network_policy_block"
    conductor.logger = logging.getLogger("test.conductor-seam")
    conductor.fault_injected = False
    conductor._fault_recovered = False
    conductor._application_cleaned = False
    conductor._cluster_reconciled = False
    conductor._noise_stopped = False
    conductor._baseline_captured = False
    conductor.submission_stage = "prepared"
    return conductor


def test_config_defaults_preserve_upstream_cleanup_and_allow_static_stages() -> None:
    default = ConductorConfig()
    assert default.defer_cleanup is False
    assert default.task_stages is None
    assert default.metrics_server_manifest is None
    assert default.openebs_manifest is None
    assert default.application_image_overrides is None
    assert default.mcp_image is None
    assert default.workload_image is None
    configured = bare_conductor()
    configured.config.task_stages = ("mitigation",)
    configured.get_problem_stages()
    assert configured.tasklist == ["mitigation"]


def test_fault_injection_seam_is_idempotent() -> None:
    conductor = bare_conductor()
    conductor.problem = object()
    conductor.stage_sequence = [{"name": "mitigation", "evaluation": lambda value: value}]
    calls = []

    def inject(self):
        calls.append("fault")
        self.fault_injected = True

    conductor._inject_fault = MethodType(inject, conductor)
    assert conductor.inject_problem_fault()["status"] == "injected"
    assert conductor.inject_problem_fault()["status"] == "already_injected"
    assert calls == ["fault"]
    assert conductor.waiting_for_agent is True
    assert conductor.submission_stage == "mitigation"


def test_recovery_and_cleanup_are_separate_and_idempotent() -> None:
    conductor = bare_conductor()
    calls = []
    app = SimpleNamespace(cleanup=lambda: calls.append("cleanup"))
    conductor.problem = SimpleNamespace(
        recover_fault=lambda: calls.append("recover"),
        app=app,
    )
    conductor.cluster_state = SimpleNamespace(reconcile_to_baseline=lambda: {})

    assert conductor.recover_problem_fault()["status"] == "recovered"
    assert conductor.recover_problem_fault()["status"] == "already_recovered"
    assert conductor.cleanup_problem()["status"] == "cleaned"
    assert conductor.cleanup_problem()["status"] == "cleaned"
    assert calls == ["recover", "cleanup"]
    assert conductor.submission_stage == "done"


def test_deferred_finish_waits_for_host_cleanup() -> None:
    conductor = bare_conductor(defer_cleanup=True)
    conductor._cleanup_sync = lambda: (_ for _ in ()).throw(AssertionError("cleanup ran"))
    conductor._finish_problem()
    assert conductor.submission_stage == "awaiting_cleanup"


def test_default_finish_preserves_automatic_cleanup() -> None:
    conductor = bare_conductor()
    calls = []
    conductor._cleanup_sync = lambda: calls.append("cleanup")
    conductor._finish_problem()
    assert calls == ["cleanup"]
    assert conductor.submission_stage == "tearing_down"


def test_start_problem_preserves_prepare_inject_advance_flow() -> None:
    conductor = bare_conductor()

    async def prepare(self):
        return StartProblemResult.SUCCESS

    def advance(self, start_index=0):
        assert start_index == 0
        self.fault_injected = True
        self.submission_stage = "mitigation"

    conductor.prepare_problem = MethodType(prepare, conductor)
    conductor._advance_to_next_stage = MethodType(advance, conductor)
    result = asyncio.run(conductor.start_problem())
    assert result is StartProblemResult.SUCCESS
    assert conductor.fault_injected is True
    assert conductor.submission_stage == "mitigation"


def test_prepare_refuses_to_replace_a_deferred_unclean_problem() -> None:
    conductor = bare_conductor(defer_cleanup=True)
    conductor.submission_stage = "awaiting_cleanup"
    with pytest.raises(RuntimeError, match="recovery and cleanup"):
        asyncio.run(conductor.prepare_problem())
