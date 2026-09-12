from __future__ import annotations

import asyncio
import json

import pytest
from clawgym.contracts import sha256_digest

from clawgym_overlay.recipe_qualification_runner import (
    RecipeQualificationError,
    SREGymRecipeQualificationBackend,
    _call,
    _metrics,
    _state,
    collect_fixed_observability_snapshot,
    execute_recipe_trial,
    run_recipe_qualification_trial,
    validate_component,
    validate_recipe_trial,
)


def _component(family: str = "workload", variant: str = "baseline") -> dict:
    profile = {
        "workload": {
            "baseline": {"rate": 100, "connections": 100, "duration_seconds": 30, "threads": 3},
            "low": {"rate": 25, "connections": 25, "duration_seconds": 30, "threads": 1},
            "high": {"rate": 250, "connections": 250, "duration_seconds": 30, "threads": 4},
        },
        "observability": {
            "standard": {"sample_interval_seconds": 15, "capture_window_seconds": 60},
            "high_frequency": {"sample_interval_seconds": 5, "capture_window_seconds": 120},
        },
    }[family][variant]
    component_digest = sha256_digest(
        {"candidate_component_digest": "0" * 64, "family": family, "profile": profile, "variant": variant}
    )
    component = {
        "schema_id": "clawgym.sregym_environment_component.v1",
        "family": family,
        "recipe_id": f"{family}-{variant}",
        "variant": variant,
        "base_component_digest": "0" * 64,
        "candidate_component_digest": component_digest,
        "target_namespace": "hotel-reservation",
        "target_resource": "deny-all-recommendation",
        "profile": profile,
    }
    bundle = {
        "schema_id": "clawgym.sregym_environment_component_bundle.v1",
        "receipt_digest": "1" * 64,
        "component_digest": component_digest,
        "component": component,
    }
    bundle["component_bundle_digest"] = sha256_digest(bundle)
    return bundle


def _trial(family: str = "workload", variant: str = "baseline") -> dict:
    if family == "workload":
        trial_id = {"baseline": "workload-control-01", "low": "workload-low-01", "high": "workload-high-01"}[variant]
        seed = {"baseline": 2026091011, "low": 2026091021, "high": 2026091031}[variant]
    else:
        trial_id = {
            "standard": "observability-control-01",
            "high_frequency": "observability-high-frequency-01",
        }[variant]
        seed = {"standard": 2026091111, "high_frequency": 2026091121}[variant]
    return {
        "schema_id": "clawgym.environment_recipe_qualification_trial.v1",
        "trial_id": trial_id,
        "recipe_family": family,
        "variant": variant,
        "release_role": "same_runtime_control" if variant in {"baseline", "standard"} else "candidate",
        "seed": seed,
        "attempt_id": "attempt-1",
        "partition": "environment_recipe_qualification",
        "profile_digest": _component(family, variant)["component_digest"],
    }


class FakeBackend:
    def __init__(self, family: str):
        self.events: list[str] = []
        self.family = family

    def reset(self):
        self.events.append("reset")
        return {}

    def configure(self, profile, family):
        self.events.append("configure")
        if family == "workload":
            return {
                "metrics": {
                    "requested_rate": profile["rate"],
                    "achieved_rate": profile["rate"],
                    "error_rate": 0.01,
                    "request_count": 100,
                    "success_count": 99,
                    "saturation_protection": True,
                }
            }
        return {
            "metrics": {
                "required_signals": ["prometheus", "loki", "jaeger"],
                "samples": 10,
                "freshness_ok": True,
                "signal_continuity": True,
                "cardinality_ok": True,
                "capture_window_seconds": profile["capture_window_seconds"],
                "sample_interval_seconds": profile["sample_interval_seconds"],
            }
        }

    def observe(self, state, family):
        self.events.append("observe:" + state)
        return {"target_path_healthy": True, "non_target_healthy": True, "oracle": "pass"}

    def recover(self, profile, family):
        self.events.append("recover")
        return {}

    def cleanup(self):
        self.events.append("cleanup")
        return {"cleanup": True, "residue_absent": True}


def test_workload_and_observability_use_typed_lifecycle(tmp_path):
    for family, variant in (("workload", "low"), ("observability", "high_frequency")):
        trial = _trial(family, variant)
        backend = FakeBackend(family)
        result = execute_recipe_trial(trial, _component(family, variant), backend)
        assert result["status"] == "completed"
        assert backend.events == [
            "reset",
            "observe:baseline",
            "configure",
            "observe:configured",
            "recover",
            "observe:recovered",
            "cleanup",
            "observe:cleaned",
        ]


def test_rejects_mismatch_and_cleanup_failure():
    trial = _trial("workload", "baseline")
    with pytest.raises(RecipeQualificationError):
        execute_recipe_trial(trial, _component("observability", "standard"), FakeBackend("workload"))

    class Broken(FakeBackend):
        def cleanup(self):
            self.events.append("cleanup")
            return {"cleanup": False, "residue_absent": False}

    result = execute_recipe_trial(trial, _component(), Broken("workload"))
    assert result["status"] == "semantic_disqualified"
    assert result["failure_class"] == "cleanup_blocked"


def test_output_is_exclusive(tmp_path):
    trial = _trial()
    trial_path = tmp_path / "trial.json"
    bundle_path = tmp_path / "bundle.json"
    trial_path.write_text(json.dumps(trial), encoding="utf-8")
    bundle_path.write_text(json.dumps(_component()), encoding="utf-8")
    output = tmp_path / "out"
    run_recipe_qualification_trial(
        trial_path=trial_path, component_bundle_path=bundle_path, output_dir=output, backend=FakeBackend("workload")
    )
    with pytest.raises(RecipeQualificationError):
        run_recipe_qualification_trial(
            trial_path=trial_path, component_bundle_path=bundle_path, output_dir=output, backend=FakeBackend("workload")
        )


def test_observability_collector_uses_fixed_selectors_and_window():
    class Condition:
        type = "Ready"
        status = "True"

    class Status:
        conditions = [Condition()]

    class Pod:
        status = Status()

    class Core:
        def __init__(self):
            self.selectors = []

        def list_namespaced_pod(self, *, namespace, label_selector):
            assert namespace == "observe"
            self.selectors.append(label_selector)
            return type("Result", (), {"items": [Pod()]})()

    core = Core()
    ticks = iter([0, 0, 0.5, 0.5, 1])
    result = collect_fixed_observability_snapshot(
        core,
        {"sample_interval_seconds": 1, "capture_window_seconds": 1},
        clock=lambda: next(ticks),
        sleeper=lambda _: None,
    )
    assert result["samples"] == 2
    assert result["freshness_ok"] is True
    assert result["cardinality_ok"] is True
    assert set(core.selectors) == {
        "app.kubernetes.io/name=prometheus",
        "app.kubernetes.io/name=loki",
        "app-name=jaeger",
        "app-name=otel-collector",
    }


def test_sregym_backend_rejects_non_boolean_oracle() -> None:
    class Oracle:
        def _run_recommendation_probe(self):
            return "false"

    class App:
        mitigation_oracle = Oracle()

    class Conductor:
        current_problem = App()
        kubectl = type(
            "Kubectl",
            (),
            {
                "list_nodes": lambda self: type(
                    "Nodes",
                    (),
                    {
                        "items": [
                            type(
                                "Node",
                                (),
                                {
                                    "status": type(
                                        "Status",
                                        (),
                                        {"conditions": [type("Condition", (), {"type": "Ready", "status": "True"})()]},
                                    )()
                                },
                            )()
                        ]
                    },
                )()
            },
        )()

    backend = SREGymRecipeQualificationBackend(Conductor())
    with pytest.raises(RecipeQualificationError, match="non-boolean"):
        backend.observe("baseline", "workload")


def test_workload_first_collection_does_not_pass_unset_window() -> None:
    class Manager:
        last_log_time = None

        def collect(self, **kwargs):
            assert kwargs == {"number": 50}
            return [type("Entry", (), {"number": 100, "ok": True})()]

    class App:
        mitigation_oracle = type("Oracle", (), {"_run_recommendation_probe": lambda self: True})()
        wrk = Manager()

        def stop_workload(self):
            pass

        def create_workload(self, **kwargs):
            pass

        def start_workload(self):
            pass

    class Conductor:
        current_problem = App()
        kubectl = type(
            "Kubectl",
            (),
            {
                "list_nodes": lambda self: type(
                    "Nodes",
                    (),
                    {
                        "items": [
                            type(
                                "Node",
                                (),
                                {
                                    "status": type(
                                        "Status",
                                        (),
                                        {"conditions": [type("Condition", (), {"type": "Ready", "status": "True"})()]},
                                    )()
                                },
                            )()
                        ]
                    },
                )()
            },
        )()

    backend = SREGymRecipeQualificationBackend(Conductor())
    result = backend.configure({"rate": 100, "connections": 100, "duration_seconds": 30, "threads": 3}, "workload")
    assert result["metrics"]["request_count"] == 100


def test_sregym_backend_resolves_problem_application_and_oracle() -> None:
    class App:
        def stop_workload(self):
            pass

        def create_workload(self, **kwargs):
            pass

        def start_workload(self):
            pass

    class Problem:
        app = App()
        mitigation_oracle = type("Oracle", (), {"_run_recommendation_probe": lambda self: True})()

    class Conductor:
        current_problem = Problem()

    backend = SREGymRecipeQualificationBackend(Conductor())
    assert backend._app() is Conductor.current_problem.app


def test_sregym_backend_waits_for_transient_application_health() -> None:
    class Oracle:
        values = iter([False, False, True])

        def _run_recommendation_probe(self):
            return next(self.values)

    class App:
        mitigation_oracle = Oracle()

    class Conductor:
        current_problem = App()

    ticks = iter([0.0, 0.0, 1.0, 1.0, 2.0])
    sleeps: list[float] = []
    backend = SREGymRecipeQualificationBackend(
        Conductor(),
        health_timeout_seconds=5,
        clock=lambda: next(ticks),
        sleeper=sleeps.append,
    )
    observation = backend.observe("baseline", "workload")
    assert observation["target_path_healthy"] is True
    assert observation["oracle"] == "pass"
    assert sleeps == [5, 5]


def test_cleaned_observation_uses_namespace_absence_instead_of_deleted_service() -> None:
    class NotFound(Exception):
        status = 404

    class Core:
        def read_namespace(self, name):
            assert name == "hotel-reservation"
            raise NotFound()

        def list_nodes(self):
            return type("Nodes", (), {"items": []})()

    class Problem:
        app = type("App", (), {"namespace": "hotel-reservation"})()

    conductor = type(
        "Conductor", (), {"current_problem": Problem(), "kubectl": type("K", (), {"core_v1_api": Core()})()}
    )()
    observation = SREGymRecipeQualificationBackend(conductor).observe("cleaned", "workload")
    assert observation == {"target_path_healthy": True, "non_target_healthy": False, "oracle": "pass"}


def test_backend_rejects_missing_problem_oracle_and_node_api() -> None:
    backend = SREGymRecipeQualificationBackend(type("Conductor", (), {})())
    with pytest.raises(RecipeQualificationError, match="not prepared"):
        backend._app()

    class Problem:
        app = object()

    class Conductor:
        current_problem = Problem()

    backend = SREGymRecipeQualificationBackend(Conductor())
    with pytest.raises(RecipeQualificationError, match="oracle is unavailable"):
        backend.observe("baseline", "workload")
    assert backend._node_ready() is False


def test_backend_health_wait_and_cleanup_boundaries() -> None:
    class Oracle:
        def _run_recommendation_probe(self):
            return True

    class App:
        mitigation_oracle = Oracle()
        namespace = "app"

    class Problem:
        app = App()

    class Conductor:
        current_problem = Problem()

        def cleanup_problem(self):
            return {"status": "not-cleaned"}

    backend = SREGymRecipeQualificationBackend(Conductor(), health_timeout_seconds=0)
    assert backend.observe("baseline", "workload")["oracle"] == "pass"
    with pytest.raises(RecipeQualificationError, match="wait bound"):
        SREGymRecipeQualificationBackend(Conductor(), health_timeout_seconds=True).observe("baseline", "workload")
    assert backend.cleanup() == {"cleanup": False, "residue_absent": False}


def test_backend_rejects_missing_hooks_and_unsafe_telemetry() -> None:
    class Problem:
        app = object()

    class Conductor:
        current_problem = Problem()

    backend = SREGymRecipeQualificationBackend(Conductor())
    with pytest.raises(RecipeQualificationError, match="workload hooks"):
        backend.configure({"rate": 1}, "workload")
    with pytest.raises(RecipeQualificationError, match="telemetry collector"):
        backend.configure({}, "fault")

    class BadTelemetry(SREGymRecipeQualificationBackend):
        def _app(self):
            return object()

    backend = BadTelemetry(Conductor(), telemetry_snapshot=lambda profile: [])
    with pytest.raises(RecipeQualificationError, match="unsafe payload"):
        backend.configure({}, "fault")
    with pytest.raises(RecipeQualificationError, match="stop hook"):
        backend.recover({}, "workload")


def test_backend_cleanup_and_async_hook_paths() -> None:
    class App:
        namespace = "app"

    class Problem:
        app = App()

    class Conductor:
        current_problem = Problem()

        async def prepare_problem(self):
            return {"status": "prepared"}

        async def cleanup_problem(self):
            return {"status": "cleaned"}

    backend = SREGymRecipeQualificationBackend(Conductor())
    assert backend.reset() == {"status": "prepared"}
    assert backend.cleanup() == {"cleanup": True, "residue_absent": True}
    assert _call(asyncio.sleep(0)) is None

    async def inside_loop() -> None:
        pending = asyncio.sleep(0)
        with pytest.raises(RecipeQualificationError, match="synchronous host"):
            _call(pending)
        pending.close()

    asyncio.run(inside_loop())


def test_observability_collector_rejects_invalid_profile() -> None:
    with pytest.raises(RecipeQualificationError, match="capture profile"):
        collect_fixed_observability_snapshot(object(), {"sample_interval_seconds": 0, "capture_window_seconds": 1})
    with pytest.raises(RecipeQualificationError, match="capture profile"):
        collect_fixed_observability_snapshot(object(), {"sample_interval_seconds": True, "capture_window_seconds": 1})


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda trial: trial.update({"unexpected": True}), "unknown input"),
        (lambda trial: trial.update({"schema_id": "wrong"}), "schema mismatch"),
        (lambda trial: trial.update({"recipe_family": "fault"}), "allowlisted"),
        (lambda trial: trial.update({"variant": "missing"}), "allowlisted"),
        (lambda trial: trial.update({"trial_id": "not-the-expected-trial"}), "scope or role"),
        (lambda trial: trial.update({"release_role": "candidate"}), "scope or role"),
        (lambda trial: trial.update({"partition": "train"}), "scope or role"),
        (lambda trial: trial.update({"seed": -1}), "scope or role"),
        (lambda trial: trial.update({"seed": True}), "scope or role"),
        (lambda trial: trial.update({"attempt_id": "../escape"}), "identity"),
        (lambda trial: trial.update({"profile_digest": "bad"}), "profile digest"),
    ],
)
def test_validate_recipe_trial_rejects_each_boundary(mutation, message) -> None:
    trial = _trial()
    mutation(trial)
    with pytest.raises(RecipeQualificationError, match=message):
        validate_recipe_trial(trial)


def test_validate_recipe_trial_accepts_all_fixed_variants() -> None:
    for family, variants in (
        ("workload", ("baseline", "low", "high")),
        ("observability", ("standard", "high_frequency")),
    ):
        for variant in variants:
            validate_recipe_trial(_trial(family, variant))


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda component: component.update({"schema_id": "wrong"}), "schema mismatch"),
        (lambda component: component.pop("component_bundle_digest"), "inventory is not exact"),
        (lambda component: component.update({"component_bundle_digest": "bad"}), "bundle digest"),
        (lambda component: component.update({"receipt_digest": "bad"}), "receipt digest"),
        (lambda component: component["component"].update({"schema_id": "wrong"}), "document schema"),
        (lambda component: component["component"].pop("profile"), "inventory is not exact"),
        (lambda component: component["component"].update({"family": "fault"}), "family/variant"),
        (lambda component: component["component"].update({"candidate_component_digest": "bad"}), "digest"),
        (lambda component: component.update({"component_digest": "bad"}), "digest"),
        (lambda component: component["component"].update({"base_component_digest": "bad"}), "base digest"),
        (lambda component: component["component"].update({"target_resource": "other"}), "target boundary"),
        (lambda component: component["component"].update({"profile": []}), "static registry"),
        (lambda component: component["component"]["profile"].update({"rate": True}), "static registry"),
    ],
)
def test_validate_component_rejects_each_boundary(mutation, message) -> None:
    component = _component()
    mutation(component)
    if component.get("component_bundle_digest") != "bad" and "component_bundle_digest" in component:
        component["component_bundle_digest"] = sha256_digest(
            {key: value for key, value in component.items() if key != "component_bundle_digest"}
        )
    with pytest.raises(RecipeQualificationError, match=message):
        validate_component(component, _trial())


def test_validate_component_accepts_unwrapped_component_document() -> None:
    bundle = _component()
    validate_component(bundle["component"], _trial())


@pytest.mark.parametrize(
    ("family", "values", "message"),
    [
        (
            "workload",
            {
                "requested_rate": "100",
                "achieved_rate": 100,
                "error_rate": 0.01,
                "request_count": 1,
                "success_count": 1,
                "saturation_protection": True,
            },
            "metrics",
        ),
        (
            "workload",
            {
                "requested_rate": 100,
                "achieved_rate": 100,
                "error_rate": 0.01,
                "request_count": -1,
                "success_count": 0,
                "saturation_protection": True,
            },
            "counts",
        ),
        (
            "workload",
            {
                "requested_rate": 100,
                "achieved_rate": 10,
                "error_rate": 0.01,
                "request_count": 1,
                "success_count": 1,
                "saturation_protection": True,
            },
            "outside",
        ),
        (
            "observability",
            {
                "required_signals": [],
                "samples": 1,
                "freshness_ok": True,
                "signal_continuity": True,
                "cardinality_ok": True,
                "capture_window_seconds": 1,
                "sample_interval_seconds": 1,
            },
            "signal set",
        ),
        (
            "observability",
            {
                "required_signals": ["prometheus"],
                "samples": 0,
                "freshness_ok": True,
                "signal_continuity": True,
                "cardinality_ok": True,
                "capture_window_seconds": 1,
                "sample_interval_seconds": 1,
            },
            "samples",
        ),
        (
            "observability",
            {
                "required_signals": ["prometheus"],
                "samples": 1,
                "freshness_ok": True,
                "signal_continuity": True,
                "cardinality_ok": True,
                "capture_window_seconds": 0,
                "sample_interval_seconds": 1,
            },
            "capture bounds",
        ),
        (
            "observability",
            {
                "required_signals": ["prometheus"],
                "samples": 1,
                "freshness_ok": False,
                "signal_continuity": True,
                "cardinality_ok": True,
                "capture_window_seconds": 1,
                "sample_interval_seconds": 1,
            },
            "signal gates",
        ),
    ],
)
def test_metrics_rejects_unqualified_values(family, values, message) -> None:
    with pytest.raises(RecipeQualificationError, match=message):
        _metrics(family, values)


def test_state_rejects_unknown_state_and_unsafe_observation() -> None:
    with pytest.raises(RecipeQualificationError, match="lifecycle state"):
        _state("unknown", {"target_path_healthy": True, "non_target_healthy": True, "oracle": "pass"})
    with pytest.raises(RecipeQualificationError, match="safe typed"):
        _state("baseline", {"target_path_healthy": "yes", "non_target_healthy": True, "oracle": "pass"})


def test_execution_preserves_primary_error_and_reports_cleanup_failure() -> None:
    trial = _trial()

    class BrokenBackend(FakeBackend):
        def reset(self):
            raise RuntimeError("primary failure")

        def cleanup(self):
            raise RuntimeError("cleanup failure")

    with pytest.raises(RecipeQualificationError, match="execution and cleanup failed"):
        execute_recipe_trial(trial, _component(), BrokenBackend("workload"))
