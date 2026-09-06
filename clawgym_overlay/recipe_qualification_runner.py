"""WP8.5 workload and observability qualification runner.

This is intentionally separate from the historical fault-only
``qualification_runner``.  The runner accepts only an already materialized
component and an explicit qualification trial.  It has no candidate command,
path, manifest or executable extension point.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import hashlib
import importlib
import inspect
import json
import re
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, Protocol, cast

from clawgym.contracts import sha256_digest

_HEX = re.compile(r"^[0-9a-f]{64}$")
_FAMILIES = {"workload", "observability"}
_VARIANTS = {
    "workload": {"baseline", "low", "high"},
    "observability": {"standard", "high_frequency"},
}
_EXPECTED_TRIALS: dict[str, dict[str, tuple[str, int]]] = {
    "workload": {
        "workload-control-01": ("baseline", 2026091011),
        "workload-control-02": ("baseline", 2026091012),
        "workload-low-01": ("low", 2026091021),
        "workload-low-02": ("low", 2026091022),
        "workload-low-03": ("low", 2026091023),
        "workload-high-01": ("high", 2026091031),
        "workload-high-02": ("high", 2026091032),
        "workload-high-03": ("high", 2026091033),
    },
    "observability": {
        "observability-control-01": ("standard", 2026091111),
        "observability-control-02": ("standard", 2026091112),
        "observability-high-frequency-01": ("high_frequency", 2026091121),
        "observability-high-frequency-02": ("high_frequency", 2026091122),
        "observability-high-frequency-03": ("high_frequency", 2026091123),
    },
}
_ATTEMPT_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
_PROFILES: dict[str, dict[str, dict[str, int]]] = {
    "workload": {
        "baseline": {"rate": 100, "connections": 100, "duration_seconds": 30, "threads": 3},
        "low": {"rate": 25, "connections": 25, "duration_seconds": 30, "threads": 1},
        "high": {"rate": 250, "connections": 250, "duration_seconds": 30, "threads": 4},
    },
    "observability": {
        "standard": {"sample_interval_seconds": 15, "capture_window_seconds": 60},
        "high_frequency": {"sample_interval_seconds": 5, "capture_window_seconds": 120},
    },
}
_STATES = ("baseline", "configured", "recovered", "cleaned")


class RecipeQualificationError(ValueError):
    """Raised for invalid explicit inputs or a provider semantic failure."""


class RecipeQualificationBackend(Protocol):
    """Typed Provider seam; implementations own SREGym/Kubernetes I/O."""

    def reset(self) -> Mapping[str, Any]: ...

    def configure(self, profile: Mapping[str, int], family: str) -> Mapping[str, Any]: ...

    def observe(self, state: str, family: str) -> Mapping[str, Any]: ...

    def recover(self, profile: Mapping[str, int], family: str) -> Mapping[str, Any]: ...

    def cleanup(self) -> Mapping[str, Any]: ...


def _call(value: Any) -> Any:
    """Resolve a synchronous or asynchronous SREGym hook without leaking it."""
    if not inspect.isawaitable(value):
        return value
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(cast(Any, value))
    raise RecipeQualificationError("recipe backend hooks require a synchronous host thread")


class SREGymRecipeQualificationBackend:
    """Provider-owned adapter for the fixed SREGym hotel workload.

    The adapter is deliberately not a plugin: callers provide only a
    Conductor already constructed by the Provider composition root and a
    fixed telemetry callback.  It never accepts a command, path, selector or
    executable candidate field.
    """

    def __init__(
        self,
        conductor: Any,
        *,
        telemetry_snapshot: Any | None = None,
        control_namespace: str | None = None,
        health_timeout_seconds: int = 60,
        clock: Any = time.monotonic,
        sleeper: Any = time.sleep,
    ) -> None:
        self.conductor = conductor
        self.telemetry_snapshot = telemetry_snapshot
        self.control_namespace = control_namespace
        self.health_timeout_seconds = health_timeout_seconds
        self.clock = clock
        self.sleeper = sleeper

    def reset(self) -> Mapping[str, Any]:
        result = cast(Mapping[str, Any], _call(self.conductor.prepare_problem()))
        if self.control_namespace:
            core = getattr(getattr(self.conductor, "kubectl", None), "core_v1_api", None)
            client = importlib.import_module("kubernetes.client")
            if core is None or not callable(getattr(core, "create_namespace", None)):
                raise RecipeQualificationError("Provider control namespace API is unavailable")
            core.create_namespace(
                client.V1Namespace(
                    metadata=client.V1ObjectMeta(
                        name=self.control_namespace,
                        labels={"clawgym.io/recipe-qualification": "wp85"},
                    )
                )
            )
        return result

    def _app(self) -> Any:
        problem = getattr(self.conductor, "current_problem", None)
        if problem is None:
            raise RecipeQualificationError("SREGym problem is not prepared")
        # SREGym exposes a Problem from ``current_problem``; the concrete
        # application (and workload hooks) live on ``Problem.app``.  Keep the
        # direct-app shape for typed fakes and older compatibility adapters.
        return getattr(problem, "app", None) or problem

    def _node_ready(self) -> bool:
        api = getattr(getattr(self.conductor, "kubectl", None), "list_nodes", None)
        if not callable(api):
            return False
        result = api()
        items = list(getattr(result, "items", []) or [])
        if not items:
            return False
        return all(
            any(
                getattr(condition, "type", None) == "Ready" and getattr(condition, "status", None) == "True"
                for condition in list(getattr(getattr(node, "status", None), "conditions", None) or [])
            )
            for node in items
        )

    def _health(self) -> dict[str, Any]:
        problem = getattr(self.conductor, "current_problem", None)
        app = self._app()
        oracle_owner = getattr(problem, "mitigation_oracle", None) or getattr(app, "mitigation_oracle", None)
        oracle = getattr(oracle_owner, "_run_recommendation_probe", None)
        if not callable(oracle):
            raise RecipeQualificationError("SREGym recommendation oracle is unavailable")
        observed = oracle()
        if not isinstance(observed, bool):
            raise RecipeQualificationError("SREGym recommendation oracle returned a non-boolean result")
        healthy = observed
        return {
            "target_path_healthy": healthy,
            "non_target_healthy": self._node_ready(),
            "oracle": "pass" if healthy else "fail",
        }

    def _wait_for_health(self) -> dict[str, Any]:
        """Bound the application-start race before recording a lifecycle state.

        SREGym can report all Pods Ready while the recommendation Service is
        still warming up.  A single immediate Oracle probe would turn that
        transient into a false semantic disqualification.  The bounded wait
        preserves a persistent failure while making the readiness contract
        explicit and deterministic; it never retries a completed trial.
        """
        if type(self.health_timeout_seconds) is not int or self.health_timeout_seconds < 0:
            raise RecipeQualificationError("health wait bound is invalid")
        started = float(self.clock())
        observation = self._health()
        while (
            not (observation["target_path_healthy"] and observation["oracle"] == "pass")
            and float(self.clock()) - started < self.health_timeout_seconds
        ):
            self.sleeper(min(5, self.health_timeout_seconds))
            observation = self._health()
        return observation

    def _clean_health(self) -> dict[str, Any]:
        """Verify the post-cleanup boundary without probing deleted services.

        SREGym's recommendation oracle reads the application Service.  After
        host cleanup that Service is intentionally gone, so invoking the
        oracle would turn a successful cleanup into a misleading 404.  The
        cleaned-state observation instead proves the application namespace is
        absent and the cluster nodes remain healthy; the lifecycle oracle
        state is ``pass`` for this teardown invariant.
        """
        app = self._app()
        core = getattr(getattr(self.conductor, "kubectl", None), "core_v1_api", None)
        namespace = getattr(app, "namespace", "hotel-reservation")
        absent = False
        read_namespace = getattr(core, "read_namespace", None)
        if callable(read_namespace):
            try:
                read_namespace(namespace)
            except Exception as exc:
                absent = getattr(exc, "status", None) == 404
            else:
                absent = False
        return {
            "target_path_healthy": absent,
            "non_target_healthy": self._node_ready(),
            "oracle": "pass" if absent else "error",
        }

    def configure(self, profile: Mapping[str, int], family: str) -> Mapping[str, Any]:
        app = self._app()
        if family == "workload":
            stop = getattr(app, "stop_workload", None)
            create = getattr(app, "create_workload", None)
            start = getattr(app, "start_workload", None)
            if not callable(stop) or not callable(create) or not callable(start):
                raise RecipeQualificationError("SREGym workload hooks are unavailable")
            _call(stop())
            _call(
                create(
                    rate=profile["rate"],
                    connections=profile["connections"],
                    duration=profile["duration_seconds"],
                    threads=profile["threads"],
                )
            )
            _call(start())
            manager = getattr(app, "wrk", None)
            if manager is None or not callable(getattr(manager, "collect", None)):
                raise RecipeQualificationError("SREGym workload manager is unavailable")
            # The upstream stream manager cannot compare ``since_seconds``
            # against its unset first-run timestamp.  The first collection is
            # therefore anchored by the manager itself; subsequent collections
            # use the fixed profile window.
            collect_kwargs: dict[str, Any] = {"number": 50}
            if getattr(manager, "last_log_time", None) is not None:
                collect_kwargs["since_seconds"] = profile["duration_seconds"]
            entries = list(manager.collect(**collect_kwargs))
            requested = float(profile["rate"])
            duration = float(profile["duration_seconds"])
            request_count = sum(int(getattr(entry, "number", 0)) for entry in entries)
            success_count = sum(
                int(getattr(entry, "number", 0)) for entry in entries if bool(getattr(entry, "ok", False))
            )
            achieved = request_count / duration if duration else 0.0
            error_rate = 1.0 - (success_count / request_count) if request_count else 1.0
            return {
                "metrics": {
                    "requested_rate": requested,
                    "achieved_rate": achieved,
                    "error_rate": error_rate,
                    "request_count": request_count,
                    "success_count": success_count,
                    "saturation_protection": self._node_ready(),
                }
            }
        if self.telemetry_snapshot is None or not callable(self.telemetry_snapshot):
            raise RecipeQualificationError("fixed telemetry collector is required")
        snapshot = self.telemetry_snapshot(profile)
        if not isinstance(snapshot, Mapping):
            raise RecipeQualificationError("telemetry collector returned an unsafe payload")
        return {"metrics": dict(cast(Mapping[str, Any], snapshot))}

    def observe(self, state: str, family: str) -> Mapping[str, Any]:
        if state == "cleaned":
            return self._clean_health()
        return self._wait_for_health()

    def recover(self, profile: Mapping[str, int], family: str) -> Mapping[str, Any]:
        if family == "workload":
            app = self._app()
            stop = getattr(app, "stop_workload", None)
            if not callable(stop):
                raise RecipeQualificationError("SREGym workload stop hook is unavailable")
            _call(stop())
        return {}

    def cleanup(self) -> Mapping[str, Any]:
        cleanup = getattr(self.conductor, "cleanup_problem", None)
        if not callable(cleanup):
            raise RecipeQualificationError("SREGym cleanup hook is unavailable")
        result = _call(cleanup())
        value: Mapping[str, Any] = cast(Mapping[str, Any], result) if isinstance(result, Mapping) else {}
        cleaned = value.get("status") == "cleaned"
        if self.control_namespace:
            core = getattr(getattr(self.conductor, "kubectl", None), "core_v1_api", None)
            delete = getattr(core, "delete_namespace", None)
            if callable(delete):
                with contextlib.suppress(Exception):
                    delete(self.control_namespace)
            read = getattr(core, "read_namespace", None)
            if callable(read):
                try:
                    read(self.control_namespace)
                except Exception as exc:
                    if getattr(exc, "status", None) != 404:
                        cleaned = False
                else:
                    cleaned = False
        return {"cleanup": cleaned, "residue_absent": cleaned}


def collect_fixed_observability_snapshot(
    core_api: Any,
    profile: Mapping[str, int],
    *,
    clock: Any = time.monotonic,
    sleeper: Any = time.sleep,
) -> dict[str, Any]:
    """Collect only fixed observer health facts for a bounded capture window.

    The collector uses provider-owned label selectors and exports counts and
    booleans only.  It never exports pod names, logs, URLs, query responses or
    any caller-supplied selector.
    """
    interval = profile.get("sample_interval_seconds")
    window = profile.get("capture_window_seconds")
    if (
        isinstance(interval, bool)
        or isinstance(window, bool)
        or not isinstance(interval, int)
        or not isinstance(window, int)
        or interval <= 0
        or window <= 0
    ):
        raise RecipeQualificationError("observability capture profile is invalid")
    selectors = (
        "app.kubernetes.io/name=prometheus",
        "app.kubernetes.io/name=loki",
        "app-name=jaeger",
        "app-name=otel-collector",
    )
    started = float(clock())
    samples = 0
    observed_counts: list[int] = []
    complete = True
    while samples == 0 or float(clock()) - started < window:
        total = 0
        for selector in selectors:
            result = core_api.list_namespaced_pod(namespace="observe", label_selector=selector)
            pods = list(getattr(result, "items", []) or [])
            ready = [
                pod
                for pod in pods
                if any(
                    getattr(condition, "type", None) == "Ready" and getattr(condition, "status", None) == "True"
                    for condition in list(getattr(getattr(pod, "status", None), "conditions", None) or [])
                )
            ]
            complete = complete and bool(ready)
            total += len(pods)
        observed_counts.append(total)
        samples += 1
        if float(clock()) - started >= window:
            break
        sleeper(interval)
    return {
        "required_signals": ["prometheus", "loki", "jaeger", "otel_collector"],
        "samples": samples,
        "freshness_ok": complete,
        "signal_continuity": complete and len(observed_counts) == samples,
        "cardinality_ok": all(count <= 100 for count in observed_counts),
        "capture_window_seconds": window,
        "sample_interval_seconds": interval,
    }


def _read(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise RecipeQualificationError("input must be a regular file")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RecipeQualificationError("input is not valid JSON") from exc
    if not isinstance(value, dict):
        raise RecipeQualificationError("input must be a JSON object")
    return cast(dict[str, Any], value)


def validate_recipe_trial(trial: Mapping[str, Any]) -> None:
    required = {
        "schema_id",
        "trial_id",
        "recipe_family",
        "variant",
        "release_role",
        "seed",
        "attempt_id",
        "partition",
        "profile_digest",
    }
    if set(trial) - required and any(
        key not in required
        for key in trial
        if key
        not in {
            "schema_id",
            "trial_id",
            "recipe_family",
            "variant",
            "release_role",
            "seed",
            "attempt_id",
            "partition",
            "profile_digest",
        }
    ):
        # Runtime trials may contain only the execution inputs; output fields
        # are added by this module.  Reject unknown input fields explicitly.
        raise RecipeQualificationError("recipe trial contains unknown input fields")
    if trial.get("schema_id") != "clawgym.environment_recipe_qualification_trial.v1":
        raise RecipeQualificationError("recipe trial schema mismatch")
    family = trial.get("recipe_family")
    variant = trial.get("variant")
    if family not in _FAMILIES or variant not in _VARIANTS[cast(str, family)]:
        raise RecipeQualificationError("recipe family or variant is not allowlisted")
    role = "same_runtime_control" if variant in {"baseline", "standard"} else "candidate"
    expected = _EXPECTED_TRIALS[cast(str, family)].get(cast(str, trial.get("trial_id")))
    if (
        trial.get("release_role") != role
        or trial.get("partition") != "environment_recipe_qualification"
        or expected is None
        or trial.get("variant") != expected[0]
        or trial.get("seed") != expected[1]
    ):
        raise RecipeQualificationError("recipe trial scope or role is invalid")
    if isinstance(trial.get("seed"), bool) or not isinstance(trial.get("seed"), int) or trial["seed"] < 0:
        raise RecipeQualificationError("recipe trial seed is invalid")
    if (
        not isinstance(trial.get("trial_id"), str)
        or not trial["trial_id"]
        or not isinstance(trial.get("attempt_id"), str)
        or not _ATTEMPT_ID.fullmatch(trial["attempt_id"])
    ):
        raise RecipeQualificationError("recipe trial identity is invalid")
    if not isinstance(trial.get("profile_digest"), str) or not _HEX.fullmatch(trial["profile_digest"]):
        raise RecipeQualificationError("recipe profile digest is invalid")


def validate_component(component_bundle: Mapping[str, Any], trial: Mapping[str, Any]) -> Mapping[str, Any]:
    schema_id = component_bundle.get("schema_id")
    if schema_id not in {
        "clawgym.sregym_environment_component.v1",
        "clawgym.sregym_environment_component_bundle.v1",
    }:
        raise RecipeQualificationError("component schema mismatch")
    if schema_id == "clawgym.sregym_environment_component_bundle.v1":
        expected_bundle_fields = {
            "schema_id",
            "receipt_digest",
            "component_digest",
            "component",
            "component_bundle_digest",
        }
        if set(component_bundle) != expected_bundle_fields:
            raise RecipeQualificationError("component bundle inventory is not exact")
        bundle_digest = component_bundle.get("component_bundle_digest")
        if not isinstance(bundle_digest, str) or not _HEX.fullmatch(bundle_digest):
            raise RecipeQualificationError("component bundle digest is invalid")
        payload = {key: value for key, value in component_bundle.items() if key != "component_bundle_digest"}
        if sha256_digest(payload) != bundle_digest:
            raise RecipeQualificationError("component bundle digest mismatch")
        receipt_digest = component_bundle.get("receipt_digest")
        if not isinstance(receipt_digest, str) or not _HEX.fullmatch(receipt_digest):
            raise RecipeQualificationError("component receipt digest is invalid")
    component_raw = component_bundle.get("component")
    component: Mapping[str, Any] = (
        cast(Mapping[str, Any], component_raw) if isinstance(component_raw, Mapping) else component_bundle
    )
    if component.get("schema_id") != "clawgym.sregym_environment_component.v1":
        raise RecipeQualificationError("component document schema mismatch")
    required_component_fields = {
        "schema_id",
        "family",
        "recipe_id",
        "variant",
        "base_component_digest",
        "candidate_component_digest",
        "target_namespace",
        "target_resource",
        "profile",
    }
    if set(component) != required_component_fields:
        raise RecipeQualificationError("component inventory is not exact")
    family = trial.get("recipe_family")
    if not isinstance(family, str) or family not in _FAMILIES:
        raise RecipeQualificationError("recipe family is not allowlisted")
    component_map = component
    if component_map.get("family") != family or component_map.get("variant") not in _VARIANTS[family]:
        raise RecipeQualificationError("component family/variant mismatch")
    candidate_digest = component_map.get("candidate_component_digest")
    digest = component_map.get("candidate_component_digest") or component_bundle.get("component_digest")
    if not isinstance(digest, str) or not _HEX.fullmatch(digest) or trial.get("profile_digest") != digest:
        raise RecipeQualificationError("component/profile digest mismatch")
    if component_bundle.get("component_digest", digest) != digest or candidate_digest != digest:
        raise RecipeQualificationError("component digest cross-reference mismatch")
    base_digest = component_map.get("base_component_digest")
    if not isinstance(base_digest, str) or not _HEX.fullmatch(base_digest):
        raise RecipeQualificationError("component base digest is invalid")
    if (
        component_map.get("target_namespace") != "hotel-reservation"
        or component_map.get("target_resource") != "deny-all-recommendation"
    ):
        raise RecipeQualificationError("component target boundary is invalid")
    profile = component_map.get("profile")
    if not isinstance(profile, Mapping):
        raise RecipeQualificationError("component profile is not the static registry profile")
    profile_map = cast(Mapping[str, Any], profile)
    expected_profile = _PROFILES[family][cast(str, component_map["variant"])]
    if dict(profile_map) != expected_profile or any(
        isinstance(value, bool) or not isinstance(value, int) for value in profile_map.values()
    ):
        raise RecipeQualificationError("component profile is not the static registry profile")
    return component


def _state(state: str, observation: Mapping[str, Any], expected: str = "pass") -> dict[str, Any]:
    if state not in _STATES:
        raise RecipeQualificationError("invalid recipe lifecycle state")
    target = observation.get("target_path_healthy")
    non_target = observation.get("non_target_healthy")
    oracle = observation.get("oracle", "error")
    if not isinstance(target, bool) or not isinstance(non_target, bool) or oracle not in {"pass", "fail", "error"}:
        raise RecipeQualificationError("provider observation is not a safe typed observation")
    return {
        "state": state,
        "target_path_healthy": target,
        "non_target_healthy": non_target,
        "oracle_expected": expected,
        "oracle_observed": oracle,
    }


def _metrics(family: str, values: Mapping[str, Any]) -> dict[str, Any]:
    if family == "workload":
        result = {
            key: values.get(key)
            for key in (
                "requested_rate",
                "achieved_rate",
                "error_rate",
                "request_count",
                "success_count",
                "saturation_protection",
            )
        }
        requested_raw, achieved_raw, error_rate_raw = (
            result["requested_rate"],
            result["achieved_rate"],
            result["error_rate"],
        )
        if any(
            isinstance(item, bool) or not isinstance(item, (int, float))
            for item in (requested_raw, achieved_raw, error_rate_raw)
        ):
            raise RecipeQualificationError("workload metrics are incomplete")
        request_count = result["request_count"]
        success_count = result["success_count"]
        if (
            isinstance(request_count, bool)
            or not isinstance(request_count, int)
            or request_count < 0
            or isinstance(success_count, bool)
            or not isinstance(success_count, int)
            or success_count < 0
            or success_count > request_count
        ):
            raise RecipeQualificationError("workload counts are incomplete")
        requested = float(cast(int | float, requested_raw))
        achieved = float(cast(int | float, achieved_raw))
        error_rate = float(cast(int | float, error_rate_raw))
        if (
            requested <= 0
            or not 0.8 * requested <= achieved <= 1.2 * requested
            or not 0 <= error_rate <= 0.05
            or result["saturation_protection"] is not True
        ):
            raise RecipeQualificationError("workload metrics are outside qualification gates")
        return result
    required = values.get("required_signals")
    result = {
        key: values.get(key)
        for key in (
            "required_signals",
            "samples",
            "freshness_ok",
            "signal_continuity",
            "cardinality_ok",
            "capture_window_seconds",
            "sample_interval_seconds",
        )
    }
    if (
        not isinstance(required, list)
        or not required
        or any(not isinstance(item, str) or not item for item in cast(list[Any], required))
    ):
        raise RecipeQualificationError("observability signal set is incomplete")
    samples = result["samples"]
    if isinstance(samples, bool) or not isinstance(samples, int) or samples < 1:
        raise RecipeQualificationError("observability samples are incomplete")
    capture_window = result["capture_window_seconds"]
    sample_interval = result["sample_interval_seconds"]
    if (
        isinstance(capture_window, bool)
        or not isinstance(capture_window, int)
        or capture_window <= 0
        or isinstance(sample_interval, bool)
        or not isinstance(sample_interval, int)
        or sample_interval <= 0
    ):
        raise RecipeQualificationError("observability capture bounds are incomplete")
    if any(result[key] is not True for key in ("freshness_ok", "signal_continuity", "cardinality_ok")):
        raise RecipeQualificationError("observability signal gates failed")
    return result


def execute_recipe_trial(
    trial: Mapping[str, Any], component_bundle: Mapping[str, Any], backend: RecipeQualificationBackend
) -> dict[str, Any]:
    """Run the fixed baseline/configured/recovered/cleaned protocol."""
    validate_recipe_trial(trial)
    component = validate_component(component_bundle, trial)
    family = cast(str, trial["recipe_family"])
    variant = cast(str, trial["variant"])
    profile = cast(Mapping[str, int], component["profile"])
    cleanup_ok = False
    try:
        backend.reset()
        baseline = backend.observe("baseline", family)
        configured_result = backend.configure(profile, family)
        configured = backend.observe("configured", family)
        backend.recover(profile, family)
        recovered = backend.observe("recovered", family)
        metrics_source = configured_result.get("metrics", configured_result)
        metrics = _metrics(family, cast(Mapping[str, Any], metrics_source))
        clean_result = backend.cleanup()
        cleanup_ok = clean_result.get("cleanup") is True and clean_result.get("residue_absent") is True
        cleaned = backend.observe("cleaned", family)
        states = [
            _state("baseline", baseline),
            _state("configured", configured),
            _state("recovered", recovered),
            _state("cleaned", cleaned),
        ]
        oracle_consistent = all(item["oracle_observed"] == "pass" for item in states)
        passed = oracle_consistent and all(item["non_target_healthy"] for item in states) and cleanup_ok
        result: dict[str, Any] = {
            "schema_id": "clawgym.environment_recipe_qualification_trial.v1",
            "trial_id": trial["trial_id"],
            "recipe_family": family,
            "variant": variant,
            "release_role": trial["release_role"],
            "seed": trial["seed"],
            "attempt_id": trial["attempt_id"],
            "partition": trial["partition"],
            "profile_digest": trial["profile_digest"],
            "states": states,
            "metrics": metrics,
            "oracle_consistent": oracle_consistent,
            "cleanup": cleanup_ok,
            "residue_absent": cleanup_ok,
            "status": "completed" if passed else "semantic_disqualified",
            "failure_class": None if passed else ("cleanup_blocked" if not cleanup_ok else "semantic_disqualified"),
        }
        result["trial_digest"] = sha256_digest(result)
        return result
    except Exception:
        # The backend owns best-effort cleanup.  We never synthesize a
        # successful artifact from an exception or hide the primary failure.
        try:
            backend.cleanup()
        except Exception as cleanup_error:
            raise RecipeQualificationError("recipe execution and cleanup failed") from cleanup_error
        raise


def run_recipe_qualification_trial(
    *,
    trial_path: str | Path,
    component_bundle_path: str | Path,
    output_dir: str | Path,
    backend: RecipeQualificationBackend,
) -> dict[str, Any]:
    trial = _read(Path(trial_path))
    component = _read(Path(component_bundle_path))
    output = Path(output_dir)
    if output.exists() or output.is_symlink():
        raise RecipeQualificationError("qualification output directory already exists")
    result = execute_recipe_trial(trial, component, backend)
    output.mkdir(parents=True, exist_ok=False)
    payload = json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    with (output / "qualification-trial.json").open("xb") as handle:
        handle.write(payload)
    return result


def run_live_recipe_qualification_trial(
    *,
    trial_path: str | Path,
    component_bundle_path: str | Path,
    deployment_lock_path: str | Path,
    deployment_cache: str | Path,
    output_dir: str | Path,
) -> dict[str, Any]:
    """Run one no-Agent trial through the Provider's fixed SREGym root."""
    from clawgym_overlay.deployment_lock import load_deployment_lock
    from clawgym_overlay.locked_runtime import LockedRuntime
    from clawgym_overlay.release import load_release_manifests
    from sregym.conductor.conductor import Conductor, ConductorConfig

    trial = _read(Path(trial_path))
    validate_recipe_trial(trial)
    manifests = load_release_manifests(Path(__file__).parent / "manifests")
    config = ConductorConfig(deploy_loki=True, enable_noise=False, defer_cleanup=True, task_stages=("mitigation",))
    lock = LockedRuntime(load_deployment_lock(Path(deployment_lock_path)), deployment_cache)
    lock.configure_conductor(config)
    conductor = Conductor(config)
    lock.configure_services(conductor)
    conductor.problem_id = manifests["problem"]["problem_id"]
    control_namespace = "clawgym-rq-" + hashlib.sha256(str(trial["attempt_id"]).encode()).hexdigest()[:12]
    core = getattr(getattr(conductor, "kubectl", None), "core_v1_api", None)
    telemetry: Callable[[Mapping[str, int]], dict[str, Any]] | None = None
    if trial["recipe_family"] == "observability":

        def collect_telemetry(profile: Mapping[str, int]) -> dict[str, Any]:
            return collect_fixed_observability_snapshot(core, profile)

        telemetry = collect_telemetry

    backend = SREGymRecipeQualificationBackend(
        conductor, telemetry_snapshot=telemetry, control_namespace=control_namespace
    )
    return run_recipe_qualification_trial(
        trial_path=trial_path,
        component_bundle_path=component_bundle_path,
        output_dir=output_dir,
        backend=backend,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sregym-qualify-recipe")
    parser.add_argument("--trial", required=True, type=Path)
    parser.add_argument("--component-bundle", required=True, type=Path)
    parser.add_argument("--deployment-lock", required=True, type=Path)
    parser.add_argument("--deployment-cache", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    result = run_live_recipe_qualification_trial(
        trial_path=args.trial,
        component_bundle_path=args.component_bundle,
        deployment_lock_path=args.deployment_lock,
        deployment_cache=args.deployment_cache,
        output_dir=args.output,
    )
    print(json.dumps({"status": result["status"], "trial_id": result["trial_id"]}, sort_keys=True))
    return 0 if result["status"] == "completed" else 2


__all__ = [
    "RecipeQualificationBackend",
    "RecipeQualificationError",
    "execute_recipe_trial",
    "collect_fixed_observability_snapshot",
    "run_recipe_qualification_trial",
    "run_live_recipe_qualification_trial",
    "validate_component",
    "validate_recipe_trial",
]


if __name__ == "__main__":
    raise SystemExit(main())
