from __future__ import annotations

import ast
import contextlib
import json
import shutil
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[2]


def isolated_method(path: Path, class_name: str, method_name: str, namespace):
    parsed = ast.parse(path.read_text())
    class_node = next(
        node for node in parsed.body if isinstance(node, ast.ClassDef) and node.name == class_name
    )
    function = next(
        node
        for node in class_node.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == method_name
    )
    module = ast.fix_missing_locations(ast.Module(body=[function], type_ignores=[]))
    exec(compile(module, str(path), "exec"), namespace)
    return namespace[method_name]


class FakeApiException(Exception):
    def __init__(self, status):
        self.status = status


def test_network_policy_recovery_accepts_absence_and_propagates_other_errors() -> None:
    recover = isolated_method(
        ROOT / "sregym/conductor/problems/network_policy_block.py",
        "NetworkPolicyBlock",
        "recover_fault",
        {"ApiException": FakeApiException},
    )

    class Networking:
        status = 404

        def delete_namespaced_network_policy(self, **kwargs):
            raise FakeApiException(self.status)

    problem = type("Problem", (), {})()
    problem.networking_v1 = Networking()
    problem.policy_name = "deny-all-recommendation"
    problem.namespace = "hotel-reservation"
    problem.fault_injected = True
    assert recover(problem) == {"status": "already_absent"}
    assert problem.fault_injected is False
    problem.networking_v1.status = 500
    with pytest.raises(FakeApiException):
        recover(problem)


class JsonYaml:
    @staticmethod
    def safe_load_all(handle):
        return [json.load(handle)]

    @staticmethod
    def safe_dump_all(documents, handle, sort_keys=False):
        json.dump(documents[0], handle)


def test_hotel_application_renders_every_locked_image_override(tmp_path: Path) -> None:
    rendered = isolated_method(
        ROOT / "sregym/service/apps/hotel_reservation.py",
        "HotelReservation",
        "_rendered_deployment_configs",
        {
            "contextlib": contextlib,
            "tempfile": tempfile,
            "shutil": shutil,
            "Path": Path,
            "yaml": JsonYaml,
            "Iterator": Iterator,
        },
    )
    source = tmp_path / "source"
    source.mkdir()
    manifest = source / "deployment.yaml"
    manifest.write_text(
        json.dumps(
            {
                "kind": "Deployment",
                "metadata": {"name": "recommendation"},
                "spec": {
                    "template": {
                        "spec": {"containers": [{"name": "app", "image": "image:latest"}]}
                    }
                },
            }
        )
    )
    app = type("Application", (), {})()
    app.k8s_deploy_path = source
    app.deployment_env_overrides = {}
    app.deployment_image_overrides = {"image": "image@sha256:" + "a" * 64}

    with rendered(app) as rendered_path:
        document = json.loads((rendered_path / "deployment.yaml").read_text())
        assert document["spec"]["template"]["spec"]["containers"][0]["image"].endswith(
            "a" * 64
        )

    app.deployment_image_overrides = {"missing": "image@sha256:" + "b" * 64}
    with pytest.raises(RuntimeError, match="image override targets were not found"):
        with rendered(app):
            pass


def test_mcp_server_renders_digest_override_without_mutating_source() -> None:
    deployment_resources = isolated_method(
        ROOT / "sregym/service/mcp_server.py",
        "MCPServer",
        "_deployment_resources",
        {
            "contextmanager": contextlib.contextmanager,
            "MCP_SERVER_K8S": ROOT / "mcp_server/k8s",
            "Path": Path,
            "shutil": shutil,
            "tempfile": tempfile,
            "yaml": yaml,
        },
    )
    source = ROOT / "mcp_server/k8s/kustomization.yaml"
    before = source.read_bytes()
    server = type("Server", (), {})()
    server.image_override = "ghcr.io/sregym/sregym-mcp@sha256:" + "a" * 64

    with deployment_resources(server) as resources:
        document = yaml.safe_load((resources / "kustomization.yaml").read_text())
        image = next(item for item in document["images"] if item["name"] == "sregym")
        assert image == {
            "name": "sregym",
            "newName": "ghcr.io/sregym/sregym-mcp",
            "digest": "sha256:" + "a" * 64,
        }

    assert source.read_bytes() == before


def test_mcp_server_rejects_mutable_image_override() -> None:
    deployment_resources = isolated_method(
        ROOT / "sregym/service/mcp_server.py",
        "MCPServer",
        "_deployment_resources",
        {
            "contextmanager": contextlib.contextmanager,
            "MCP_SERVER_K8S": ROOT / "mcp_server/k8s",
            "Path": Path,
            "shutil": shutil,
            "tempfile": tempfile,
            "yaml": yaml,
        },
    )
    server = type("Server", (), {})()
    server.image_override = "ghcr.io/sregym/sregym-mcp:latest"
    with pytest.raises(RuntimeError, match="immutable"):
        with deployment_resources(server):
            pass


def test_wrk_job_uses_configured_immutable_image(tmp_path: Path) -> None:
    create_wrk_job = isolated_method(
        ROOT / "sregym/generators/workload/wrk2.py",
        "Wrk2",
        "create_wrk_job",
        {},
    )
    template = tmp_path / "generators/workload"
    template.mkdir(parents=True)
    (template / "wrk-job-template.yaml").write_text(
        yaml.safe_dump(
            {
                "metadata": {"name": "template"},
                "spec": {
                    "template": {
                        "spec": {
                            "containers": [{"name": "workload", "image": "mutable:latest"}],
                            "volumes": [],
                        }
                    }
                },
            }
        )
    )
    created = []

    class MissingJob(Exception):
        status = 404

    class Batch:
        def read_namespaced_job(self, **kwargs):
            raise MissingJob()

        def create_namespaced_job(self, namespace, body):
            created.append(body)
            return type("Response", (), {"metadata": type("Metadata", (), {"name": "wrk"})()})()

    fake_client = type(
        "Client",
        (),
        {
            "BatchV1Api": lambda: Batch(),
            "V1DeleteOptions": object,
            "exceptions": type("Exceptions", (), {"ApiException": MissingJob}),
        },
    )
    fake_logger = type("Logger", (), {"info": lambda *args, **kwargs: None, "error": lambda *args, **kwargs: None})()
    create_wrk_job.__globals__.update(
        BASE_DIR=tmp_path,
        client=fake_client,
        logger=fake_logger,
        yaml=yaml,
    )
    wrk = type("Wrk", (), {})()
    wrk.namespace = "hotel-reservation"
    wrk.image = "deathstarbench/wrk2-client@sha256:" + "b" * 64
    wrk.wait_for_job_deletion = lambda *args, **kwargs: None

    create_wrk_job(wrk, "wrk2-job", "hotel-reservation", Path("payload.lua"))

    assert created[0]["spec"]["template"]["spec"]["containers"][0]["image"] == wrk.image
