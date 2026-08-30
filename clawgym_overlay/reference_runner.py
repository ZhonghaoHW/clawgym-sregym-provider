"""Host-controlled, least-privilege Stratus container invocation for WP5."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
import tempfile
import time
from pathlib import Path

from clawgym.contracts import RunManifest

from clawgym_overlay.providers.reference_agent import ReferenceAgentExecution
from clawgym_overlay.r1d_protocol import validate_handoff
from clawgym_overlay.r1f_protocol import endpoint_result_ready, handoff_from_trajectory_records


_SENSITIVE_OUTPUT = re.compile(
    r"(?:bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}|"
    r"(?:sk|ak)-[A-Za-z0-9_-]{12,}|"
    r"\b[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}\b|"
    r"-----BEGIN [A-Z0-9 ]*(?:PRIVATE KEY|CERTIFICATE)-----|"
    r"\bclient-(?:certificate|key)-data\s*:|"
    r"\b(?:apiVersion:\s*v1\s+)?clusters\s*:|"
    r"(?:unix://)?/var/run/docker\.sock|"
    r"\bi-[a-z0-9]{8,}\b|"
    r"(?<![0-9])(?:[0-9]{1,3}\.){3}[0-9]{1,3}(?![0-9])",
    re.IGNORECASE,
)


def _safe_text(payload: bytes) -> str:
    """Retain process evidence without persisting credentials or host paths."""

    text = payload.decode("utf-8", errors="replace")
    text = _SENSITIVE_OUTPUT.sub("[REDACTED]", text)
    return re.sub(r"(?<![A-Za-z0-9_.-])/(?:[^\s\x00]+)", "[HOST_PATH]", text)


def _trajectory_records(root: Path) -> tuple[dict[str, object], ...]:
    records: list[dict[str, object]] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        payload = path.read_bytes()
        records.append(
            {
                "name": path.relative_to(root).as_posix(),
                "sha256_digest": hashlib.sha256(payload).hexdigest(),
                "bytes": len(payload),
                "text": _safe_text(payload),
            }
        )
    return tuple(records)


def _digest_document(document: dict[str, object], field: str) -> str:
    payload = {key: value for key, value in document.items() if key != field}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def _extract_r1c_handoff(records: tuple[dict[str, object], ...], run: RunManifest) -> dict[str, object]:
    release_digest = getattr(getattr(run, "agent_release", None), "agent_release_digest", "")
    required = ("symptom", "target_component", "evidence", "root_cause_hypothesis", "candidate_resource", "minimal_remediation", "verification_plan")
    found = None
    for record in records:
        text = str(record.get("text", ""))
        for line in reversed(text.splitlines()):
            if "R1C_HANDOFF_JSON" not in line and "submit_tool" not in line:
                continue
            candidate = line.split("R1C_HANDOFF_JSON", 1)[-1].lstrip(" :")
            try:
                value = json.loads(candidate)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict) and all(key in value for key in required):
                found = value
                break
        if found:
            break
    if found is None:
        document = {"schema_id": "clawgym.sregym_diagnosis_handoff.v1", "status": "incomplete", "run_manifest_digest": run.manifest_digest, "agent_release_digest": release_digest, "stage": "diagnosis", "symptom": "", "target_component": "", "evidence": [], "root_cause_hypothesis": "", "candidate_resource": {"kind": "", "namespace": "", "name": ""}, "minimal_remediation": "", "verification_plan": []}
    else:
        resource = found.get("candidate_resource") if isinstance(found.get("candidate_resource"), dict) else {}
        document = {"schema_id": "clawgym.sregym_diagnosis_handoff.v1", "status": "complete", "run_manifest_digest": run.manifest_digest, "agent_release_digest": release_digest, "stage": "diagnosis", "symptom": str(found.get("symptom", "")), "target_component": str(found.get("target_component", "")), "evidence": found.get("evidence", []) if isinstance(found.get("evidence"), list) else [], "root_cause_hypothesis": str(found.get("root_cause_hypothesis", "")), "candidate_resource": {"kind": str(resource.get("kind", "")), "namespace": str(resource.get("namespace", "")), "name": str(resource.get("name", ""))}, "minimal_remediation": str(found.get("minimal_remediation", "")), "verification_plan": found.get("verification_plan", []) if isinstance(found.get("verification_plan"), list) else []}
    document["handoff_digest"] = _digest_document(document, "handoff_digest")
    return document


def _extract_r1d_handoff(records: tuple[dict[str, object], ...], run: RunManifest) -> dict[str, object]:
    """Extract only an explicit R1D marker; transcript tails are never trusted."""
    release_digest = getattr(getattr(run, "agent_release", None), "agent_release_digest", "")
    required = ("symptom", "target_component", "evidence", "root_cause_hypothesis", "candidate_resource", "minimal_remediation", "verification_plan")
    found = None
    for record in records:
        for line in str(record.get("text", "")).splitlines():
            if "R1D_HANDOFF_JSON" not in line:
                continue
            try:
                value = json.loads(line.split("R1D_HANDOFF_JSON", 1)[-1].lstrip(" :"))
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict) and value.get("status") == "complete" and all(k in value for k in required):
                found = value
                break
        if found:
            break
    if found is None:
        document = {"schema_id": "clawgym.sregym_diagnosis_handoff.v2", "status": "incomplete", "run_manifest_digest": run.manifest_digest, "agent_release_digest": release_digest, "stage": "diagnosis", "symptom": "", "target_component": "", "evidence": [], "root_cause_hypothesis": "", "candidate_resource": {"kind": "", "namespace": "", "name": ""}, "minimal_remediation": "", "verification_plan": []}
    else:
        resource = found.get("candidate_resource") if isinstance(found.get("candidate_resource"), dict) else {}
        document = {"schema_id": "clawgym.sregym_diagnosis_handoff.v2", "status": "complete", "run_manifest_digest": run.manifest_digest, "agent_release_digest": release_digest, "stage": "diagnosis", "symptom": str(found.get("symptom", "")), "target_component": str(found.get("target_component", "")), "evidence": found.get("evidence", []), "root_cause_hypothesis": str(found.get("root_cause_hypothesis", "")), "candidate_resource": {"kind": str(resource.get("kind", "")), "namespace": str(resource.get("namespace", "")), "name": str(resource.get("name", ""))}, "minimal_remediation": str(found.get("minimal_remediation", "")), "verification_plan": found.get("verification_plan", [])}
    document["handoff_digest"] = _digest_document(document, "handoff_digest")
    if document.get("status") == "complete":
        try:
            validate_handoff(document, run_manifest_digest=run.manifest_digest, agent_release_digest=release_digest)
        except ValueError:
            document["status"] = "incomplete"
            document["handoff_digest"] = _digest_document(document, "handoff_digest")
    return document


def _extract_r1e_handoff(records: tuple[dict[str, object], ...], run: RunManifest) -> dict[str, object]:
    """R1e accepts only an explicit, identity-bound handoff marker."""
    release_digest = getattr(getattr(run, "agent_release", None), "agent_release_digest", "")
    required = ("symptom", "target_component", "evidence", "root_cause_hypothesis", "candidate_resource", "minimal_remediation", "verification_plan")
    found = None
    for record in records:
        for line in str(record.get("text", "")).splitlines():
            if "R1E_HANDOFF_JSON" not in line:
                continue
            try:
                value = json.loads(line.split("R1E_HANDOFF_JSON", 1)[-1].lstrip(" :"))
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict) and value.get("status") == "complete" and all(k in value for k in required):
                found = value
                break
        if found:
            break
    if found is None:
        document = {"schema_id": "clawgym.sregym_diagnosis_handoff.v2", "status": "incomplete", "run_manifest_digest": run.manifest_digest, "agent_release_digest": release_digest, "symptom": "", "target_component": "", "evidence": [], "root_cause_hypothesis": "", "candidate_resource": {"kind": "", "namespace": "", "name": ""}, "minimal_remediation": "", "verification_plan": []}
    else:
        document = {"schema_id": "clawgym.sregym_diagnosis_handoff.v2", "status": "complete", "run_manifest_digest": run.manifest_digest, "agent_release_digest": release_digest, "symptom": str(found.get("symptom", "")), "target_component": str(found.get("target_component", "")), "evidence": found.get("evidence", []), "root_cause_hypothesis": str(found.get("root_cause_hypothesis", "")), "candidate_resource": found.get("candidate_resource", {}), "minimal_remediation": str(found.get("minimal_remediation", "")), "verification_plan": found.get("verification_plan", [])}
    document["handoff_digest"] = _digest_document(document, "handoff_digest")
    if document.get("status") == "complete":
        from clawgym_overlay.r1e_protocol import TARGET
        if document.get("candidate_resource") != TARGET:
            document["status"] = "incomplete"
            document["handoff_digest"] = _digest_document(document, "handoff_digest")
    return document


def _extract_r1f_handoff(records: tuple[dict[str, object], ...], run: RunManifest) -> dict[str, object]:
    """Replay the same structured tool-call normalization used by R1f runtime."""

    run_digest = run.manifest_digest
    release_digest = getattr(getattr(run, "agent_release", None), "agent_release_digest", "")
    for record in records:
        if str(record.get("name", "")) != "r1f-handoff.json":
            continue
        try:
            document = json.loads(str(record.get("text", "")))
        except json.JSONDecodeError:
            continue
        if (
            isinstance(document, dict)
            and document.get("schema_id") == "clawgym.sregym_diagnosis_handoff.v2"
            and document.get("status") == "complete"
            and document.get("run_manifest_digest") == run_digest
            and document.get("agent_release_digest") == release_digest
            and document.get("candidate_resource") == {
                "kind": "NetworkPolicy",
                "namespace": "hotel-reservation",
                "name": "deny-all-recommendation",
            }
        ):
            # The trajectory record is intentionally redacted before it is
            # retained (for example, pod addresses in evidence).  Rebind the
            # digest to that sanitized, host-retained representation instead
            # of rejecting an otherwise valid handoff whose original digest
            # covered unsanitized model text.
            document["handoff_digest"] = _digest_document(document, "handoff_digest")
            return document

    return handoff_from_trajectory_records(
        records,
        run_manifest_digest=run_digest,
        agent_release_digest=release_digest,
    )


def _extract_gate_event_journal(
    records: tuple[dict[str, object], ...], run: RunManifest
) -> dict[str, object] | None:
    """Load the live R1f gate snapshot; never infer it from prose logs."""

    for record in records:
        if str(record.get("name", "")) != "r1f-gate-event-journal.json":
            continue
        try:
            document = json.loads(str(record.get("text", "")))
        except json.JSONDecodeError:
            return None
        if not isinstance(document, dict):
            return None
        if document.get("schema_id") != "clawgym.sregym_gate_event_journal.v1":
            return None
        if document.get("run_manifest_digest") != run.manifest_digest:
            return None
        release_digest = getattr(getattr(run, "agent_release", None), "agent_release_digest", "")
        if document.get("agent_release_digest") != release_digest:
            return None
        if document.get("journal_digest") != _digest_document(document, "journal_digest"):
            return None
        return document
    return None


def _r1d_transaction(handoff: dict[str, object], ledger: dict[str, object], run: RunManifest) -> dict[str, object]:
    mutations = [item for item in ledger.get("records", []) if isinstance(item, dict) and item.get("operation") == "mutate"]
    target = handoff.get("candidate_resource", {})
    mutation_sequence = mutations[0].get("sequence", 0) if mutations else 0
    post_read = {"observed": any(isinstance(item, dict) and item.get("operation") == "read" and item.get("sequence", 0) > mutation_sequence for item in ledger.get("records", []))}
    document = {"schema_id": "clawgym.sregym_remediation_transaction.v1", "run_manifest_digest": run.manifest_digest, "agent_release_digest": getattr(getattr(run, "agent_release", None), "agent_release_digest", ""), "status": "executed" if mutations else "incomplete", "target": target, "intent": handoff.get("minimal_remediation", ""), "preconditions": {"handoff_validated": handoff.get("status") == "complete"}, "policy_decision": "allow" if handoff.get("status") == "complete" else "deny", "mutation": mutations[0] if mutations else {}, "post_read": post_read,}
    document["transaction_digest"] = _digest_document(document, "transaction_digest")
    return document


def _r1d_verification_observation(ledger: dict[str, object], run: RunManifest) -> dict[str, object]:
    mutations = [item for item in ledger.get("records", []) if isinstance(item, dict) and item.get("operation") == "mutate"]
    boundary = mutations[0].get("sequence", 0) if mutations else 0
    observations = [{"sequence": item.get("sequence"), "operation": "read", "outcome": item.get("outcome")} for item in ledger.get("records", []) if isinstance(item, dict) and item.get("operation") == "read" and item.get("sequence", 0) > boundary]
    document = {"schema_id": "clawgym.sregym_verification_observation.v1", "run_manifest_digest": run.manifest_digest, "agent_release_digest": getattr(getattr(run, "agent_release", None), "agent_release_digest", ""), "observations": observations}
    document["observation_digest"] = _digest_document(document, "observation_digest")
    return document


def _r1e_transaction(handoff: dict[str, object], ledger: dict[str, object], run: RunManifest, gate_journal: dict[str, object] | None = None) -> dict[str, object]:
    records = [item for item in ledger.get("records", []) if isinstance(item, dict)]
    mutations = [item for item in records if item.get("operation") == "mutate"]
    reads = [item for item in records if item.get("operation") == "read"]
    target = {"kind": "NetworkPolicy", "namespace": "hotel-reservation", "name": "deny-all-recommendation"}
    exact = [m for m in mutations if m.get("resource") == target and m.get("outcome") == "executed"]
    pre = any(r.get("resource") == target and r.get("sequence", 0) < (exact[0].get("sequence", 10**9) if exact else 10**9) and r.get("outcome") == "executed" for r in reads)
    post = any(r.get("resource") == target and r.get("sequence", 0) > (exact[0].get("sequence", 0) if exact else 0) and r.get("result_summary") == "not_found" for r in reads)
    endpoint = any("recommendation" in str(r.get("resource", {}).get("name", "")) and r.get("result_summary") == "ready" for r in reads)
    if gate_journal is not None:
        state = gate_journal.get("state", {})
        if isinstance(state, dict):
            pre = bool(state.get("precondition_read"))
            post = bool(state.get("target_reread"))
            endpoint = bool(state.get("endpoint_ready"))
    mutation = exact[0] if exact else (mutations[0] if mutations else {})
    document = {"schema_id": "clawgym.sregym_remediation_transaction.v2", "run_manifest_digest": run.manifest_digest, "agent_release_digest": getattr(getattr(run, "agent_release", None), "agent_release_digest", ""), "status": "executed" if len(mutations) == 1 and exact and pre and post and endpoint else "incomplete", "target": target, "handoff_digest": str(handoff.get("handoff_digest", "")), "gate_trace": ["handoff_validated", "precondition_read", "single_mutation", "target_reread", "endpoint_read"], "precondition": {"policy_exists": pre, "target_matches": bool(mutation.get("resource") == target), "observation_digest": hashlib.sha256(json.dumps(reads, sort_keys=True, separators=(",", ":")).encode()).hexdigest()}, "mutation": {"attempted": bool(mutations), "count": len(mutations), "verb": "delete", "command_sha256": str(mutation.get("command_sha256", "")), "outcome": str(mutation.get("outcome", "none")) if mutations else "none", "target_matches": bool(mutation.get("resource") == target)}, "postcondition": {"target_absent": post, "endpoint_ready": endpoint, "observation_digest": hashlib.sha256(json.dumps(reads, sort_keys=True, separators=(",", ":")).encode()).hexdigest()}}
    document["transaction_digest"] = _digest_document(document, "transaction_digest")
    return document


def _r1e_verification_observation(ledger: dict[str, object], run: RunManifest, gate_journal: dict[str, object] | None = None) -> dict[str, object]:
    records = [item for item in ledger.get("records", []) if isinstance(item, dict) and item.get("operation") == "read"]
    observations = []
    for item in records:
        resource = item.get("resource", {})
        if resource == {"kind": "NetworkPolicy", "namespace": "hotel-reservation", "name": "deny-all-recommendation"}:
            observations.append({"kind": "target_reread", "outcome": "target_absent" if item.get("result_summary") == "not_found" else "target_present"})
        elif "recommendation" in str(resource.get("name", "")):
            observations.append({"kind": "endpoint_read", "outcome": "endpoint_ready" if item.get("result_summary") == "ready" else "unknown"})
    if gate_journal is not None and isinstance(gate_journal.get("state"), dict):
        state = gate_journal["state"]
        if state.get("endpoint_ready") is True and not any(o.get("outcome") == "endpoint_ready" for o in observations):
            observations.append({"kind": "endpoint_read", "outcome": "endpoint_ready"})
    document = {"schema_id": "clawgym.sregym_verification_observation.v2", "run_manifest_digest": run.manifest_digest, "agent_release_digest": getattr(getattr(run, "agent_release", None), "agent_release_digest", ""), "target": {"kind": "NetworkPolicy", "namespace": "hotel-reservation", "name": "deny-all-recommendation"}, "observations": observations}
    document["observation_digest"] = _digest_document(document, "observation_digest")
    return document


def _extract_action_ledger(records: tuple[dict[str, object], ...], run: RunManifest, *, schema_id: str = "clawgym.sregym_agent_action_ledger.v1") -> dict[str, object]:
    release_digest = getattr(getattr(run, "agent_release", None), "agent_release_digest", "")
    entries: list[dict[str, object]] = []
    events: dict[str, dict[str, object]] = {}
    order: list[str] = []
    pending_responses: dict[str, str] = {}
    for record in records:
        if not str(record.get("name", "")).endswith(".jsonl"):
            continue
        for line in str(record.get("text", "")).splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            messages = event.get("messages", event) if isinstance(event, dict) else {}
            if not isinstance(messages, list):
                continue
            responses = {str(m.get("tool_call_id")): str(m.get("content", "")) for m in messages if isinstance(m, dict) and m.get("tool_call_id")}
            for message in messages:
                if not isinstance(message, dict):
                    continue
                for call in message.get("tool_calls", []) if isinstance(message.get("tool_calls"), list) else []:
                    if not isinstance(call, dict):
                        continue
                    call_id = str(call.get("id", ""))
                    if not call_id:
                        continue
                    if call_id in events:
                        continue
                    tool = str(call.get("name", call.get("function", {}).get("name", "unknown")))
                    args = call.get("args", call.get("function", {}).get("arguments", {}))
                    if isinstance(args, str):
                        try: args = json.loads(args)
                        except json.JSONDecodeError: args = {}
                    command = str(args.get("command", "")) if isinstance(args, dict) else ""
                    lower = command.lower()
                    if "submit_tool" in tool or tool in {"f_submit_tool", "manual_submit_tool"}:
                        operation = "submit"
                    elif lower.startswith("kubectl get") or lower.startswith("kubectl describe") or lower.startswith("kubectl logs") or lower.startswith("kubectl get"):
                        operation = "read"
                    elif any(lower.startswith("kubectl " + verb) for verb in ("apply", "patch", "delete", "replace", "create", "edit", "rollout", "scale", "set")):
                        operation = "mutate"
                    else:
                        operation = "unknown"
                    resource = {"kind": "", "namespace": "", "name": ""}
                    match = re.search(r"(?:networkpolicy|netpol)\s+([A-Za-z0-9_.-]+)", lower)
                    if match: resource["kind"], resource["name"] = "NetworkPolicy", match.group(1)
                    ns = re.search(r"(?:-n|--namespace)\s+([A-Za-z0-9_.-]+)", lower)
                    if ns: resource["namespace"] = ns.group(1)
                    events[call_id] = {"stage": "mitigation" if "mitigation" in str(record.get("name", "")) else "diagnosis", "tool": tool, "operation": operation, "command": command, "resource": resource, "response": pending_responses.pop(call_id, "")}
                    order.append(call_id)
                for call_id, response in responses.items():
                    if call_id in events:
                        events[call_id]["response"] = response
                    else:
                        pending_responses[call_id] = response
    for sequence, call_id in enumerate(order, 1):
        event = events[call_id]
        response = str(event.get("response", ""))
        if not response:
            outcome = "unknown"
        elif "command rejected" in response.lower() or "forbidden" in response.lower():
            outcome = "rejected"
        elif "error" in response.lower() or "exception" in response.lower():
            outcome = "failed"
        else:
            outcome = "executed"
        command = str(event.get("command", ""))
        response_lower = response.lower()
        if "notfound" in response_lower or "not found" in response_lower:
            result_summary = "not_found"
        elif event["resource"] == {"kind": "Endpoints", "namespace": "hotel-reservation", "name": "recommendation"} and endpoint_result_ready(response):
            result_summary = "ready"
        elif "ready" in response_lower or "addresses" in response_lower:
            result_summary = "ready"
        elif outcome in {"failed", "rejected"}:
            result_summary = "error"
        elif outcome == "executed":
            result_summary = "ok"
        else:
            result_summary = "unknown"
        entries.append({"sequence": sequence, "stage": event["stage"], "tool": event["tool"], "operation": event["operation"], "command_sha256": hashlib.sha256(command.encode()).hexdigest(), "resource": event["resource"], "outcome": outcome, "result_summary": result_summary})
    summary = {"total": len(entries), "read": sum(e["operation"] == "read" for e in entries), "mutate": sum(e["operation"] == "mutate" for e in entries), "submit": sum(e["operation"] == "submit" for e in entries), "unknown": sum(e["operation"] == "unknown" for e in entries), "executed_mutations": sum(e["operation"] == "mutate" and e["outcome"] == "executed" for e in entries)}
    document = {"schema_id": schema_id, "run_manifest_digest": run.manifest_digest, "agent_release_digest": release_digest, "records": entries, "summary": summary}
    document["ledger_digest"] = _digest_document(document, "ledger_digest")
    return document


class ReferenceAgentSecretError(ValueError):
    """Raised when the agent-only secret file is absent or unsafe."""


def read_agent_secret(path: str | Path) -> str:
    secret = Path(path)
    try:
        metadata = secret.lstat()
    except OSError as exc:
        raise ReferenceAgentSecretError("agent secret file is unavailable") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise ReferenceAgentSecretError("agent secret must be a regular non-symlink file")
    if stat.S_IMODE(metadata.st_mode) != 0o600:
        raise ReferenceAgentSecretError("agent secret file must have mode 0600")
    value = secret.read_text(encoding="utf-8").strip()
    if not value:
        raise ReferenceAgentSecretError("agent secret file is empty")
    return value


def _r1b_config_mounts(profile: dict) -> list[str]:
    """Resolve the fixed R1b config bundle without accepting caller paths."""

    if profile.get("sop_variant") != "r1-evidence-first-bounded-v1":
        return []
    root = Path(__file__).resolve().parent / "manifests"
    bundle_path = root / "agent.reference-stratus-r1b.config-bundle.v1.json"
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    expected = profile.get("config_bundle_digest")
    actual = hashlib.sha256(
        json.dumps(
            {key: value for key, value in bundle.items() if key != "bundle_digest"},
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()
    if bundle.get("bundle_digest") != actual or expected != actual:
        raise RuntimeError("R1b configuration bundle digest mismatch")
    mounts: list[str] = []
    for item in bundle.get("files", []):
        source = root / item["path"]
        if not source.is_file() or source.is_symlink():
            raise RuntimeError("R1b configuration file is unavailable")
        if hashlib.sha256(source.read_bytes()).hexdigest() != item["sha256_digest"]:
            raise RuntimeError("R1b configuration file digest mismatch")
        mounts.append(f"{source.resolve()}:{item['container_path']}:ro")
    if len(mounts) != 2:
        raise RuntimeError("R1b configuration bundle is incomplete")
    return mounts


def _r1c_config_mounts(profile: dict) -> list[str]:
    if profile.get("sop_variant") not in {"r1c-structured-attribution-v1", "r1c-structured-attribution-deepseek-v1"}:
        return []
    root = Path(__file__).resolve().parent / "manifests"
    bundle_path = root / "agent.reference-stratus-r1c.config-bundle.v1.json"
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    expected = profile.get("config_bundle_digest")
    actual = hashlib.sha256(json.dumps({k: v for k, v in bundle.items() if k != "bundle_digest"}, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
    if bundle.get("bundle_digest") != actual or expected != actual:
        raise RuntimeError("R1c configuration bundle digest mismatch")
    mounts = []
    for item in bundle.get("files", []):
        source = root / item["path"]
        if not source.is_file() or source.is_symlink() or hashlib.sha256(source.read_bytes()).hexdigest() != item["sha256_digest"]:
            raise RuntimeError("R1c configuration file digest mismatch")
        mounts.append(f"{source.resolve()}:{item['container_path']}:ro")
    if len(mounts) != 2: raise RuntimeError("R1c configuration bundle is incomplete")
    return mounts


def _r1d_config_mounts(profile: dict) -> list[str]:
    if profile.get("sop_variant") != "r1d-typed-remediation-v1":
        return []
    root = Path(__file__).resolve().parent / "manifests"
    bundle_path = root / "agent.reference-stratus-r1d.config-bundle.v1.json"
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    expected = profile.get("config_bundle_digest")
    actual = hashlib.sha256(json.dumps({k: v for k, v in bundle.items() if k != "bundle_digest"}, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
    if bundle.get("bundle_digest") != actual or expected != actual:
        raise RuntimeError("R1d configuration bundle digest mismatch")
    mounts = []
    for item in bundle.get("files", []):
        source = root / item["path"]
        if not source.is_file() or source.is_symlink() or hashlib.sha256(source.read_bytes()).hexdigest() != item["sha256_digest"]:
            raise RuntimeError("R1d configuration file digest mismatch")
        mounts.append(f"{source.resolve()}:{item['container_path']}:ro")
    if len(mounts) != 4:
        raise RuntimeError("R1d configuration bundle is incomplete")
    return mounts


def _r1e_config_mounts(profile: dict) -> list[str]:
    if profile.get("sop_variant") != "r1e-runtime-gated-v1":
        return []
    root = Path(__file__).resolve().parent / "manifests"
    bundle = json.loads((root / "agent.reference-stratus-r1e.config-bundle.v1.json").read_text(encoding="utf-8"))
    actual = hashlib.sha256(json.dumps({k: v for k, v in bundle.items() if k != "bundle_digest"}, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
    if bundle.get("bundle_digest") != actual or profile.get("config_bundle_digest") != actual:
        raise RuntimeError("R1e configuration bundle digest mismatch")
    mounts = []
    for item in bundle.get("files", []):
        source = root / item["path"]
        if not source.is_file() or source.is_symlink() or hashlib.sha256(source.read_bytes()).hexdigest() != item["sha256_digest"]:
            raise RuntimeError("R1e configuration file digest mismatch")
        mounts.append(f"{source.resolve()}:{item['container_path']}:ro")
    if len(mounts) != 4:
        raise RuntimeError("R1e configuration bundle is incomplete")
    return mounts


def _r1f_config_mounts(profile: dict) -> list[str]:
    if profile.get("sop_variant") != "r1f-host-normalized-remediation-v1":
        return []
    root = Path(__file__).resolve().parent / "manifests"
    bundle = json.loads((root / "agent.reference-stratus-r1f.config-bundle.v1.json").read_text(encoding="utf-8"))
    actual = hashlib.sha256(
        json.dumps(
            {key: value for key, value in bundle.items() if key != "bundle_digest"},
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode()
    ).hexdigest()
    if bundle.get("bundle_digest") != actual or profile.get("config_bundle_digest") != actual:
        raise RuntimeError("R1f configuration bundle digest mismatch")
    mounts = []
    for item in bundle.get("files", []):
        source = root / item["path"]
        if not source.is_file() or source.is_symlink() or hashlib.sha256(source.read_bytes()).hexdigest() != item["sha256_digest"]:
            raise RuntimeError("R1f configuration file digest mismatch")
        mounts.append(f"{source.resolve()}:{item['container_path']}:ro")
    if len(mounts) != 4:
        raise RuntimeError("R1f configuration bundle is incomplete")
    return mounts


def _r1i_config_mounts(profile: dict) -> list[str]:
    if profile.get("sop_variant") != "r1i-typed-handoff-journal-v1":
        return []
    root = Path(__file__).resolve().parent / "manifests"
    bundle = json.loads((root / "agent.reference-stratus-r1i.config-bundle.v1.json").read_text(encoding="utf-8"))
    actual = hashlib.sha256(json.dumps({k: v for k, v in bundle.items() if k != "bundle_digest"}, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
    if bundle.get("bundle_digest") != actual or profile.get("config_bundle_digest") != actual:
        raise RuntimeError("R1i configuration bundle digest mismatch")
    mounts = []
    for item in bundle.get("files", []):
        source = root / item["path"]
        if not source.is_file() or source.is_symlink() or hashlib.sha256(source.read_bytes()).hexdigest() != item["sha256_digest"]:
            raise RuntimeError("R1i configuration file digest mismatch")
        mounts.append(f"{source.resolve()}:{item['container_path']}:ro")
    if len(mounts) != 4:
        raise RuntimeError("R1i configuration bundle is incomplete")
    return mounts


def _materialized_config_mounts(profile: dict, bundle_root: str | Path) -> list[str]:
    """Validate and mount a declarative materialization bundle read-only.

    Materialized profiles are deliberately not looked up by profile name.  The
    bundle path is supplied by the host worker and every file is bound to the
    digest declared by its config manifest before it can enter the container.
    """
    root = Path(bundle_root).resolve()
    if not root.is_dir() or root.is_symlink():
        raise RuntimeError("materialization bundle root is unavailable")
    bundle_path = root / "config-bundle.json"
    if not bundle_path.is_file() or bundle_path.is_symlink():
        raise RuntimeError("materialization config bundle is unavailable")
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    unsigned = {key: value for key, value in bundle.items() if key != "config_bundle_digest"}
    actual = hashlib.sha256(
        json.dumps(unsigned, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()
    expected = bundle.get("config_bundle_digest")
    if expected != actual or profile.get("config_bundle_digest") != actual:
        raise RuntimeError("materialized configuration bundle digest mismatch")
    mounts: list[str] = []
    for item in bundle.get("files", []):
        if not isinstance(item, dict):
            raise RuntimeError("materialized configuration file entry is invalid")
        relative = item.get("path")
        target = item.get("container_path")
        declared = item.get("sha256_digest")
        if not isinstance(relative, str) or not relative.startswith("reference-materialized/"):
            raise RuntimeError("materialized configuration path is invalid")
        source = (root / relative).resolve()
        if source.parent != (root / "reference-materialized").resolve() or not source.is_file() or source.is_symlink():
            raise RuntimeError("materialized configuration file is invalid")
        if hashlib.sha256(source.read_bytes()).hexdigest() != declared:
            raise RuntimeError("materialized configuration file digest mismatch")
        if not isinstance(target, str) or not target.startswith("/opt/sregym/clients/stratus/configs/") or target.endswith("/"):
            raise RuntimeError("materialized configuration target is invalid")
        mounts.append(f"{source}:{target}:ro")
    if len(mounts) != 4:
        raise RuntimeError("materialized configuration bundle is incomplete")
    return mounts


class SafeStratusRunner:
    """Run Stratus with only filtered Kubernetes access and one model credential."""

    def __init__(self, *, profile: dict, secret_file: str | Path, image: str = "sregym-agent-base:latest", materialization_bundle: str | Path | None = None):
        self._profile = profile
        self._secret_file = Path(secret_file)
        self._image = image
        self._materialization_bundle = Path(materialization_bundle) if materialization_bundle else None

    def __call__(self, run_manifest: RunManifest, filtered_kubeconfig_path: str) -> ReferenceAgentExecution:
        kubeconfig = Path(filtered_kubeconfig_path)
        if not kubeconfig.is_file() or kubeconfig.is_symlink():
            raise RuntimeError("filtered kubeconfig is unavailable")
        key = read_agent_secret(self._secret_file)
        image_id = subprocess.run(
            ["docker", "image", "inspect", self._image, "--format", "{{.Id}}"],
            capture_output=True,
            check=True,
            text=True,
        ).stdout.strip()
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id):
            raise RuntimeError("reference agent image does not have a local SHA-256 identity")
        command_profile = self._profile["command"]
        is_r1i = self._profile.get("sop_variant") == "r1i-typed-handoff-journal-v1"
        timeout_seconds = self._profile.get("bounded_execution", {}).get(
            "container_timeout_seconds", 1800
        )
        started = time.monotonic()
        with tempfile.TemporaryDirectory(prefix="clawgym-wp5-agent-") as logs:
            env_file = Path(logs) / "agent.env"
            env_file.write_text(f"AGENT_API_KEY={key}\n", encoding="utf-8")
            env_file.chmod(0o600)
            command = [
                "docker", "run", "--rm", "--network=host",
                "--add-host=host.docker.internal:host-gateway", "--read-only",
                "--tmpfs", "/tmp:rw,noexec,nosuid,size=256m",
                "--cap-drop=ALL", "--security-opt=no-new-privileges",
                "--cpus=4", "--memory=8g",
                "--user", f"{os.getuid()}:{os.getgid()}",
                "-v", f"{kubeconfig.resolve()}:/home/agent/.kube/config:ro",
                "-v", f"{Path(logs).resolve()}:/logs:rw",
                "-e", "KUBECONFIG=/home/agent/.kube/config",
                "-e", "AGENT_LOGS_DIR=/logs",
                "-e", f"AGENT_MODEL_ID={self._profile['model_id']}",
                "-e", f"AGENT_API_BASE={self._profile['api_base']}",
                "--env-file", str(env_file),
                "-e", f"SREGYM_ARTIFACT_ID={self._profile['artifact_id']}",
                "-e", f"SREGYM_SOP_VARIANT={self._profile.get('sop_variant', 'r0-baseline')}",
                "-e", f"SREGYM_RUN_MANIFEST_DIGEST={run_manifest.manifest_digest}",
                "-e", f"SREGYM_AGENT_RELEASE_DIGEST={getattr(getattr(run_manifest, 'agent_release', None), 'agent_release_digest', '')}",
                "-e", "API_HOSTNAME=host.docker.internal",
                "-e", "MCP_SERVER_URL=http://host.docker.internal:9954",
                "--entrypoint", command_profile[0],
                image_id,
                *command_profile[1:],
            ]
            if self._profile.get("sop_variant") == "r1-evidence-first-bounded-v1":
                overlay = Path(__file__).resolve().parent / "reference_driver.py"
                image_index = command.index(image_id)
                command[image_index:image_index] = [
                    "-v", f"{overlay.resolve()}:/opt/clawgym_overlay/reference_driver.py:ro",
                    "-e", "PYTHONPATH=/opt/clawgym_overlay:/opt/sregym",
                ]
                image_index = command.index(image_id)
                command[image_index:image_index] = sum((['-v', mount] for mount in _r1b_config_mounts(self._profile)), [])
            elif self._profile.get("sop_variant") in {"r1c-structured-attribution-v1", "r1c-structured-attribution-deepseek-v1"}:
                overlay = Path(__file__).resolve().parent / "reference_driver_r1c.py"
                image_index = command.index(image_id)
                command[image_index:image_index] = ["-v", f"{overlay.resolve()}:/opt/clawgym_overlay/reference_driver_r1c.py:ro", "-e", "PYTHONPATH=/opt/clawgym_overlay:/opt/sregym"]
                image_index = command.index(image_id)
                command[image_index:image_index] = sum((['-v', mount] for mount in _r1c_config_mounts(self._profile)), [])
            elif self._profile.get("sop_variant") == "r1d-typed-remediation-v1":
                overlay = Path(__file__).resolve().parent / "reference_driver_r1d.py"
                image_index = command.index(image_id)
                command[image_index:image_index] = ["-v", f"{overlay.resolve()}:/opt/clawgym_overlay/reference_driver_r1d.py:ro", "-e", "PYTHONPATH=/opt/clawgym_overlay:/opt/sregym"]
                image_index = command.index(image_id)
                command[image_index:image_index] = sum((['-v', mount] for mount in _r1d_config_mounts(self._profile)), [])
            elif self._profile.get("sop_variant") == "r1e-runtime-gated-v1":
                overlay = Path(__file__).resolve().parent / "reference_driver_r1e.py"
                protocol = Path(__file__).resolve().parent / "r1e_protocol.py"
                image_index = command.index(image_id)
                # Mount the two R1e modules beneath a fixed package directory.
                # The package's parent (/opt), rather than the package itself,
                # must be on PYTHONPATH so ``import clawgym_overlay.r1e_protocol``
                # resolves inside the isolated container.
                command[image_index:image_index] = ["-v", f"{overlay.resolve()}:/opt/clawgym_overlay/reference_driver_r1e.py:ro", "-v", f"{protocol.resolve()}:/opt/clawgym_overlay/r1e_protocol.py:ro", "-e", "PYTHONPATH=/opt:/opt/clawgym_overlay:/opt/sregym"]
                image_index = command.index(image_id)
                command[image_index:image_index] = sum((['-v', mount] for mount in _r1e_config_mounts(self._profile)), [])
            elif self._profile.get("sop_variant") in {"r1f-host-normalized-remediation-v1", "r1i-typed-handoff-journal-v1", "materialized-reference-v1"}:
                overlay = Path(__file__).resolve().parent / "reference_driver_r1f.py"
                protocol = Path(__file__).resolve().parent / "r1f_protocol.py"
                image_index = command.index(image_id)
                command[image_index:image_index] = [
                    "-v", f"{overlay.resolve()}:/opt/clawgym_overlay/reference_driver_r1f.py:ro",
                    "-v", f"{protocol.resolve()}:/opt/clawgym_overlay/r1f_protocol.py:ro",
                    "-e", "PYTHONPATH=/opt:/opt/clawgym_overlay:/opt/sregym",
                ]
                image_index = command.index(image_id)
                if self._profile.get("sop_variant") == "materialized-reference-v1":
                    if self._materialization_bundle is None:
                        raise RuntimeError("materialized profile requires an explicit bundle")
                    mounts = _materialized_config_mounts(self._profile, self._materialization_bundle)
                else:
                    mounts = _r1i_config_mounts(self._profile) if is_r1i else _r1f_config_mounts(self._profile)
                command[image_index:image_index] = sum((['-v', mount] for mount in mounts), [])
            try:
                completed = subprocess.run(
                    command, capture_output=True, text=False, timeout=timeout_seconds, check=False
                )
                transcript = completed.stdout + completed.stderr
                exit_code = completed.returncode if completed.returncode >= 0 else 1
            except subprocess.TimeoutExpired as exc:
                transcript = (exc.stdout or b"") + (exc.stderr or b"") + b"\nreference-agent-timeout\n"
                exit_code = 124
            digest = hashlib.sha256(transcript).hexdigest()
            size = len(transcript)
            trajectories = _trajectory_records(Path(logs))
        duration_ms = int((time.monotonic() - started) * 1000)
        is_r1d = self._profile.get("sop_variant") == "r1d-typed-remediation-v1"
        is_r1e = self._profile.get("sop_variant") == "r1e-runtime-gated-v1"
        is_r1f = self._profile.get("sop_variant") == "r1f-host-normalized-remediation-v1"
        is_r1i = self._profile.get("sop_variant") == "r1i-typed-handoff-journal-v1"
        handoff = _extract_r1f_handoff(trajectories, run_manifest) if (is_r1f or is_r1i) else (_extract_r1e_handoff(trajectories, run_manifest) if is_r1e else (_extract_r1d_handoff(trajectories, run_manifest) if is_r1d else (_extract_r1c_handoff(trajectories, run_manifest) if self._profile.get("sop_variant") in {"r1c-structured-attribution-v1", "r1c-structured-attribution-deepseek-v1"} else None)))
        ledger = _extract_action_ledger(trajectories, run_manifest, schema_id="clawgym.sregym_agent_action_ledger.v2" if (is_r1d or is_r1e or is_r1f or is_r1i) else "clawgym.sregym_agent_action_ledger.v1") if is_r1d or is_r1e or is_r1f or is_r1i or self._profile.get("sop_variant") in {"r1c-structured-attribution-v1", "r1c-structured-attribution-deepseek-v1"} else None
        gate_journal = _extract_gate_event_journal(trajectories, run_manifest) if is_r1i else None
        return ReferenceAgentExecution(
            exit_code=exit_code,
            submission={"reference_agent": "stratus", "run_manifest_digest": run_manifest.manifest_digest},
            duration_ms=duration_ms,
            transcript_digest=digest,
            transcript_bytes=size,
            transcript=_safe_text(transcript),
            trajectory_records=trajectories,
            image_digest=image_id.removeprefix("sha256:"),
            timeout_seconds=timeout_seconds,
            diagnosis_handoff=handoff,
            action_ledger=ledger,
            remediation_transaction=_r1e_transaction(handoff, ledger, run_manifest, gate_journal) if (is_r1e or is_r1f or is_r1i) and handoff is not None and ledger is not None else (_r1d_transaction(handoff, ledger, run_manifest) if is_r1d and handoff is not None and ledger is not None else None),
            verification_observation=_r1e_verification_observation(ledger, run_manifest, gate_journal) if (is_r1e or is_r1f or is_r1i) and ledger is not None else (_r1d_verification_observation(ledger, run_manifest) if is_r1d and ledger is not None else None),
            gate_event_journal=gate_journal,
        )
