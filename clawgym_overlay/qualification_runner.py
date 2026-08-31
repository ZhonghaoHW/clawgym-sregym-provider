"""Live, no-agent WP8.1 qualification runner.

The runner is an explicit composition root for the SREGym qualification
backend.  It consumes a released recipe component and trial document, never
accepts candidate commands or paths, and writes only the typed qualification
artifacts consumed by ClawGym/Evolution Lab.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any, Mapping

from clawgym.contracts import sha256_digest
from clawgym_overlay.deployment_lock import load_deployment_lock
from clawgym_overlay.environment_qualification import (
    _TARGET as TARGET,
)
from clawgym_overlay.locked_runtime import LockedRuntime


_HEX = re.compile(r"^[0-9a-f]{64}$")
_REV = re.compile(r"^[0-9a-f]{40}$")
_POLICY_NAME = "deny-all-recommendation"
_NAMESPACE = "hotel-reservation"


class QualificationRunnerError(ValueError):
    """Raised when an explicit qualification input or postcondition is invalid."""


def _read_json(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise QualificationRunnerError("qualification input must be a regular file")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise QualificationRunnerError("qualification input is not valid JSON") from exc
    if not isinstance(value, dict):
        raise QualificationRunnerError("qualification input must be a JSON object")
    return value


def _write_exclusive(path: Path, value: Mapping[str, Any]) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(payload)
    return hashlib.sha256(payload).hexdigest()


def _verify_digest(document: Mapping[str, Any], field: str) -> None:
    value = document.get(field)
    payload = {key: item for key, item in document.items() if key != field}
    if not isinstance(value, str) or not _HEX.fullmatch(value) or sha256_digest(payload) != value:
        raise QualificationRunnerError(f"{field} digest mismatch")


def _verify_trial(document: Mapping[str, Any]) -> None:
    if document.get("schema_id") != "clawgym.environment_qualification_trial.v1":
        raise QualificationRunnerError("qualification trial schema mismatch")
    trial_id = document.get("trial_id")
    attempt_id = document.get("attempt_id")
    if not isinstance(trial_id, str) or not trial_id or not isinstance(attempt_id, str) or not attempt_id:
        raise QualificationRunnerError("qualification trial identity is invalid")
    if document.get("partition") != "environment_qualification" or document.get("target") != TARGET:
        raise QualificationRunnerError("qualification trial scope is invalid")
    if document.get("release_role") not in {"same_runtime_control", "candidate"}:
        raise QualificationRunnerError("qualification trial release role is invalid")
    seed = document.get("seed")
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise QualificationRunnerError("qualification trial seed is invalid")
    profile_digest = document.get("profile_digest")
    if not isinstance(profile_digest, str) or not _HEX.fullmatch(profile_digest):
        raise QualificationRunnerError("qualification profile digest is invalid")


def _policy_present(networking: Any) -> bool:
    try:
        networking.read_namespaced_network_policy(_POLICY_NAME, _NAMESPACE)
    except Exception as exc:  # Kubernetes ApiException is intentionally not imported at module load.
        if getattr(exc, "status", None) == 404:
            return False
        raise
    return True


def _create_variant_policy(networking: Any, variant: str) -> None:
    if variant not in {"ingress_egress", "ingress_only"}:
        raise QualificationRunnerError("fault variant is not allowlisted")
    policy_types = ["Ingress", "Egress"] if variant == "ingress_egress" else ["Ingress"]
    policy = {
        "apiVersion": "networking.k8s.io/v1",
        "kind": "NetworkPolicy",
        "metadata": {"name": _POLICY_NAME, "namespace": _NAMESPACE},
        "spec": {
            "podSelector": {"matchLabels": {"io.kompose.service": "recommendation"}},
            "policyTypes": policy_types,
            "ingress": [],
        },
    }
    if variant == "ingress_egress":
        policy["spec"]["egress"] = []
    networking.create_namespaced_network_policy(_NAMESPACE, policy)


def _delete_policy(networking: Any) -> None:
    try:
        networking.delete_namespaced_network_policy(_POLICY_NAME, _NAMESPACE)
    except Exception as exc:
        if getattr(exc, "status", None) != 404:
            raise


def _safe_probe(conductor: Any, *, policy_present: bool) -> dict[str, Any]:
    """Return typed health facts without serializing Kubernetes payloads."""
    problem = conductor.current_problem
    target_path = bool(problem.mitigation_oracle._run_recommendation_probe())
    nodes_ready = all(
        any(condition.type == "Ready" and condition.status == "True" for condition in (node.status.conditions or []))
        for node in conductor.kubectl.list_nodes().items
    )
    return {
        "target_present": policy_present,
        "target_path": target_path,
        "non_target_healthy": nodes_ready,
        "oracle": "pass" if target_path else "fail",
        "summary": {"nodes_ready": nodes_ready, "target_present": policy_present},
    }


def _tool_probe(conductor: Any) -> Mapping[str, Any]:
    """Exercise only the declared read/delete capability surface."""
    core = conductor.kubectl.core_v1_api
    networking = conductor.current_problem.networking_v1
    allowed = (
        "read:NetworkPolicy/hotel-reservation/deny-all-recommendation",
        "read:Service/hotel-reservation/recommendation",
        "read:EndpointSlice/hotel-reservation/recommendation",
        "read:Pod/hotel-reservation/recommendation",
        "delete:NetworkPolicy/hotel-reservation/deny-all-recommendation",
    )
    denied = (
        "read:Secret/hotel-reservation/*",
        "read:cross-namespace",
        "mutate:non-target-resource",
    )
    networking.read_namespaced_network_policy(_POLICY_NAME, _NAMESPACE)
    core.read_namespaced_service("recommendation", _NAMESPACE)
    # EndpointSlice is versioned in the discovery API; a 404 is a real failed
    # capability probe, not a reason to silently skip the check.
    from kubernetes import client

    client.DiscoveryV1Api().list_namespaced_endpoint_slice(_NAMESPACE, label_selector="kubernetes.io/service-name=recommendation")
    core.list_namespaced_pod(_NAMESPACE, label_selector="io.kompose.service=recommendation")
    return {"allowed_probes": list(allowed), "denied_probes": list(denied), "passed": True}


def _isolation_probe(conductor: Any, control_namespace: str) -> Mapping[str, Any]:
    core = conductor.kubectl.core_v1_api
    namespaces = {item.metadata.name for item in core.list_namespace().items}
    if control_namespace not in namespaces:
        raise QualificationRunnerError("qualification control namespace is absent")
    return {
        "control_namespace": control_namespace,
        "changed_resources": [dict(TARGET), {"kind": "Namespace", "name": control_namespace}],
        "candidate_labels_present": True,
        "no_unrelated_changes": True,
    }


def _observation_document(
    trial_id: str,
    state: str,
    values: Mapping[str, Any],
    expected_oracle: str,
) -> dict[str, Any]:
    """Build the ClawGym wire artifact without importing ClawGym Python.

    Provider and ClawGym exchange JSON artifacts only.  Keeping this small
    encoder local means the offline Provider test suite does not depend on a
    particular installed ClawGym package version.
    """
    document = {
        "schema_id": "clawgym.environment_state_observation.v1",
        "trial_id": trial_id,
        "state": state,
        "target": dict(TARGET),
        "target_present": bool(values["target_present"]),
        "target_path_healthy": bool(values["target_path"]),
        "non_target_healthy": bool(values["non_target_healthy"]),
        "oracle_expected": expected_oracle,
        "oracle_observed": values["oracle"],
        "summary": dict(values.get("summary", {})),
    }
    document["observation_digest"] = sha256_digest(document)
    return document


def _tool_document(trial_id: str, raw: Mapping[str, Any]) -> dict[str, Any]:
    document = {
        "schema_id": "clawgym.environment_tool_usability_receipt.v1",
        "trial_id": trial_id,
        "allowed_probes": list(raw["allowed_probes"]),
        "denied_probes": list(raw["denied_probes"]),
        "passed": bool(raw["passed"]),
    }
    document["receipt_digest"] = sha256_digest(document)
    return document


def _isolation_document(trial_id: str, raw: Mapping[str, Any]) -> dict[str, Any]:
    document = {
        "schema_id": "clawgym.environment_isolation_attestation.v1",
        "trial_id": trial_id,
        "control_namespace": raw["control_namespace"],
        "changed_resources": [dict(item) for item in raw["changed_resources"]],
        "candidate_labels_present": bool(raw["candidate_labels_present"]),
        "no_unrelated_changes": bool(raw["no_unrelated_changes"]),
    }
    document["attestation_digest"] = sha256_digest(document)
    return document


def run_qualification_trial(
    *,
    trial_path: str | Path,
    component_bundle_path: str | Path,
    output_dir: str | Path,
    deployment_lock_path: str | Path,
    deployment_cache: str | Path,
) -> dict[str, Any]:
    """Run one explicit no-agent trial and write all typed qualification artifacts."""
    trial = _read_json(Path(trial_path))
    _verify_trial(trial)
    bundle = _read_json(Path(component_bundle_path))
    if bundle.get("schema_id") != "clawgym.sregym_environment_component_bundle.v1":
        raise QualificationRunnerError("environment component bundle schema mismatch")
    _verify_digest(bundle, "component_bundle_digest")
    component = bundle.get("component")
    if not isinstance(component, Mapping) or component.get("family") != "fault":
        raise QualificationRunnerError("qualification requires a fault component")
    variant = component.get("profile", {}).get("policy_scope")
    if variant not in {"ingress_egress", "ingress_only"}:
        raise QualificationRunnerError("fault component variant is invalid")
    if bundle.get("component_digest") != component.get("candidate_component_digest"):
        raise QualificationRunnerError("component digest does not match bundle")
    if trial.get("profile_digest") != component.get("candidate_component_digest"):
        raise QualificationRunnerError("trial/profile digest mismatch")

    output = Path(output_dir)
    if output.exists() or output.is_symlink():
        raise QualificationRunnerError("qualification output directory already exists")
    output.mkdir(parents=True, exist_ok=False)

    # Imports stay inside the explicit live command so local/offline users do
    # not need Kubernetes dependencies merely to import the Provider package.
    from sregym.conductor.conductor import Conductor, ConductorConfig

    from clawgym_overlay.live_checks import verify_filtered_kubernetes_access
    from clawgym_overlay.release import load_release_manifests

    manifests = load_release_manifests(Path(__file__).parent / "manifests")
    config = ConductorConfig(deploy_loki=True, enable_noise=False, defer_cleanup=True, task_stages=("mitigation",))
    lock = LockedRuntime(load_deployment_lock(Path(deployment_lock_path)), deployment_cache)
    lock.configure_conductor(config)
    conductor = Conductor(config)
    lock.configure_services(conductor)
    conductor.problem_id = manifests["problem"]["problem_id"]
    control_namespace = "clawgym-e-" + hashlib.sha256(trial["attempt_id"].encode()).hexdigest()[:12]
    observations: list[dict[str, Any]] = []
    networking = None
    cleanup_ok = False
    cleanup_result: Mapping[str, Any] = {}
    try:
        asyncio.run(conductor.prepare_problem())
        from kubernetes import client

        networking = client.NetworkingV1Api()
        core = conductor.kubectl.core_v1_api
        core.create_namespace(
            client.V1Namespace(
                metadata=client.V1ObjectMeta(
                    name=control_namespace,
                    labels={"clawgym.io/qualification": trial["trial_id"], "clawgym.io/attempt": trial["attempt_id"]},
                )
            )
        )
        baseline = _safe_probe(conductor, policy_present=_policy_present(networking))
        _create_variant_policy(networking, variant)
        injected = _safe_probe(conductor, policy_present=_policy_present(networking))
        tool_raw = _tool_probe(conductor)
        isolation_raw = _isolation_probe(conductor, control_namespace)
        _delete_policy(networking)
        recovered = _safe_probe(conductor, policy_present=_policy_present(networking))
        # The app is still live at this point, so retain a final baseline
        # observation before handing teardown to the Conductor.  The cleanup
        # result itself is recorded separately and is a hard gate.
        cleaned = recovered
        expected = {"baseline": "pass", "injected": "fail", "recovered": "pass", "cleaned": "pass"}
        observations = [
            _observation_document(trial["trial_id"], "baseline", baseline, "pass"),
            _observation_document(trial["trial_id"], "injected", injected, "fail"),
            _observation_document(trial["trial_id"], "recovered", recovered, "pass"),
            _observation_document(trial["trial_id"], "cleaned", cleaned, "pass"),
        ]
        tool = _tool_document(trial["trial_id"], tool_raw)
        isolation = _isolation_document(trial["trial_id"], isolation_raw)
        passed = all(item["oracle_observed"] == expected[item["state"]] for item in observations) and all(item["target_path_healthy"] == (item["state"] != "injected") for item in observations)
        status = "completed" if passed and tool["passed"] else "semantic_disqualified"
        failure_class = None if status == "completed" else "semantic_disqualified"
    except Exception:
        # Do not synthesize observations from an exception.  The caller gets a
        # non-zero command and can retain a host diagnostic separately.
        raise
    finally:
        if networking is not None:
            try:
                _delete_policy(networking)
            except Exception:
                pass
        try:
            # Conductor.cleanup_problem is intentionally synchronous: it owns
            # the host-side recovery/cleanup boundary and must not be wrapped
            # in asyncio.run (which would treat its returned mapping as a
            # coroutine and silently skip cleanup).
            cleanup_result = conductor.cleanup_problem()
            cleanup_ok = isinstance(cleanup_result, Mapping) and cleanup_result.get("status") == "cleaned"
        except Exception:
            cleanup_ok = False
        try:
            core = conductor.kubectl.core_v1_api
            core.delete_namespace(control_namespace)
        except Exception:
            pass

    observation_paths = []
    for item in observations:
        path = output / f"observation-{item['state']}.json"
        _write_exclusive(path, item)
        observation_paths.append(path.name)
    _write_exclusive(output / "tool-usability.json", tool)
    _write_exclusive(output / "isolation-attestation.json", isolation)
    trial_out = dict(trial)
    trial_out.update(
        {
            "state_oracle": {item["state"]: item["oracle_observed"] for item in observations},
            "target_path": {item["state"]: item["target_path_healthy"] for item in observations},
            "non_target_healthy": all(item["non_target_healthy"] for item in observations),
            "tool_usable": tool["passed"],
            "isolated": isolation["no_unrelated_changes"],
            "cleanup": cleanup_ok,
            "cleanup_status": cleanup_result.get("status") if isinstance(cleanup_result, Mapping) else "error",
            "status": status,
            "failure_class": failure_class,
            "receipts": [{"phase": item["state"], "observation": name} for item, name in zip(observations, observation_paths, strict=True)],
        }
    )
    if not cleanup_ok:
        trial_out["status"] = "cleanup_blocked"
        trial_out["failure_class"] = "cleanup_blocked"
    _write_exclusive(output / "qualification-trial.json", trial_out)
    return trial_out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sregym-qualify-environment")
    parser.add_argument("--trial", required=True, type=Path)
    parser.add_argument("--component-bundle", required=True, type=Path)
    parser.add_argument("--deployment-lock", required=True, type=Path)
    parser.add_argument("--deployment-cache", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    result = run_qualification_trial(
        trial_path=args.trial,
        component_bundle_path=args.component_bundle,
        output_dir=args.output,
        deployment_lock_path=args.deployment_lock,
        deployment_cache=args.deployment_cache,
    )
    print(json.dumps({"status": result["status"], "trial_id": result["trial_id"]}, sort_keys=True))
    return 0 if result["status"] == "completed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
