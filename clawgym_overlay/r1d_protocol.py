"""Pure protocol guards used by the R1d local replay tests.

The module deliberately has no Kubernetes, model or SREGym imports. It
provides deterministic validation/reduction primitives for the host-side
evidence boundary; the agent remains responsible for issuing the actual tool
call.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Iterable, Mapping


TARGET = {"kind": "NetworkPolicy", "namespace": "hotel-reservation", "name": "deny-all-recommendation"}
HANDOFF_FIELDS = ("symptom", "target_component", "evidence", "root_cause_hypothesis", "candidate_resource", "minimal_remediation", "verification_plan")
VALID_STAGES = {"diagnosis", "mitigation", "awaiting_cleanup", "done", "error"}


def _digest(document: Mapping[str, Any], field: str) -> str:
    value = {k: v for k, v in document.items() if k != field}
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def validate_handoff(document: Mapping[str, Any], *, run_manifest_digest: str, agent_release_digest: str) -> dict[str, Any]:
    """Validate and return a canonical handoff projection, failing closed."""
    if document.get("schema_id") != "clawgym.sregym_diagnosis_handoff.v2":
        raise ValueError("R1d handoff schema is invalid")
    if document.get("status") != "complete":
        raise ValueError("R1d handoff is incomplete")
    if document.get("run_manifest_digest") != run_manifest_digest or document.get("agent_release_digest") != agent_release_digest:
        raise ValueError("R1d handoff identity mismatch")
    if any(not document.get(field) for field in HANDOFF_FIELDS):
        raise ValueError("R1d handoff fields are incomplete")
    resource = document.get("candidate_resource")
    if not isinstance(resource, Mapping) or dict(resource) != TARGET:
        raise ValueError("R1d handoff target is not the declared resource")
    if document.get("handoff_digest") != _digest(document, "handoff_digest"):
        raise ValueError("R1d handoff digest mismatch")
    return dict(document)


def reduce_tool_events(events: Iterable[Mapping[str, Any]]) -> tuple[dict[str, Any], ...]:
    """Join tool calls/results across snapshots by ID, independent of order."""
    calls: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for snapshot in events:
        messages = snapshot.get("messages", snapshot)
        if not isinstance(messages, list):
            continue
        for message in messages:
            if not isinstance(message, Mapping):
                continue
            for call in message.get("tool_calls", []) if isinstance(message.get("tool_calls"), list) else []:
                if not isinstance(call, Mapping):
                    continue
                call_id = str(call.get("id", ""))
                if not call_id:
                    continue
                if call_id not in calls:
                    args = call.get("args", call.get("function", {}).get("arguments", {}))
                    if isinstance(args, str):
                        try:
                            args = json.loads(args)
                        except json.JSONDecodeError:
                            args = {}
                    command = str(args.get("command", "")) if isinstance(args, Mapping) else ""
                    lower = command.lower()
                    if str(call.get("name", call.get("function", {}).get("name", ""))) in {"submit_tool", "f_submit_tool", "manual_submit_tool"}:
                        operation = "submit"
                    elif lower.startswith(("kubectl get", "kubectl describe", "kubectl logs")):
                        operation = "read"
                    elif lower.startswith(tuple("kubectl " + verb for verb in ("apply", "patch", "delete", "replace", "create", "edit", "rollout", "scale", "set"))):
                        operation = "mutate"
                    else:
                        operation = "unknown"
                    calls[call_id] = {"id": call_id, "tool": str(call.get("name", call.get("function", {}).get("name", "unknown"))), "operation": operation, "command": command, "response": ""}
                    order.append(call_id)
            tool_call_id = message.get("tool_call_id")
            if tool_call_id and str(tool_call_id) in calls:
                calls[str(tool_call_id)]["response"] = str(message.get("content", ""))
    result = []
    for sequence, call_id in enumerate(order, 1):
        item = calls[call_id]
        response = item["response"].lower()
        outcome = "unknown" if not response else "rejected" if ("forbidden" in response or "command rejected" in response) else "failed" if ("error" in response or "exception" in response) else "executed"
        result.append({"sequence": sequence, "tool": item["tool"], "operation": item["operation"], "command_sha256": hashlib.sha256(item["command"].encode()).hexdigest(), "outcome": outcome})
    return tuple(result)


@dataclass(frozen=True, slots=True)
class R1dGate:
    """Deterministic state gate for one bounded remediation transaction."""

    handoff_validated: bool = False
    preconditions_verified: bool = False
    mutation_count: int = 0
    reread_done: bool = False
    verification_done: bool = False

    def accept_handoff(self, handoff: Mapping[str, Any], *, run_manifest_digest: str, agent_release_digest: str) -> "R1dGate":
        validate_handoff(handoff, run_manifest_digest=run_manifest_digest, agent_release_digest=agent_release_digest)
        return R1dGate(True, self.preconditions_verified, self.mutation_count, self.reread_done, self.verification_done)

    def verify_preconditions(self, *, policy_exists: bool) -> "R1dGate":
        if not self.handoff_validated or not policy_exists:
            raise ValueError("R1d preconditions are not satisfied")
        return R1dGate(True, True, self.mutation_count, self.reread_done, self.verification_done)

    def record_mutation(self) -> "R1dGate":
        if not self.preconditions_verified or self.mutation_count >= 1:
            raise ValueError("R1d mutation is outside the one-mutation gate")
        return R1dGate(True, True, 1, False, False)

    def record_reread(self) -> "R1dGate":
        if self.mutation_count != 1:
            raise ValueError("R1d reread requires one mutation")
        return R1dGate(True, True, 1, True, self.verification_done)

    def record_verification(self) -> "R1dGate":
        if not self.reread_done:
            raise ValueError("R1d verification requires reread")
        return R1dGate(True, True, 1, True, True)

    @property
    def may_submit(self) -> bool:
        return self.handoff_validated and self.preconditions_verified and self.mutation_count == 1 and self.reread_done and self.verification_done


def conductor_transition(stage: str, *, handoff_validated: bool) -> str:
    """Model the host stage seam used by the fake-conductor replay tests."""
    if stage not in VALID_STAGES:
        return "error"
    if stage == "diagnosis":
        return "mitigation" if handoff_validated else "awaiting_cleanup"
    return stage
