from __future__ import annotations

import json
import runpy
import sys
import types

import pytest
from clawgym.contracts import sha256_digest

import clawgym_overlay.qualification_runner as qualification_runner
from clawgym_overlay.qualification_runner import (
    QualificationRunnerError,
    _create_variant_policy,
    _delete_policy,
    _isolation_document,
    _isolation_probe,
    _observation_document,
    _policy_present,
    _read_json,
    _safe_probe,
    _tool_document,
    _tool_probe,
    _verify_digest,
    _verify_trial,
    _write_exclusive,
    run_qualification_trial,
)


class _Networking:
    def __init__(self):
        self.created = []

    def create_namespaced_network_policy(self, namespace, body):
        self.created.append((namespace, body))


def _trial(**overrides):
    value = {
        "schema_id": "clawgym.environment_qualification_trial.v1",
        "trial_id": "candidate-01",
        "attempt_id": "attempt-1",
        "partition": "environment_qualification",
        "target": {"kind": "NetworkPolicy", "namespace": "hotel-reservation", "name": "deny-all-recommendation"},
        "release_role": "candidate",
        "seed": 2026090111,
        "profile_digest": "a" * 64,
    }
    value.update(overrides)
    return value


def test_variant_policy_is_closed_and_deterministic():
    networking = _Networking()
    _create_variant_policy(networking, "ingress_only")
    assert networking.created[0][0] == "hotel-reservation"
    assert networking.created[0][1]["spec"]["policyTypes"] == ["Ingress"]
    assert "egress" not in networking.created[0][1]["spec"]

    networking = _Networking()
    _create_variant_policy(networking, "ingress_egress")
    assert networking.created[0][1]["spec"]["policyTypes"] == ["Ingress", "Egress"]
    assert networking.created[0][1]["spec"]["egress"] == []


@pytest.mark.parametrize(
    "change",
    [
        {"target": {"kind": "Service"}},
        {"partition": "validation"},
        {"profile_digest": "not-a-digest"},
        {"seed": True},
    ],
)
def test_trial_scope_is_fail_closed(change):
    with pytest.raises(QualificationRunnerError):
        _verify_trial(_trial(**change))


def test_unknown_fault_variant_is_rejected():
    with pytest.raises(QualificationRunnerError):
        _create_variant_policy(_Networking(), "delete-everything")


def test_delete_policy_is_idempotent_but_surfaces_unexpected_api_errors():
    class _DeleteOnly:
        def __init__(self, status):
            self.status = status

        def delete_namespaced_network_policy(self, name, namespace):
            error = RuntimeError("api failure")
            error.status = self.status
            raise error

    # A missing policy is already clean and must not fail cleanup.
    _delete_policy(_DeleteOnly(404))
    with pytest.raises(RuntimeError, match="api failure"):
        _delete_policy(_DeleteOnly(500))


def test_safe_probe_reports_unready_negative_control_and_missing_conditions():
    conductor = _FakeConductor({}, cleanup_status="cleaned")
    conductor.kubectl.list_nodes = lambda: types.SimpleNamespace(
        items=[types.SimpleNamespace(status=types.SimpleNamespace(conditions=[]))]
    )
    probe = _safe_probe(conductor, policy_present=False)
    assert probe["target_path"] is True
    assert probe["non_target_healthy"] is False


def test_safe_probe_rejects_empty_node_observation():
    conductor = _FakeConductor({}, cleanup_status="cleaned")
    conductor.kubectl.list_nodes = lambda: types.SimpleNamespace(items=[])
    probe = _safe_probe(conductor, policy_present=False)
    assert probe["non_target_healthy"] is False


def test_safe_probe_marks_injected_policy_unhealthy_and_oracle_fail():
    conductor = _FakeConductor({}, cleanup_status="cleaned")
    conductor.current_problem.networking_v1.present = True
    probe = _safe_probe(conductor, policy_present=True)
    assert probe["target_present"] is True
    assert probe["target_path"] is False
    assert probe["oracle"] == "fail"
    assert probe["non_target_healthy"] is True


def test_trial_verifier_rejects_each_identity_and_scope_dimension():
    for change in (
        {"schema_id": "wrong"},
        {"trial_id": ""},
        {"attempt_id": ""},
        {"release_role": "other"},
        {"seed": 1.5},
    ):
        with pytest.raises(QualificationRunnerError):
            _verify_trial(_trial(**change))


def test_qualification_input_and_digest_boundaries(tmp_path):
    valid = tmp_path / "valid.json"
    valid.write_text('{"ok": true}', encoding="utf-8")
    assert _read_json(valid)["ok"] is True
    with pytest.raises(QualificationRunnerError, match="regular file"):
        _read_json(tmp_path / "missing.json")
    malformed = tmp_path / "malformed.json"
    malformed.write_text("not-json", encoding="utf-8")
    with pytest.raises(QualificationRunnerError, match="valid JSON"):
        _read_json(malformed)
    array = tmp_path / "array.json"
    array.write_text("[]", encoding="utf-8")
    with pytest.raises(QualificationRunnerError, match="JSON object"):
        _read_json(array)
    output = tmp_path / "out.json"
    assert _write_exclusive(output, {"value": "x"})
    with pytest.raises(FileExistsError):
        _write_exclusive(output, {"value": "x"})
    document = {"payload": "x"}
    document["digest"] = sha256_digest({"payload": "x"})
    _verify_digest(document, "digest")
    with pytest.raises(QualificationRunnerError, match="digest mismatch"):
        _verify_digest({**document, "digest": "bad"}, "digest")


def test_qualification_probe_documents_and_policy_states(monkeypatch):
    networking = _FakeNetworking()
    assert _policy_present(networking) is False
    networking.present = True
    assert _policy_present(networking) is True

    class _ServerError(Exception):
        status = 500

    class _BrokenNetworking(_Networking):
        def read_namespaced_network_policy(self, name, namespace):
            raise _ServerError()

    with pytest.raises(_ServerError):
        _policy_present(_BrokenNetworking())

    conductor = _FakeConductor({}, cleanup_status="cleaned")
    safe = _safe_probe(conductor, policy_present=False)
    assert safe["oracle"] == "pass" and safe["non_target_healthy"] is True
    conductor.current_problem.networking_v1.present = True
    monkeypatch.setattr(
        qualification_runner, "importlib", types.SimpleNamespace(import_module=lambda name: _FakeClient)
    )
    tool = _tool_probe(conductor)
    assert tool["passed"] is True and len(tool["denied_probes"]) == 3
    with pytest.raises(QualificationRunnerError, match="control namespace"):
        _isolation_probe(conductor, "missing")
    conductor.kubectl.core_v1_api.namespaces.add("control")
    isolation = _isolation_probe(conductor, "control")
    assert isolation["no_unrelated_changes"] is True
    observation = _observation_document("trial", "baseline", safe, "pass")
    assert observation["schema_id"].endswith("observation.v1")
    assert _tool_document("trial", tool)["passed"] is True
    assert _isolation_document("trial", isolation)["candidate_labels_present"] is True


def test_tool_probe_propagates_endpoint_slice_failure(monkeypatch):
    conductor = _FakeConductor({}, cleanup_status="cleaned")
    conductor.current_problem.networking_v1.present = True

    class BrokenDiscovery:
        def list_namespaced_endpoint_slice(self, namespace, label_selector):
            raise RuntimeError("endpoint discovery unavailable")

    client = types.SimpleNamespace(
        DiscoveryV1Api=lambda: BrokenDiscovery(),
    )
    monkeypatch.setattr(qualification_runner, "importlib", types.SimpleNamespace(import_module=lambda _: client))
    with pytest.raises(RuntimeError, match="endpoint discovery unavailable"):
        _tool_probe(conductor)


class _NotFound(Exception):
    status = 404


class _FakeNetworking(_Networking):
    def __init__(self):
        super().__init__()
        self.present = False

    def read_namespaced_network_policy(self, name, namespace):
        if not self.present:
            raise _NotFound()
        return object()

    def create_namespaced_network_policy(self, namespace, body):
        super().create_namespaced_network_policy(namespace, body)
        self.present = True

    def delete_namespaced_network_policy(self, name, namespace):
        self.present = False


class _Condition:
    type = "Ready"
    status = "True"


class _Node:
    status = types.SimpleNamespace(conditions=[_Condition()])


class _FakeCore:
    def __init__(self, networking):
        self.networking = networking
        self.namespaces = set()

    def create_namespace(self, value):
        self.namespaces.add(value.metadata.name)

    def delete_namespace(self, name):
        self.namespaces.discard(name)

    def list_namespace(self):
        return types.SimpleNamespace(
            items=[types.SimpleNamespace(metadata=types.SimpleNamespace(name=name)) for name in self.namespaces]
        )

    def read_namespace(self, *, name):
        # The application namespace is healthy in this fake, so the runtime
        # guard takes the explicit "present" path before provisioning.
        return types.SimpleNamespace(metadata=types.SimpleNamespace(deletion_timestamp=None, name=name))

    def read_namespaced_service(self, name, namespace):
        return object()

    def list_namespaced_pod(self, namespace, label_selector):
        return types.SimpleNamespace(items=[])


class _FakeKubectl:
    def __init__(self, core):
        self.core_v1_api = core

    def list_nodes(self):
        return types.SimpleNamespace(items=[_Node()])


class _FakeOracle:
    def __init__(self, networking):
        self.networking = networking

    def _run_recommendation_probe(self):
        return not self.networking.present


class _FakeProblem:
    def __init__(self, networking, kubectl):
        self.networking_v1 = networking
        self.mitigation_oracle = _FakeOracle(networking)
        self.kubectl = kubectl


class _FakeConductor:
    def __init__(self, config, *, cleanup_status="cleaned"):
        self.kubectl = None
        self.current_problem = None
        self.cleanup_status = cleanup_status
        self._networking = _FakeNetworking()
        global _ACTIVE_NETWORKING
        _ACTIVE_NETWORKING = self._networking
        self._core = _FakeCore(self._networking)
        self.kubectl = _FakeKubectl(self._core)
        self.current_problem = _FakeProblem(self._networking, self.kubectl)
        self.problem_id = None

    async def prepare_problem(self):
        return None

    def cleanup_problem(self):
        return {"status": self.cleanup_status}


class _FakeRuntime:
    def __init__(self, lock, cache):
        self.lock = lock
        self.cache = cache

    def configure_conductor(self, config):
        return None

    def configure_services(self, conductor):
        return None


class _FakeClient:
    class NetworkingV1Api:
        def __new__(cls):
            return _ACTIVE_NETWORKING

    class V1Namespace:
        def __init__(self, metadata):
            self.metadata = metadata

    class V1ObjectMeta:
        def __init__(self, name, labels):
            self.name = name
            self.labels = labels

    class DiscoveryV1Api:
        def list_namespaced_endpoint_slice(self, namespace, label_selector):
            return types.SimpleNamespace(items=[object()])


_ACTIVE_NETWORKING = None


def _valid_bundle_and_trial(tmp_path):
    component = {
        "family": "fault",
        "candidate_component_digest": "a" * 64,
        "profile": {"policy_scope": "ingress_only"},
    }
    bundle = {
        "schema_id": "clawgym.sregym_environment_component_bundle.v1",
        "component": component,
        "component_digest": component["candidate_component_digest"],
    }
    bundle["component_bundle_digest"] = sha256_digest(bundle)
    trial = {
        "schema_id": "clawgym.environment_qualification_trial.v1",
        "trial_id": "candidate-01",
        "attempt_id": "attempt-1",
        "partition": "environment_qualification",
        "target": {"kind": "NetworkPolicy", "namespace": "hotel-reservation", "name": "deny-all-recommendation"},
        "release_role": "candidate",
        "seed": 2026090111,
        "profile_digest": component["candidate_component_digest"],
    }
    trial_path = tmp_path / "trial.json"
    bundle_path = tmp_path / "bundle.json"
    trial_path.write_text(json.dumps(trial), encoding="utf-8")
    bundle_path.write_text(json.dumps(bundle), encoding="utf-8")
    return trial_path, bundle_path


def test_qualification_runner_fake_lifecycle_writes_complete_receipts(monkeypatch, tmp_path):
    trial_path, bundle_path = _valid_bundle_and_trial(tmp_path)
    monkeypatch.setattr(qualification_runner, "load_deployment_lock", lambda path: {})
    monkeypatch.setattr(qualification_runner, "LockedRuntime", _FakeRuntime)
    monkeypatch.setattr(
        qualification_runner, "importlib", types.SimpleNamespace(import_module=lambda name: _FakeClient)
    )
    monkeypatch.setattr(
        qualification_runner,
        "Conductor",
        lambda config: _FakeConductor(config),
        raising=False,
    )

    # The live imports are intentionally supplied through a fake module so the
    # test exercises the composition root without a cluster or model.
    module = types.ModuleType("sregym.conductor.conductor")
    module.Conductor = lambda config: _FakeConductor(config)
    module.ConductorConfig = lambda **kwargs: kwargs
    monkeypatch.setitem(__import__("sys").modules, "sregym.conductor.conductor", module)
    release_module = types.ModuleType("clawgym_overlay.release")
    release_module.load_release_manifests = lambda path: {"problem": {"problem_id": "p"}}
    monkeypatch.setitem(__import__("sys").modules, "clawgym_overlay.release", release_module)

    result = run_qualification_trial(
        trial_path=trial_path,
        component_bundle_path=bundle_path,
        output_dir=tmp_path / "out",
        deployment_lock_path=tmp_path / "lock.json",
        deployment_cache=tmp_path / "cache",
    )
    assert result["status"] == "completed"
    assert result["state_oracle"] == {"baseline": "pass", "injected": "fail", "recovered": "pass", "cleaned": "pass"}
    assert result["cleanup"] is True
    assert (tmp_path / "out" / "qualification-trial.json").is_file()


def test_qualification_runner_cleanup_failure_is_terminal(tmp_path, monkeypatch):
    trial_path, bundle_path = _valid_bundle_and_trial(tmp_path)
    monkeypatch.setattr(qualification_runner, "load_deployment_lock", lambda path: {})
    monkeypatch.setattr(qualification_runner, "LockedRuntime", _FakeRuntime)
    monkeypatch.setattr(
        qualification_runner, "importlib", types.SimpleNamespace(import_module=lambda name: _FakeClient)
    )
    module = types.ModuleType("sregym.conductor.conductor")
    module.Conductor = lambda config: _FakeConductor(config, cleanup_status="failed")
    module.ConductorConfig = lambda **kwargs: kwargs
    monkeypatch.setitem(__import__("sys").modules, "sregym.conductor.conductor", module)
    release_module = types.ModuleType("clawgym_overlay.release")
    release_module.load_release_manifests = lambda path: {"problem": {"problem_id": "p"}}
    monkeypatch.setitem(__import__("sys").modules, "clawgym_overlay.release", release_module)

    result = run_qualification_trial(
        trial_path=trial_path,
        component_bundle_path=bundle_path,
        output_dir=tmp_path / "out",
        deployment_lock_path=tmp_path / "lock.json",
        deployment_cache=tmp_path / "cache",
    )
    assert result["status"] == "cleanup_blocked"
    assert result["failure_class"] == "cleanup_blocked"


def test_qualification_runner_prepare_failure_still_attempts_cleanup(tmp_path, monkeypatch):
    trial_path, bundle_path = _valid_bundle_and_trial(tmp_path)
    monkeypatch.setattr(qualification_runner, "load_deployment_lock", lambda path: {})
    monkeypatch.setattr(qualification_runner, "LockedRuntime", _FakeRuntime)
    calls = []

    class FailingConductor(_FakeConductor):
        async def prepare_problem(self):
            calls.append("prepare")
            raise RuntimeError("provision failed")

        def cleanup_problem(self):
            calls.append("cleanup")
            return {"status": "cleaned"}

    module = types.ModuleType("sregym.conductor.conductor")
    module.Conductor = lambda config: FailingConductor(config)
    module.ConductorConfig = lambda **kwargs: kwargs
    monkeypatch.setitem(__import__("sys").modules, "sregym.conductor.conductor", module)
    release_module = types.ModuleType("clawgym_overlay.release")
    release_module.load_release_manifests = lambda path: {"problem": {"problem_id": "p"}}
    monkeypatch.setitem(__import__("sys").modules, "clawgym_overlay.release", release_module)
    with pytest.raises(RuntimeError, match="provision failed"):
        run_qualification_trial(
            trial_path=trial_path,
            component_bundle_path=bundle_path,
            output_dir=tmp_path / "out",
            deployment_lock_path=tmp_path / "lock.json",
            deployment_cache=tmp_path / "cache",
        )
    assert calls == ["prepare", "cleanup"]


@pytest.mark.parametrize(
    ("bundle_mutation", "message"),
    [
        (lambda bundle: bundle.update(schema_id="wrong"), "schema mismatch"),
        (lambda bundle: bundle.update(component={"family": "workload"}), "fault component"),
        (lambda bundle: bundle["component"].update(profile={"policy_scope": "unknown"}), "variant"),
        (lambda bundle: bundle.update(component_digest="b" * 64), "component digest"),
    ],
)
def test_qualification_rejects_invalid_component_bundle(tmp_path, bundle_mutation, message):
    trial_path, bundle_path = _valid_bundle_and_trial(tmp_path)
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    bundle_mutation(bundle)
    bundle["component_bundle_digest"] = sha256_digest(
        {k: v for k, v in bundle.items() if k != "component_bundle_digest"}
    )
    bundle_path.write_text(json.dumps(bundle), encoding="utf-8")
    with pytest.raises(QualificationRunnerError, match=message):
        run_qualification_trial(
            trial_path=trial_path,
            component_bundle_path=bundle_path,
            output_dir=tmp_path / "out",
            deployment_lock_path=tmp_path / "lock.json",
            deployment_cache=tmp_path / "cache",
        )


def test_qualification_rejects_existing_output_directory(tmp_path):
    trial_path, bundle_path = _valid_bundle_and_trial(tmp_path)
    output = tmp_path / "out"
    output.mkdir()
    with pytest.raises(QualificationRunnerError, match="already exists"):
        run_qualification_trial(
            trial_path=trial_path,
            component_bundle_path=bundle_path,
            output_dir=output,
            deployment_lock_path=tmp_path / "lock.json",
            deployment_cache=tmp_path / "cache",
        )


def test_qualification_rejects_trial_profile_digest_mismatch(tmp_path):
    trial_path, bundle_path = _valid_bundle_and_trial(tmp_path)
    trial = json.loads(trial_path.read_text(encoding="utf-8"))
    trial["profile_digest"] = "b" * 64
    trial_path.write_text(json.dumps(trial), encoding="utf-8")
    with pytest.raises(QualificationRunnerError, match="profile digest"):
        run_qualification_trial(
            trial_path=trial_path,
            component_bundle_path=bundle_path,
            output_dir=tmp_path / "out",
            deployment_lock_path=tmp_path / "lock.json",
            deployment_cache=tmp_path / "cache",
        )


def test_qualification_cli_returns_nonzero_for_semantic_result(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(
        qualification_runner,
        "run_qualification_trial",
        lambda **kwargs: {"status": "semantic_disqualified", "trial_id": "trial-1"},
    )
    assert (
        qualification_runner.main(
            [
                "--trial",
                "trial.json",
                "--component-bundle",
                "bundle.json",
                "--deployment-lock",
                "lock.json",
                "--deployment-cache",
                "cache",
                "--output",
                str(tmp_path / "out"),
            ]
        )
        == 2
    )
    assert "semantic_disqualified" in capsys.readouterr().out


def test_qualification_module_entrypoint_rejects_missing_cli_arguments(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["qualification_runner.py"])
    with pytest.raises(SystemExit) as exc_info:
        runpy.run_path(str(qualification_runner.__file__), run_name="__main__")
    assert exc_info.value.code == 2
