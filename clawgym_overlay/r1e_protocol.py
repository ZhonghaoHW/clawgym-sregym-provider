"""Runtime enforcement for the bounded R1e remediation profile.

The gate is deliberately small and deterministic.  It is an adapter concern,
not a change to the public SREGym tool provider: only the exact fault target
may be deleted, and submit is unavailable until the postconditions are read.
"""

from __future__ import annotations

import hashlib
import json
import shlex
from dataclasses import dataclass, field

TARGET = {"kind": "NetworkPolicy", "namespace": "hotel-reservation", "name": "deny-all-recommendation"}


def _normalise_target(kind: str, namespace: str, name: str) -> dict[str, str]:
    return {"kind": kind, "namespace": namespace, "name": name}


def parse_command(command: str) -> tuple[str, dict[str, str]]:
    """Return ``(operation, resource)`` and reject shell/compound commands."""
    if any(token in command for token in (";", "&&", "||", "|", ">", "<", "`", "$((", "$(")):
        return "unknown", {"kind": "", "namespace": "", "name": ""}
    try:
        tokens = shlex.split(command)
    except ValueError:
        return "unknown", {"kind": "", "namespace": "", "name": ""}
    if len(tokens) < 2 or tokens[0] != "kubectl":
        return "unknown", {"kind": "", "namespace": "", "name": ""}
    verb = tokens[1]
    operation = (
        "read"
        if verb in {"get", "describe", "logs", "events", "top", "api-resources", "version"}
        else "mutate"
        if verb in {"delete", "apply", "patch", "replace", "create", "edit", "rollout", "scale", "set"}
        else "unknown"
    )
    kind = tokens[2] if len(tokens) > 2 else ""
    name = ""
    if len(tokens) > 3 and not tokens[3].startswith("-"):
        name = tokens[3]
    namespace = ""
    for index, token in enumerate(tokens):
        if token in {"-n", "--namespace"} and index + 1 < len(tokens):
            namespace = tokens[index + 1]
    if kind in {"netpol", "networkpolicy", "networkpolicies"}:
        kind = "NetworkPolicy"
    return operation, _normalise_target(kind, namespace, name)


def validate_handoff(document: dict[str, object], *, run_manifest_digest: str, agent_release_digest: str) -> bool:
    required = {
        "schema_id",
        "status",
        "run_manifest_digest",
        "agent_release_digest",
        "candidate_resource",
        "handoff_digest",
    }
    if not required.issubset(document) or document.get("schema_id") != "clawgym.sregym_diagnosis_handoff.v2":
        return False
    if (
        document.get("status") != "complete"
        or document.get("run_manifest_digest") != run_manifest_digest
        or document.get("agent_release_digest") != agent_release_digest
    ):
        return False
    target = document.get("candidate_resource")
    if target != TARGET:
        return False
    payload = {key: value for key, value in document.items() if key != "handoff_digest"}
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()
    return document.get("handoff_digest") == digest


@dataclass
class R1eGate:
    """State machine for one run; all transitions fail closed."""

    handoff_validated: bool = False
    precondition_read: bool = False
    mutation_count: int = 0
    mutation_executed: bool = False
    target_reread: bool = False
    endpoint_ready: bool = False
    events: list[dict[str, object]] = field(default_factory=lambda: [])

    def record(self, command: str, result: str, *, stage: str) -> bool:
        operation, resource = parse_command(command)
        allowed = False
        lower = result.lower()
        if operation == "read":
            if resource == TARGET and not self.mutation_executed:
                self.precondition_read = "notfound" not in lower and "not found" not in lower
                allowed = True
            elif resource == TARGET and self.mutation_executed:
                self.target_reread = "notfound" in lower or "not found" in lower
                allowed = True
            elif resource.get("name") == "recommendation" or "recommendation" in command.lower():
                self.endpoint_ready = (
                    any(marker in lower for marker in ("ready", "addresses", "endpoint")) and "not ready" not in lower
                )
                allowed = True
            else:
                allowed = True
        elif operation == "mutate":
            allowed = (
                stage == "mitigation"
                and self.handoff_validated
                and self.precondition_read
                and resource == TARGET
                and command.strip().startswith("kubectl delete")
                and self.mutation_count == 0
            )
            if allowed:
                self.mutation_count += 1
                self.mutation_executed = "error" not in lower and "rejected" not in lower
        self.events.append(
            {
                "command_sha256": hashlib.sha256(command.encode()).hexdigest(),
                "operation": operation,
                "resource": resource,
                "stage": stage,
                "allowed": allowed,
                "result": "rejected" if not allowed else "executed",
            }
        )
        return allowed

    @property
    def may_submit(self) -> bool:
        return (
            self.handoff_validated
            and self.mutation_count == 1
            and self.mutation_executed
            and self.target_reread
            and self.endpoint_ready
        )
