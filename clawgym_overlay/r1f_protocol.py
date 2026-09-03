"""Trusted R1f handoff normalization and bounded remediation gate.

R1f accepts only a semantic diagnosis submission from the model.  The host
normalizes the declared target, binds immutable identities, and creates the
canonical v2 handoff.  This removes the former dependence on transcript-line
formatting while preserving a fail-closed mutation boundary.
"""

from __future__ import annotations

import hashlib
import json
import re
import shlex
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, cast

MARKER = "R1F_HANDOFF_JSON"
TARGET = {
    "kind": "NetworkPolicy",
    "namespace": "hotel-reservation",
    "name": "deny-all-recommendation",
}
TARGET_TEXT = "NetworkPolicy/hotel-reservation/deny-all-recommendation"
_REQUIRED_SEMANTIC_FIELDS = (
    "symptom",
    "target_component",
    "evidence",
    "root_cause_hypothesis",
    "candidate_resource",
    "minimal_remediation",
    "verification_plan",
)


def _digest(document: Mapping[str, Any], field: str) -> str:
    payload = {key: value for key, value in document.items() if key != field}
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


def _marker_payload(value: str) -> dict[str, Any] | None:
    """Parse one marker followed by a complete JSON value, including newlines."""

    marker_at = value.find(MARKER)
    if marker_at < 0:
        return None
    tail = value[marker_at + len(MARKER) :].lstrip(" \t\r\n:")
    try:
        parsed, _ = json.JSONDecoder().raw_decode(tail)
    except json.JSONDecodeError:
        return None
    return cast(dict[str, Any], parsed) if isinstance(parsed, Mapping) else None


def _normalise_target(value: object) -> dict[str, str] | None:
    if isinstance(value, str):
        return dict(TARGET) if value.strip() == TARGET_TEXT else None
    if isinstance(value, Mapping):
        value_object = cast(Mapping[str, Any], value)
        target = {
            "kind": str(value_object.get("kind", "")),
            "namespace": str(value_object.get("namespace", "")),
            "name": str(value_object.get("name", "")),
        }
        return target if target == TARGET else None
    return None


def _normalise_evidence_list(value: object) -> list[str] | None:
    """Accept model-friendly text or a JSON string list, retain canonical lists.

    The published v2 handoff schema represents evidence and verification steps
    as arrays.  The R1f prompt asked for semantic fields but did not require a
    particular JSON container; DeepSeek consequently produced one useful
    narrative string for each field.  This adapter boundary owns that harmless
    representation conversion, while still rejecting empty or non-text items.
    """

    if isinstance(value, str):
        item = value.strip()
        return [item] if item else None
    if not isinstance(value, list):
        return None
    items = [item.strip() for item in cast(list[Any], value) if isinstance(item, str) and item.strip()]
    return items if len(items) == len(cast(list[Any], value)) and items else None


def _object(value: Any) -> Mapping[str, Any] | None:
    """Narrow untyped JSON values at the trajectory boundary."""
    return cast(Mapping[str, Any], value) if isinstance(value, Mapping) else None


def endpoint_result_ready(result: str) -> bool:
    """Recognise a ready recommendation endpoint without trusting prose.

    Stratus output is sanitised before it is retained, so the address may be
    ``[REDACTED]:8085`` or a normal cluster IP.  Merely looking for the word
    ``ready`` (or accepting any non-empty response) caused the runtime gate and
    the offline ledger to disagree.  The fixed service/port is the only
    endpoint accepted by this profile.
    """

    lower = result.lower()
    if any(marker in lower for marker in ("<none>", "not ready", "no addresses", "no endpoints")):
        return False
    # ``kubectl get endpoints`` emits either a compact table address
    # (``10.244.0.10:8085``) or separate address/port columns
    # (``10.244.0.10  8085/TCP``).  Accept both representations, including
    # the sanitised ``[REDACTED]`` address, while still requiring an address
    # and the fixed recommendation port.  A bare ``8085`` is insufficient:
    # it could be the port field of an endpoint object with no ready address.
    has_address = "[redacted]" in lower or bool(re.search(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", lower))
    has_port = bool(re.search(r"(?:[:\s])8085(?:/tcp|\b)", lower))
    return has_address and has_port


def normalise_handoff_submission(
    submission: str,
    *,
    run_manifest_digest: str,
    agent_release_digest: str,
) -> dict[str, Any] | None:
    """Create the canonical host-bound handoff from one model submission."""

    value = _marker_payload(submission)
    if value is None or any(field not in value for field in _REQUIRED_SEMANTIC_FIELDS):
        return None
    target = _normalise_target(value["candidate_resource"])
    text_fields = ("symptom", "target_component", "root_cause_hypothesis", "minimal_remediation")
    evidence = _normalise_evidence_list(value["evidence"])
    verification_plan = _normalise_evidence_list(value["verification_plan"])
    if (
        target is None
        or not all(isinstance(value[field], str) and value[field].strip() for field in text_fields)
        or evidence is None
        or verification_plan is None
    ):
        return None
    document: dict[str, Any] = {
        "schema_id": "clawgym.sregym_diagnosis_handoff.v2",
        "status": "complete",
        "run_manifest_digest": run_manifest_digest,
        "agent_release_digest": agent_release_digest,
        "symptom": value["symptom"].strip(),
        "target_component": value["target_component"].strip(),
        "evidence": evidence,
        "root_cause_hypothesis": value["root_cause_hypothesis"].strip(),
        "candidate_resource": target,
        "minimal_remediation": value["minimal_remediation"].strip(),
        "verification_plan": verification_plan,
    }
    document["handoff_digest"] = _digest(document, "handoff_digest")
    return document


def normalise_handoff_tool_argument(
    answer: str,
    *,
    run_manifest_digest: str,
    agent_release_digest: str,
) -> dict[str, Any] | None:
    """Normalize a typed ``submit_tool.ans`` argument.

    A tool argument is already delimited by the provider protocol, so the
    transcript marker is optional here.  The free-text submission parser above
    remains marker-required.  Keeping the two paths separate prevents a
    transcript tail from being mistaken for a host-authorized handoff while
    accepting the JSON object emitted by OpenAI-compatible tool calls.
    """

    value = answer.strip()
    if value.startswith(MARKER):
        value = value[len(MARKER) :].lstrip(" \t\r\n:")
    try:
        parsed, end = json.JSONDecoder().raw_decode(value)
    except json.JSONDecodeError:
        return None
    if value[end:].strip() or not isinstance(parsed, Mapping):
        return None
    if set(cast(Mapping[str, Any], parsed)) != set(_REQUIRED_SEMANTIC_FIELDS):
        return None
    return normalise_handoff_submission(
        MARKER + " " + json.dumps(parsed, ensure_ascii=False, separators=(",", ":")),
        run_manifest_digest=run_manifest_digest,
        agent_release_digest=agent_release_digest,
    )


def incomplete_handoff(*, run_manifest_digest: str, agent_release_digest: str) -> dict[str, Any]:
    document: dict[str, Any] = {
        "schema_id": "clawgym.sregym_diagnosis_handoff.v2",
        "status": "incomplete",
        "run_manifest_digest": run_manifest_digest,
        "agent_release_digest": agent_release_digest,
        "symptom": "",
        "target_component": "",
        "evidence": [],
        "root_cause_hypothesis": "",
        "candidate_resource": {"kind": "", "namespace": "", "name": ""},
        "minimal_remediation": "",
        "verification_plan": [],
    }
    document["handoff_digest"] = _digest(document, "handoff_digest")
    return document


def validate_handoff(document: Mapping[str, Any], *, run_manifest_digest: str, agent_release_digest: str) -> bool:
    return (
        document.get("schema_id") == "clawgym.sregym_diagnosis_handoff.v2"
        and document.get("status") == "complete"
        and document.get("run_manifest_digest") == run_manifest_digest
        and document.get("agent_release_digest") == agent_release_digest
        and document.get("candidate_resource") == TARGET
        and document.get("handoff_digest") == _digest(document, "handoff_digest")
    )


def _tool_submission_values(records: Iterable[Mapping[str, object]]) -> Iterable[str]:
    """Yield submitted ``ans`` values from preserved Stratus JSONL events."""

    for record in records:
        if not str(record.get("name", "")).endswith(".jsonl"):
            continue
        for line in str(record.get("text", "")).splitlines():
            try:
                event: Any = json.loads(line)
            except json.JSONDecodeError:
                continue
            event_object = _object(event) or {}
            messages: Any = event_object.get("messages", event_object)
            if not isinstance(messages, list):
                continue
            for raw_message in cast(list[Any], messages):
                message = _object(raw_message)
                if message is None:
                    continue
                calls: Any = message.get("tool_calls", [])
                for raw_call in cast(list[Any], calls) if isinstance(calls, list) else []:
                    call = _object(raw_call) or {}
                    function = _object(call.get("function")) or {}
                    name = str(call.get("name", function.get("name", "")))
                    arguments = call.get("args", function.get("arguments", {}))
                    if isinstance(arguments, str):
                        try:
                            arguments = json.loads(arguments)
                        except json.JSONDecodeError:
                            arguments = {}
                    arguments_object = _object(arguments)
                    if name == "submit_tool" and arguments_object is not None:
                        answer = arguments_object.get("ans")
                        if isinstance(answer, str):
                            yield answer


def handoff_from_trajectory_records(
    records: Iterable[Mapping[str, Any]], *, run_manifest_digest: str, agent_release_digest: str
) -> dict[str, Any]:
    for answer in _tool_submission_values(records):
        document = normalise_handoff_submission(
            answer,
            run_manifest_digest=run_manifest_digest,
            agent_release_digest=agent_release_digest,
        ) or normalise_handoff_tool_argument(
            answer,
            run_manifest_digest=run_manifest_digest,
            agent_release_digest=agent_release_digest,
        )
        if document is not None:
            return document
    return incomplete_handoff(run_manifest_digest=run_manifest_digest, agent_release_digest=agent_release_digest)


def parse_command(command: str) -> tuple[str, dict[str, str], tuple[str, ...]]:
    """Classify a simple kubectl command and reject shell composition."""

    if any(token in command for token in (";", "&&", "||", "|", ">", "<", "`", "$(", "\n")):
        return "unknown", {"kind": "", "namespace": "", "name": ""}, ()
    try:
        tokens = tuple(shlex.split(command))
    except ValueError:
        return "unknown", {"kind": "", "namespace": "", "name": ""}, ()
    if len(tokens) < 2 or tokens[0] != "kubectl":
        return "unknown", {"kind": "", "namespace": "", "name": ""}, tokens
    verb = tokens[1]
    operation = "read" if verb in {"get", "describe", "logs"} else "mutate" if verb == "delete" else "unknown"
    kind = tokens[2] if len(tokens) > 2 else ""
    name = tokens[3] if len(tokens) > 3 and not tokens[3].startswith("-") else ""
    if kind in {"netpol", "networkpolicy", "networkpolicies"}:
        kind = "NetworkPolicy"
    elif kind in {"endpoint", "endpoints", "ep"}:
        kind = "Endpoints"
    namespace = ""
    for index, token in enumerate(tokens):
        if token in {"-n", "--namespace"} and index + 1 < len(tokens):
            namespace = tokens[index + 1]
    return operation, {"kind": kind, "namespace": namespace, "name": name}, tokens


def is_exact_delete(tokens: tuple[str, ...]) -> bool:
    return tokens in {
        ("kubectl", "delete", "networkpolicy", "deny-all-recommendation", "-n", "hotel-reservation"),
        ("kubectl", "delete", "networkpolicy", "deny-all-recommendation", "--namespace", "hotel-reservation"),
        ("kubectl", "delete", "netpol", "deny-all-recommendation", "-n", "hotel-reservation"),
        ("kubectl", "delete", "netpol", "deny-all-recommendation", "--namespace", "hotel-reservation"),
    }


@dataclass
class R1fGate:
    """One fail-closed remediation transaction for the fixed E0 fault."""

    handoff_validated: bool = False
    precondition_read: bool = False
    mutation_count: int = 0
    mutation_executed: bool = False
    target_reread: bool = False
    endpoint_ready: bool = False
    events: list[dict[str, object]] = field(default_factory=lambda: [])
    strict_postconditions: bool = False

    def snapshot(self, *, run_manifest_digest: str, agent_release_digest: str) -> dict[str, object]:
        """Return the canonical runtime state used by offline evidence."""

        events: list[dict[str, object]] = list(self.events)
        document: dict[str, object] = {
            "schema_id": "clawgym.sregym_gate_event_journal.v1",
            "run_manifest_digest": run_manifest_digest,
            "agent_release_digest": agent_release_digest,
            "state": {
                "handoff_validated": self.handoff_validated,
                "precondition_read": self.precondition_read,
                "mutation_count": self.mutation_count,
                "mutation_executed": self.mutation_executed,
                "target_reread": self.target_reread,
                "endpoint_ready": self.endpoint_ready,
                "may_submit": self.may_submit,
            },
            "events": events,
        }
        document["journal_digest"] = _digest(document, "journal_digest")
        return document

    def permits_mutation(self, command: str, *, stage: str) -> bool:
        operation, resource, tokens = parse_command(command)
        return (
            operation == "mutate"
            and stage == "mitigation"
            and self.handoff_validated
            and self.precondition_read
            and resource == TARGET
            and self.mutation_count == 0
            and is_exact_delete(tokens)
        )

    def record(self, command: str, result: str, *, stage: str) -> bool:
        operation, resource, _ = parse_command(command)
        lower = result.lower()
        allowed = False
        if operation == "read":
            if resource == TARGET and not self.mutation_executed:
                self.precondition_read = "notfound" not in lower and "not found" not in lower
                allowed = True
            elif resource == TARGET and self.mutation_executed:
                self.target_reread = "notfound" in lower or "not found" in lower
                allowed = True
            elif resource == {
                "kind": "Endpoints",
                "namespace": "hotel-reservation",
                "name": "recommendation",
            }:
                # Endpoint readiness is valid only after the target has been
                # reread absent; a pre-mutation endpoint check cannot satisfy
                # the transaction postcondition.
                self.endpoint_ready = (
                    self.target_reread and endpoint_result_ready(result)
                    if self.strict_postconditions
                    else "<none>" not in lower and "not ready" not in lower
                )
                allowed = True
            else:
                allowed = True
        elif operation == "mutate":
            allowed = self.permits_mutation(command, stage=stage)
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
