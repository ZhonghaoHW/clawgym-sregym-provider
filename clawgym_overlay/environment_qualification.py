"""WP8.1 environment qualification boundary.

The backend is deliberately callback-based: ClawGym owns the lifecycle and
the Provider supplies SREGym-specific operations.  No candidate can provide a
command, path, manifest, image or executable callback.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping
import re
import asyncio
import inspect

from clawgym.contracts import sha256_digest

_HEX = re.compile(r"^[0-9a-f]{64}$")
_TARGET = {"kind": "NetworkPolicy", "namespace": "hotel-reservation", "name": "deny-all-recommendation"}
_FAMILIES = {
    "fault": {"ingress_egress", "ingress_only"},
    "workload": {"baseline", "low", "high"},
    "observability": {"standard", "high_frequency"},
}


def _conductor_hook(conductor: Any, method: str) -> Callable[..., Any]:
    callback = getattr(conductor, method, None)
    if not callable(callback):
        raise ValueError(f"SREGym conductor is missing {method} hook")
    return callback


def _invoke(callback: Callable[..., Any], *args: Any) -> Any:
    value = callback(*args)
    if inspect.isawaitable(value):
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(value)
        raise RuntimeError("qualification hooks require a synchronous host thread")
    return value


def _safe_observation(value: Any) -> dict[str, Any]:
    """Keep only typed qualification facts; never export callback payloads.

    SREGym callbacks may contain command output or host paths.  Qualification
    evidence is an interchange artifact, so receipts deliberately retain only
    the booleans/oracle needed by the state machine and a digest of any
    provider-owned summary.
    """
    raw = dict(value) if isinstance(value, Mapping) else {}
    safe: dict[str, Any] = {}
    for key in ("target_path", "non_target_healthy", "cleanup"):
        if isinstance(raw.get(key), bool):
            safe[key] = raw[key]
    if raw.get("oracle") in {"pass", "fail", "error"}:
        safe["oracle"] = raw["oracle"]
    summary = raw.get("summary")
    if isinstance(summary, Mapping):
        safe["summary_digest"] = sha256_digest(dict(summary))
    return safe


@dataclass(frozen=True, slots=True)
class EnvironmentControlProfile:
    profile_id: str
    revision: str
    families: Mapping[str, tuple[str, ...]]
    target: Mapping[str, str]
    required_states: tuple[str, ...] = ("baseline", "injected", "recovered", "cleaned")

    def to_dict(self) -> dict[str, Any]:
        value = {
            "schema_id": "clawgym.sregym_environment_control_profile.v1",
            "profile_id": self.profile_id,
            "revision": self.revision,
            "families": {key: list(vals) for key, vals in self.families.items()},
            "target": dict(self.target),
            "required_states": list(self.required_states),
        }
        value["profile_digest"] = sha256_digest(value)
        return value

    @classmethod
    def from_dict(cls, document: Mapping[str, Any]) -> "EnvironmentControlProfile":
        expected = {"schema_id", "profile_id", "revision", "families", "target", "required_states", "profile_digest"}
        if set(document) != expected or document.get("schema_id") != "clawgym.sregym_environment_control_profile.v1":
            raise ValueError("control profile fields are invalid")
        digest = document.get("profile_digest")
        payload = {key: value for key, value in document.items() if key != "profile_digest"}
        if not isinstance(digest, str) or not _HEX.fullmatch(digest) or sha256_digest(payload) != digest:
            raise ValueError("control profile digest mismatch")
        revision = document.get("revision")
        families = document.get("families")
        if not isinstance(revision, str) or not re.fullmatch(r"[0-9a-f]{40}", revision) or not isinstance(families, Mapping):
            raise ValueError("control profile identity is invalid")
        normalized = {key: tuple(value) for key, value in families.items() if isinstance(value, (list, tuple))}
        profile = cls(document["profile_id"], revision, normalized, document["target"], tuple(document["required_states"]))
        if profile.to_dict() != dict(document):
            raise ValueError("control profile is not the canonical allowlist")
        return profile


def default_environment_control_profile(revision: str) -> dict[str, Any]:
    """Return the only production control-point profile."""
    if not isinstance(revision, str) or not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("control profile revision must be a complete commit")
    profile = EnvironmentControlProfile(
        "sregym-environment-control-v1", revision,
        {key: tuple(sorted(values)) for key, values in _FAMILIES.items()}, _TARGET,
    )
    return profile.to_dict()


@dataclass(slots=True)
class SREGymEnvironmentQualificationBackend:
    """Run one no-agent qualification trial through explicit SREGym hooks."""

    reset: Callable[[], Mapping[str, Any]]
    provision: Callable[[str], Mapping[str, Any]]
    inject: Callable[[str], Mapping[str, Any]]
    observe: Callable[[str], Mapping[str, Any]]
    recover: Callable[[str], Mapping[str, Any]]
    cleanup: Callable[[str], Mapping[str, Any]]
    tool_probe: Callable[[], Mapping[str, Any]]
    isolation_probe: Callable[[], Mapping[str, Any]]
    profile_digest: str
    release_role: str
    target: Mapping[str, str] | None = None

    def __post_init__(self) -> None:
        self.target = dict(self.target or _TARGET)
        if self.target != _TARGET or not _HEX.fullmatch(self.profile_digest):
            raise ValueError("qualification target or profile identity is invalid")
        if self.release_role not in {"same_runtime_control", "candidate"}:
            raise ValueError("qualification release role is invalid")

    def run(self, *, trial_id: str, seed: int, attempt_id: str) -> dict[str, Any]:
        if not trial_id or not attempt_id or isinstance(seed, bool) or not isinstance(seed, int):
            raise ValueError("trial identity is invalid")
        receipts: list[dict[str, Any]] = []
        self.reset()
        baseline = dict(self.observe("baseline")); receipts.append({"phase": "baseline", "result": _safe_observation(baseline)})
        self.provision(trial_id)
        self.inject(trial_id)
        injected = dict(self.observe("injected")); receipts.append({"phase": "injected", "result": _safe_observation(injected)})
        tool = dict(self.tool_probe())
        isolation = dict(self.isolation_probe())
        self.recover(trial_id)
        recovered = dict(self.observe("recovered")); receipts.append({"phase": "recovered", "result": _safe_observation(recovered)})
        self.cleanup(trial_id)
        cleaned = dict(self.observe("cleaned")); receipts.append({"phase": "cleaned", "result": _safe_observation(cleaned)})
        expected = {"baseline": "pass", "injected": "fail", "recovered": "pass", "cleaned": "pass"}
        state_oracle = {name: item.get("oracle") for name, item in (("baseline", baseline), ("injected", injected), ("recovered", recovered), ("cleaned", cleaned))}
        passed = (
            state_oracle == expected
            and baseline.get("target_path") is True
            and injected.get("target_path") is False
            and recovered.get("target_path") is True
            and cleaned.get("target_path") is True
            and all(item.get("non_target_healthy") is True for item in (baseline, injected, recovered, cleaned))
            and tool.get("passed") is True and isolation.get("passed") is True
        )
        return {
            "schema_id": "clawgym.environment_qualification_trial.v1",
            "trial_id": trial_id, "seed": seed, "attempt_id": attempt_id,
            "release_role": self.release_role, "partition": "environment_qualification",
            "profile_digest": self.profile_digest, "target": dict(self.target),
            "state_oracle": state_oracle,
            "target_path": {name: item.get("target_path") for name, item in (("baseline", baseline), ("injected", injected), ("recovered", recovered), ("cleaned", cleaned))},
            "non_target_healthy": all(item.get("non_target_healthy") is True for item in (baseline, injected, recovered, cleaned)),
            "tool_usable": tool.get("passed") is True, "isolated": isolation.get("passed") is True,
            "cleanup": cleaned.get("cleanup") is True, "status": "completed" if passed else "semantic_disqualified",
            "failure_class": None if passed else "semantic_disqualified", "receipts": receipts,
        }


def build_sregym_qualification_backend(
    conductor: Any,
    *,
    profile_digest: str,
    release_role: str,
    observe: Callable[[str], Mapping[str, Any]],
    tool_probe: Callable[[], Mapping[str, Any]],
    isolation_probe: Callable[[], Mapping[str, Any]],
) -> SREGymEnvironmentQualificationBackend:
    """Bind the backend to explicit Conductor lifecycle hooks.

    The factory does not inspect candidate names or discover commands.  The
    supplied observation/tool/isolation callbacks are host-owned gates.
    """
    return SREGymEnvironmentQualificationBackend(
        reset=lambda: _invoke(_conductor_hook(conductor, "prepare_problem")),
        provision=lambda _trial: None,
        inject=lambda _trial: _invoke(_conductor_hook(conductor, "inject_problem_fault")),
        observe=observe,
        recover=lambda _trial: _invoke(_conductor_hook(conductor, "recover_problem_fault")),
        cleanup=lambda _trial: _invoke(_conductor_hook(conductor, "cleanup_problem")),
        tool_probe=tool_probe,
        isolation_probe=isolation_probe,
        profile_digest=profile_digest,
        release_role=release_role,
    )


__all__ = ["EnvironmentControlProfile", "SREGymEnvironmentQualificationBackend", "build_sregym_qualification_backend", "default_environment_control_profile"]
