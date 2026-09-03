from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from clawgym.contracts import sha256_digest

import clawgym_overlay.reference_runner as reference_runner
from clawgym_overlay.reference_runner import (
    ReferenceAgentSecretError,
    SafeStratusRunner,
    _r1b_config_mounts,
    _r1c_config_mounts,
    _r1d_config_mounts,
    _r1e_config_mounts,
    _r1f_config_mounts,
    _r1i_config_mounts,
    _safe_text,
    read_agent_secret,
)


def test_reference_secret_requires_private_regular_file(tmp_path: Path) -> None:
    path = tmp_path / "agent-key"
    path.write_text("new-key")
    os.chmod(path, 0o644)
    with pytest.raises(ReferenceAgentSecretError, match="0600"):
        read_agent_secret(path)
    os.chmod(path, 0o600)
    assert read_agent_secret(path) == "new-key"


def test_reference_secret_rejects_empty_or_symlink(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.touch(mode=0o600)
    os.chmod(empty, 0o600)
    with pytest.raises(ReferenceAgentSecretError, match="empty"):
        read_agent_secret(empty)
    linked = tmp_path / "linked"
    linked.symlink_to(empty)
    with pytest.raises(ReferenceAgentSecretError, match="non-symlink"):
        read_agent_secret(linked)


def test_safe_text_redacts_model_key_material_and_host_paths() -> None:
    safe = _safe_text(
        b"Authorization: Bearer abcdefghijklmnop /var/run/example 10.20.1.27 i-t4nf7igf5ax6pg9s8jc1 client-key-data:"
    )
    assert "abcdefghijklmnop" not in safe
    assert "/var/run/example" not in safe
    assert "10.20.1.27" not in safe
    assert "i-t4nf7igf5ax6pg9s8jc1" not in safe
    assert "client-key-data:" not in safe
    assert "[REDACTED]" in safe
    assert "[HOST_PATH]" in safe


def test_runner_replaces_shell_entrypoint_with_frozen_python_command(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    secret = tmp_path / "agent-key"
    secret.write_text("not-a-real-key")
    secret.chmod(0o600)
    kubeconfig = tmp_path / "filtered-kubeconfig"
    kubeconfig.write_text("safe")
    calls: list[list[str]] = []

    def fake_run(command, **kwargs):
        calls.append(list(command))
        if command[:3] == ["docker", "image", "inspect"]:
            return SimpleNamespace(stdout="sha256:" + "a" * 64 + "\n")
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr("clawgym_overlay.reference_runner.subprocess.run", fake_run)
    runner = SafeStratusRunner(
        profile={
            "model_id": "openai/deepseek-v4-pro",
            "api_base": "https://example.invalid/v1",
            "artifact_id": "network_policy_block",
            "command": ["python", "-m", "clients.stratus.stratus_agent.driver.driver"],
        },
        secret_file=secret,
    )
    result = runner(SimpleNamespace(manifest_digest="a" * 64), str(kubeconfig))
    docker = calls[1]
    entrypoint = docker.index("--entrypoint")
    assert docker[entrypoint + 1] == "python"
    assert docker[-2:] == ["-m", "clients.stratus.stratus_agent.driver.driver"]
    assert "--user" in docker
    assert any(item.endswith(":/home/agent/.kube/config:ro") for item in docker)
    assert "KUBECONFIG=/home/agent/.kube/config" in docker
    assert "SREGYM_ARTIFACT_ID=network_policy_block" in docker
    assert "--env-file" in docker
    assert "AGENT_API_KEY=not-a-real-key" not in docker
    assert result.image_digest == "a" * 64


def test_r1b_runner_mounts_only_registered_bounded_configuration(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    secret = tmp_path / "agent-key"
    secret.write_text("not-a-real-key")
    secret.chmod(0o600)
    kubeconfig = tmp_path / "filtered-kubeconfig"
    kubeconfig.write_text("safe")
    calls: list[list[str]] = []

    def fake_run(command, **kwargs):
        calls.append(list(command))
        if command[:3] == ["docker", "image", "inspect"]:
            return SimpleNamespace(stdout="sha256:" + "b" * 64 + "\n")
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr("clawgym_overlay.reference_runner.subprocess.run", fake_run)
    from clawgym_overlay.reference_profiles import load_reference_agent_profile

    root = Path(__file__).resolve().parents[2] / "clawgym_overlay" / "manifests"
    profile = load_reference_agent_profile(
        root,
        profile_digest=sha256_digest(json.loads((root / "agent.reference-stratus-r1b.v1.json").read_text())),
    )
    result = SafeStratusRunner(profile=profile, secret_file=secret)(
        SimpleNamespace(manifest_digest="b" * 64), str(kubeconfig)
    )
    docker = calls[1]
    assert "PYTHONPATH=/opt/clawgym_overlay:/opt/sregym" in docker
    assert any("diagnosis_agent_config.yaml:ro" in item for item in docker)
    assert any("mitigation_agent_config.yaml:ro" in item for item in docker)
    assert docker[-2:] == ["-m", "reference_driver"]
    assert result.timeout_seconds == 900


def test_r1f_runner_mounts_only_registered_host_normalized_configuration(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    secret = tmp_path / "agent-key"
    secret.write_text("not-a-real-key")
    secret.chmod(0o600)
    kubeconfig = tmp_path / "filtered-kubeconfig"
    kubeconfig.write_text("safe")
    calls: list[list[str]] = []

    def fake_run(command, **kwargs):
        calls.append(list(command))
        if command[:3] == ["docker", "image", "inspect"]:
            return SimpleNamespace(stdout="sha256:" + "c" * 64 + "\n")
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr("clawgym_overlay.reference_runner.subprocess.run", fake_run)
    from clawgym_overlay.reference_profiles import load_reference_agent_profile

    root = Path(__file__).resolve().parents[2] / "clawgym_overlay" / "manifests"
    profile = load_reference_agent_profile(
        root,
        profile_digest=sha256_digest(json.loads((root / "agent.reference-stratus-r1f.v1.json").read_text())),
    )
    result = SafeStratusRunner(profile=profile, secret_file=secret)(
        SimpleNamespace(manifest_digest="c" * 64), str(kubeconfig)
    )
    docker = calls[1]
    assert "PYTHONPATH=/opt:/opt/clawgym_overlay:/opt/sregym" in docker
    assert any("reference_driver_r1f.py:ro" in item for item in docker)
    assert any("r1f_protocol.py:ro" in item for item in docker)
    assert any("diagnosis_agent_config.yaml:ro" in item for item in docker)
    assert docker[-2:] == ["-m", "reference_driver_r1f"]
    assert result.timeout_seconds == 900


def test_materialized_runner_mounts_explicit_bundle_and_reference_driver(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    secret = tmp_path / "agent-key"
    secret.write_text("not-a-real-key")
    secret.chmod(0o600)
    kubeconfig = tmp_path / "filtered-kubeconfig"
    kubeconfig.write_text("safe")
    bundle_root = tmp_path / "materialized"
    files_root = bundle_root / "reference-materialized"
    files_root.mkdir(parents=True)
    entries = []
    for name in (
        "diagnosis_agent_config.yaml",
        "diagnosis_agent_prompts.yaml",
        "mitigation_agent_config.yaml",
        "mitigation_agent_prompts.yaml",
    ):
        content = f"{name}: safe\n".encode()
        path = files_root / name
        path.write_bytes(content)
        entries.append(
            {
                "path": f"reference-materialized/{name}",
                "container_path": f"/opt/sregym/clients/stratus/configs/{name}",
                "sha256_digest": __import__("hashlib").sha256(content).hexdigest(),
                "bytes": len(content),
            }
        )
    import hashlib

    config = {
        "schema_id": "clawgym.sregym_reference_agent_config_bundle.v2",
        "component_bundle_digest": "a" * 64,
        "semantic_component_digest": "b" * 64,
        "diagnosis_step_limit": 8,
        "mitigation_step_limit": 8,
        "components": {},
        "files": entries,
    }
    config["config_bundle_digest"] = hashlib.sha256(
        json.dumps(config, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()
    (bundle_root / "config-bundle.json").write_text(
        json.dumps(config, sort_keys=True, separators=(",", ":")), encoding="utf-8"
    )
    calls: list[list[str]] = []

    def fake_run(command, **kwargs):
        calls.append(list(command))
        if command[:3] == ["docker", "image", "inspect"]:
            return SimpleNamespace(stdout="sha256:" + "d" * 64 + "\n")
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr("clawgym_overlay.reference_runner.subprocess.run", fake_run)
    profile = {
        "model_id": "openai/deepseek-v4-pro",
        "api_base": "https://example.invalid/v1",
        "artifact_id": "network_policy_block",
        "command": ["python", "-m", "reference_driver_r1f"],
        "sop_variant": "materialized-reference-v1",
        "runtime_protocol": "r1i-typed-handoff-journal-v1",
        "handoff_argument_protocol": "structured-submit-tool-argument-v1",
        "config_bundle_digest": config["config_bundle_digest"],
        "bounded_execution": {"container_timeout_seconds": 900},
    }
    result = SafeStratusRunner(profile=profile, secret_file=secret, materialization_bundle=bundle_root)(
        SimpleNamespace(manifest_digest="d" * 64), str(kubeconfig)
    )
    docker = calls[1]
    assert "PYTHONPATH=/opt:/opt/clawgym_overlay:/opt/sregym" in docker
    assert "SREGYM_RUNTIME_PROTOCOL=r1i-typed-handoff-journal-v1" in docker
    assert "SREGYM_HANDOFF_ARGUMENT_PROTOCOL=structured-submit-tool-argument-v1" in docker
    assert any("reference_driver_r1f.py:ro" in item for item in docker)
    assert any("r1f_protocol.py:ro" in item for item in docker)
    assert sum("diagnosis_agent_config.yaml:ro" in item for item in docker) == 1
    assert docker[-2:] == ["-m", "reference_driver_r1f"]
    assert result.image_digest == "d" * 64


def _materialized_fixture(tmp_path: Path) -> tuple[dict[str, object], Path]:
    """Build a minimal, digest-bound materialization bundle for negative tests."""
    root = tmp_path / "materialized-negative"
    files_root = root / "reference-materialized"
    files_root.mkdir(parents=True)
    files: list[dict[str, object]] = []
    for name in (
        "diagnosis_agent_config.yaml",
        "diagnosis_agent_prompts.yaml",
        "mitigation_agent_config.yaml",
        "mitigation_agent_prompts.yaml",
    ):
        payload = f"{name}: safe\n".encode()
        (files_root / name).write_bytes(payload)
        files.append(
            {
                "path": f"reference-materialized/{name}",
                "container_path": f"/opt/sregym/clients/stratus/configs/{name}",
                "sha256_digest": __import__("hashlib").sha256(payload).hexdigest(),
            }
        )
    bundle: dict[str, object] = {"schema_id": "clawgym.sregym_reference_agent_config_bundle.v2", "files": files}
    bundle["config_bundle_digest"] = (
        __import__("hashlib")
        .sha256(
            json.dumps(
                {k: v for k, v in bundle.items() if k != "config_bundle_digest"},
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode()
        )
        .hexdigest()
    )
    (root / "config-bundle.json").write_text(
        json.dumps(bundle, sort_keys=True, separators=(",", ":")), encoding="utf-8"
    )
    return {"config_bundle_digest": bundle["config_bundle_digest"]}, root


@pytest.mark.parametrize("mutation", ["extra", "symlink", "traversal", "digest", "target"])
def test_materialized_bundle_inventory_and_mounts_fail_closed(tmp_path: Path, mutation: str) -> None:
    profile, root = _materialized_fixture(tmp_path)
    bundle_path = root / "config-bundle.json"
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    files = bundle["files"]
    if mutation == "extra":
        (root / "reference-materialized" / "extra.yaml").write_text("unexpected", encoding="utf-8")
    elif mutation == "symlink":
        extra = root / "reference-materialized" / "extra.yaml"
        extra.symlink_to(root / "config-bundle.json")
    elif mutation == "traversal":
        files[0]["path"] = "reference-materialized/../config-bundle.json"
    elif mutation == "digest":
        files[0]["sha256_digest"] = "0" * 64
    else:
        files[0]["container_path"] = "/tmp/escape"
    bundle["config_bundle_digest"] = (
        __import__("hashlib")
        .sha256(
            json.dumps(
                {k: v for k, v in bundle.items() if k != "config_bundle_digest"},
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode()
        )
        .hexdigest()
    )
    bundle_path.write_text(json.dumps(bundle, sort_keys=True, separators=(",", ":")), encoding="utf-8")
    profile["config_bundle_digest"] = bundle["config_bundle_digest"]
    with pytest.raises(RuntimeError, match="inventory|invalid|digest|target"):
        reference_runner._materialized_config_mounts(profile, root)


@pytest.mark.parametrize(
    ("loader", "variant"),
    [
        (_r1b_config_mounts, "r1-evidence-first-bounded-v1"),
        (_r1c_config_mounts, "r1c-structured-attribution-v1"),
        (_r1d_config_mounts, "r1d-typed-remediation-v1"),
        (_r1e_config_mounts, "r1e-runtime-gated-v1"),
        (_r1f_config_mounts, "r1f-host-normalized-remediation-v1"),
        (_r1i_config_mounts, "r1i-typed-handoff-journal-v1"),
    ],
)
def test_fixed_config_loaders_fail_closed_on_variant_or_bundle_digest(loader, variant: str) -> None:
    assert loader({"sop_variant": "unregistered"}) == []
    with pytest.raises(RuntimeError, match="digest mismatch"):
        loader({"sop_variant": variant, "config_bundle_digest": "0" * 64})


@pytest.mark.parametrize(
    ("loader", "variant", "bundle_name", "expected_files"),
    [
        (_r1b_config_mounts, "r1-evidence-first-bounded-v1", "agent.reference-stratus-r1b.config-bundle.v1.json", 2),
        (_r1c_config_mounts, "r1c-structured-attribution-v1", "agent.reference-stratus-r1c.config-bundle.v1.json", 2),
        (_r1d_config_mounts, "r1d-typed-remediation-v1", "agent.reference-stratus-r1d.config-bundle.v1.json", 4),
        (_r1e_config_mounts, "r1e-runtime-gated-v1", "agent.reference-stratus-r1e.config-bundle.v1.json", 4),
        (
            _r1f_config_mounts,
            "r1f-host-normalized-remediation-v1",
            "agent.reference-stratus-r1f.config-bundle.v1.json",
            4,
        ),
        (_r1i_config_mounts, "r1i-typed-handoff-journal-v1", "agent.reference-stratus-r1i.config-bundle.v1.json", 4),
    ],
)
def test_fixed_config_loaders_reject_missing_files_and_wrong_inventory(
    monkeypatch: pytest.MonkeyPatch,
    loader,
    variant: str,
    bundle_name: str,
    expected_files: int,
) -> None:
    """Every pinned profile rejects a damaged or over-complete bundle."""

    root = Path(reference_runner.__file__).resolve().parent / "manifests"
    bundle = reference_runner._load_json_object(root / bundle_name)
    profile = {"sop_variant": variant, "config_bundle_digest": bundle["bundle_digest"]}
    original_files = reference_runner._bundle_files(bundle)

    monkeypatch.setattr(
        reference_runner,
        "_bundle_files",
        lambda _bundle: [{"path": "missing.yaml", "container_path": "/tmp/missing", "sha256_digest": "0" * 64}],
    )
    with pytest.raises(RuntimeError, match="unavailable|digest mismatch"):
        loader(profile)

    monkeypatch.setattr(reference_runner, "_bundle_files", lambda _bundle: original_files + [original_files[0]])
    with pytest.raises(RuntimeError, match="incomplete"):
        loader(profile)
    assert len(original_files) == expected_files


def test_r1i_runner_mounts_typed_handoff_journal_configuration(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    secret = tmp_path / "agent-key"
    secret.write_text("not-a-real-key")
    secret.chmod(0o600)
    kubeconfig = tmp_path / "filtered-kubeconfig"
    kubeconfig.write_text("safe")
    calls: list[list[str]] = []

    def fake_run(command, **kwargs):
        calls.append(list(command))
        if command[:3] == ["docker", "image", "inspect"]:
            return SimpleNamespace(stdout="sha256:" + "d" * 64 + "\n")
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr("clawgym_overlay.reference_runner.subprocess.run", fake_run)
    from clawgym_overlay.reference_profiles import load_reference_agent_profile

    root = Path(__file__).resolve().parents[2] / "clawgym_overlay" / "manifests"
    profile = load_reference_agent_profile(
        root,
        profile_digest=sha256_digest(json.loads((root / "agent.reference-stratus-r1i.v1.json").read_text())),
    )
    result = SafeStratusRunner(profile=profile, secret_file=secret)(
        SimpleNamespace(manifest_digest="d" * 64), str(kubeconfig)
    )
    docker = calls[1]
    assert any("reference_driver_r1f.py:ro" in item for item in docker)
    assert any("/opt/sregym/clients/stratus/configs/diagnosis_agent_config.yaml:ro" in item for item in docker)
    assert docker[-2:] == ["-m", "reference_driver_r1f"]
    assert result.timeout_seconds == 900


def test_r1f_handoff_rejects_malformed_or_mismatched_records() -> None:
    run = _fake_run()
    base = {
        "schema_id": "clawgym.sregym_diagnosis_handoff.v2",
        "status": "complete",
        "run_manifest_digest": run.manifest_digest,
        "agent_release_digest": run.agent_release.agent_release_digest,
        "candidate_resource": {
            "kind": "NetworkPolicy",
            "namespace": "hotel-reservation",
            "name": "deny-all-recommendation",
        },
    }
    assert (
        reference_runner._extract_r1f_handoff(({"name": "r1f-handoff.json", "text": "{"},), run)["status"]
        == "incomplete"
    )
    for mutation in ("schema_id", "run_manifest_digest", "agent_release_digest", "candidate_resource"):
        record = dict(base)
        record[mutation] = "wrong" if mutation != "candidate_resource" else {"kind": "Service"}
        result = reference_runner._extract_r1f_handoff(({"name": "r1f-handoff.json", "text": json.dumps(record)},), run)
        assert result["status"] == "incomplete"
    valid = dict(base)
    result = reference_runner._extract_r1f_handoff(({"name": "r1f-handoff.json", "text": json.dumps(valid)},), run)
    assert result["status"] == "complete"


def test_r1c_handoff_requires_all_required_fields() -> None:
    run = _fake_run()
    malformed = {"symptom": "only"}
    result = reference_runner._extract_r1c_handoff(
        ({"name": "log.txt", "text": "R1C_HANDOFF_JSON " + json.dumps(malformed)},), run
    )
    assert result["status"] == "incomplete"


def test_gate_journal_loader_is_strict_and_identity_bound() -> None:
    run = _fake_run()
    assert reference_runner._extract_gate_event_journal((), run) is None
    assert (
        reference_runner._extract_gate_event_journal(({"name": "r1f-gate-event-journal.json", "text": "{"},), run)
        is None
    )
    base = {
        "schema_id": "clawgym.sregym_gate_event_journal.v1",
        "run_manifest_digest": run.manifest_digest,
        "agent_release_digest": run.agent_release.agent_release_digest,
        "state": {"precondition_read": True, "target_reread": True, "endpoint_ready": True},
    }
    base["journal_digest"] = reference_runner._digest_document(base, "journal_digest")
    assert (
        reference_runner._extract_gate_event_journal(
            ({"name": "r1f-gate-event-journal.json", "text": json.dumps(base)},), run
        )
        == base
    )
    for key, value in (
        ("schema_id", "wrong"),
        ("run_manifest_digest", "wrong"),
        ("agent_release_digest", "wrong"),
        ("journal_digest", "0" * 64),
        ("state", []),
    ):
        record = dict(base, **{key: value})
        assert (
            reference_runner._extract_gate_event_journal(
                ({"name": "r1f-gate-event-journal.json", "text": json.dumps(record)},), run
            )
            is None
        )
    assert (
        reference_runner._extract_gate_event_journal(({"name": "r1f-gate-event-journal.json", "text": "[]"},), run)
        is None
    )


def test_transaction_and_verification_use_valid_gate_state() -> None:
    run = _fake_run()
    target = {"kind": "NetworkPolicy", "namespace": "hotel-reservation", "name": "deny-all-recommendation"}
    ledger = {
        "records": [
            {
                "operation": "read",
                "resource": target,
                "sequence": 1,
                "result_summary": "present",
                "outcome": "executed",
            },
            {
                "operation": "mutate",
                "resource": target,
                "sequence": 2,
                "outcome": "executed",
                "command_sha256": "a" * 64,
            },
            {"operation": "read", "resource": target, "sequence": 3, "result_summary": "not_found"},
            {
                "operation": "read",
                "resource": {"kind": "Endpoints", "namespace": "hotel-reservation", "name": "recommendation"},
                "sequence": 4,
                "result_summary": "ready",
            },
        ]
    }
    journal = {"state": {"precondition_read": True, "target_reread": True, "endpoint_ready": True}}
    transaction = reference_runner._r1d_transaction({}, ledger, run)
    assert transaction["status"] == "executed"
    transaction_with_journal = reference_runner._r1e_transaction({}, ledger, run, journal)
    assert transaction_with_journal["precondition"]["policy_exists"] is True
    observation = reference_runner._r1e_verification_observation(ledger, run, journal)
    assert any(item["outcome"] == "endpoint_ready" for item in observation["observations"])
    transaction_without_object_state = reference_runner._r1e_transaction({}, ledger, run, {"state": []})
    assert transaction_without_object_state["status"] == "executed"
    observation_from_journal = reference_runner._r1e_verification_observation(
        {"records": ledger["records"][:3]}, run, journal
    )
    assert any(item["outcome"] == "endpoint_ready" for item in observation_from_journal["observations"])


def test_action_ledger_ignores_malformed_and_duplicate_calls() -> None:
    run = _fake_run()
    records = (
        {"name": "events.jsonl", "text": "not-json\n" + json.dumps({"messages": {}})},
        {
            "name": "events.jsonl",
            "text": json.dumps(
                {
                    "messages": [
                        {"tool_calls": [{"id": ""}, {"id": "call-1", "name": "read", "args": {"resource": "x"}}]}
                    ]
                }
            ),
        },
        {
            "name": "events.jsonl",
            "text": json.dumps(
                {"messages": [{"tool_calls": [{"id": "call-1", "name": "read", "args": {"resource": "x"}}]}]}
            ),
        },
    )
    result = reference_runner._extract_action_ledger(records, run)
    assert result["summary"]["total"] == 1


def test_action_ledger_classifies_not_found_and_ready_responses() -> None:
    run = _fake_run()
    payload = {
        "messages": [
            {"tool_calls": [{"id": "call-not-found", "name": "read", "args": {"resource": "x"}}]},
            {"tool_call_id": "call-not-found", "content": "NotFound"},
            {
                "tool_calls": [
                    {
                        "id": "call-ready",
                        "name": "read",
                        "args": {
                            "resource": {
                                "kind": "Endpoints",
                                "namespace": "hotel-reservation",
                                "name": "recommendation",
                            }
                        },
                    }
                ]
            },
            {"tool_call_id": "call-ready", "content": "addresses ready"},
        ]
    }
    result = reference_runner._extract_action_ledger(({"name": "events.jsonl", "text": json.dumps(payload)},), run)
    summaries = [record["result_summary"] for record in result["records"]]
    assert "not_found" in summaries and "ready" in summaries


def test_materialized_runner_requires_bundle_when_variant_is_materialized(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    secret = tmp_path / "agent-key"
    secret.write_text("key")
    secret.chmod(0o600)
    kubeconfig = tmp_path / "kubeconfig"
    kubeconfig.write_text("safe")
    monkeypatch.setattr(
        reference_runner.subprocess,
        "run",
        lambda command, **kwargs: SimpleNamespace(stdout="sha256:" + "f" * 64, returncode=0),
    )
    runner = SafeStratusRunner(
        profile={
            "model_id": "m",
            "api_base": "https://example.invalid",
            "artifact_id": "x",
            "command": ["python", "-m", "reference_driver_r1f"],
            "sop_variant": "materialized-reference-v1",
            "config_bundle_digest": "a" * 64,
        },
        secret_file=secret,
    )
    with pytest.raises(RuntimeError, match="explicit bundle"):
        runner(_fake_run(), kubeconfig)


@pytest.mark.parametrize(
    ("filename", "expected_driver"),
    [
        ("agent.reference-stratus-r1c.v1.json", "reference_driver_r1c"),
        ("agent.reference-stratus-r1c-deepseek.v1.json", "reference_driver_r1c"),
        ("agent.reference-stratus-r1d.v1.json", "reference_driver_r1d"),
        ("agent.reference-stratus-r1e.v1.json", "reference_driver_r1e"),
        ("agent.reference-stratus-r0-panel.v1.json", "reference_driver"),
    ],
)
def test_registered_runner_variants_mount_their_pinned_protocol(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, filename: str, expected_driver: str
) -> None:
    secret = tmp_path / "agent-key"
    secret.write_text("not-a-real-key")
    secret.chmod(0o600)
    kubeconfig = tmp_path / "filtered-kubeconfig"
    kubeconfig.write_text("safe")
    calls: list[list[str]] = []

    def fake_run(command, **kwargs):
        calls.append(list(command))
        if command[:3] == ["docker", "image", "inspect"]:
            return SimpleNamespace(stdout="sha256:" + "f" * 64 + "\n")
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr("clawgym_overlay.reference_runner.subprocess.run", fake_run)
    from clawgym_overlay.reference_profiles import load_reference_agent_profile

    root = Path(__file__).resolve().parents[2] / "clawgym_overlay" / "manifests"
    path = root / filename
    profile = load_reference_agent_profile(root, profile_digest=sha256_digest(json.loads(path.read_text())))
    result = SafeStratusRunner(profile=profile, secret_file=secret)(
        SimpleNamespace(manifest_digest="f" * 64), str(kubeconfig)
    )
    assert result.image_digest == "f" * 64
    assert calls[1][-2:] == ["-m", expected_driver]


def test_runner_rejects_missing_kubeconfig_and_unidentified_image(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    secret = tmp_path / "agent-key"
    secret.write_text("key")
    secret.chmod(0o600)
    runner = SafeStratusRunner(
        profile={"model_id": "m", "api_base": "https://example.invalid", "artifact_id": "x", "command": ["python"]},
        secret_file=secret,
    )
    with pytest.raises(RuntimeError, match="kubeconfig"):
        runner(_fake_run(), str(tmp_path / "missing-kubeconfig"))
    kubeconfig = tmp_path / "kubeconfig"
    kubeconfig.write_text("safe")
    monkeypatch.setattr(
        "clawgym_overlay.reference_runner.subprocess.run",
        lambda *_args, **_kwargs: SimpleNamespace(stdout="not-a-digest"),
    )
    with pytest.raises(RuntimeError, match="SHA-256"):
        runner(_fake_run(), str(kubeconfig))


def test_runner_timeout_removes_container_and_returns_terminal_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    secret = tmp_path / "agent-key"
    secret.write_text("key")
    secret.chmod(0o600)
    kubeconfig = tmp_path / "kubeconfig"
    kubeconfig.write_text("safe")
    calls: list[list[str]] = []

    def fake_run(command, **kwargs):
        calls.append(list(command))
        if command[:3] == ["docker", "image", "inspect"]:
            return SimpleNamespace(stdout="sha256:" + "e" * 64)
        if command[:3] == ["docker", "rm", "-f"]:
            return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")
        raise __import__("subprocess").TimeoutExpired(
            command, kwargs["timeout"], output=b"partial", stderr=b"secretless"
        )

    monkeypatch.setattr("clawgym_overlay.reference_runner.subprocess.run", fake_run)
    runner = SafeStratusRunner(
        profile={"model_id": "m", "api_base": "https://example.invalid", "artifact_id": "x", "command": ["python"]},
        secret_file=secret,
    )
    result = runner(_fake_run(), str(kubeconfig))
    assert result.exit_code == 124
    assert "reference-agent-timeout" in result.transcript
    assert calls[-1][:3] == ["docker", "rm", "-f"]


def _fake_run() -> SimpleNamespace:
    return SimpleNamespace(manifest_digest="r" * 64, agent_release=SimpleNamespace(agent_release_digest="a" * 64))


def _handoff_fields() -> dict[str, object]:
    return {
        "symptom": "recommendation unavailable",
        "target_component": "recommendation",
        "evidence": ["endpoint unhealthy"],
        "root_cause_hypothesis": "network policy",
        "candidate_resource": {
            "kind": "NetworkPolicy",
            "namespace": "hotel-reservation",
            "name": "deny-all-recommendation",
        },
        "minimal_remediation": "delete the policy",
        "verification_plan": ["reread policy", "check endpoint"],
    }


def test_handoff_extractors_are_marker_bound_and_fail_closed() -> None:
    run = _fake_run()
    fields = _handoff_fields()
    r1c = reference_runner._extract_r1c_handoff(
        ({"name": "log.txt", "text": "R1C_HANDOFF_JSON " + json.dumps(fields)},), run
    )
    assert r1c["status"] == "complete"
    assert reference_runner._extract_r1c_handoff((), run)["status"] == "incomplete"
    assert reference_runner._extract_r1d_handoff((), run)["status"] == "incomplete"
    assert reference_runner._extract_r1e_handoff((), run)["status"] == "incomplete"
    malformed = ({"name": "log.txt", "text": "R1D_HANDOFF_JSON {bad"},)
    assert reference_runner._extract_r1d_handoff(malformed, run)["status"] == "incomplete"
    non_object = ({"name": "log.txt", "text": "R1D_HANDOFF_JSON []"},)
    assert reference_runner._extract_r1d_handoff(non_object, run)["status"] == "incomplete"
    complete_r1d = {
        "schema_id": "clawgym.sregym_diagnosis_handoff.v2",
        "status": "complete",
        "run_manifest_digest": run.manifest_digest,
        "agent_release_digest": run.agent_release.agent_release_digest,
        **fields,
    }
    complete_r1d["handoff_digest"] = reference_runner._digest_document(complete_r1d, "handoff_digest")
    parsed_r1d = reference_runner._extract_r1d_handoff(
        ({"name": "log.txt", "text": "R1D_HANDOFF_JSON " + json.dumps(complete_r1d)},), run
    )
    assert parsed_r1d["status"] == "complete"
    wrong_target = dict(
        fields, status="complete", candidate_resource={"kind": "Service", "namespace": "hotel-reservation", "name": "x"}
    )
    r1e = reference_runner._extract_r1e_handoff(
        ({"name": "log.txt", "text": "R1E_HANDOFF_JSON " + json.dumps(wrong_target)},), run
    )
    assert r1e["status"] == "incomplete"
    valid_r1e = dict(fields, status="complete")
    assert valid_r1e["candidate_resource"] == fields["candidate_resource"]
    parsed_r1e = reference_runner._extract_r1e_handoff(
        ({"name": "log.txt", "text": "R1E_HANDOFF_JSON " + json.dumps(valid_r1e)},), run
    )
    assert parsed_r1e["status"] == "complete"
    assert reference_runner._extract_r1f_handoff((), run)["status"] == "incomplete"

    explicit_r1f = {
        "schema_id": "clawgym.sregym_diagnosis_handoff.v2",
        "status": "complete",
        "run_manifest_digest": run.manifest_digest,
        "agent_release_digest": run.agent_release.agent_release_digest,
        **fields,
    }
    explicit_r1f["handoff_digest"] = reference_runner._digest_document(explicit_r1f, "handoff_digest")
    parsed_r1f = reference_runner._extract_r1f_handoff(
        ({"name": "r1f-handoff.json", "text": json.dumps(explicit_r1f)},), run
    )
    assert parsed_r1f["status"] == "complete"


def test_handoff_replay_rejects_malformed_variants_and_identity_drift() -> None:
    run = _fake_run()
    fields = _handoff_fields()
    malformed_r1c = dict(
        fields, candidate_resource="not-an-object", evidence="not-a-list", verification_plan="not-a-list"
    )
    parsed = reference_runner._extract_r1c_handoff(
        (
            {"name": "log.txt", "text": "R1C_HANDOFF_JSON {bad"},
            {"name": "log.txt", "text": "R1C_HANDOFF_JSON " + json.dumps(malformed_r1c)},
        ),
        run,
    )
    assert parsed["status"] == "complete"
    assert parsed["candidate_resource"] == {"kind": "", "namespace": "", "name": ""}
    assert parsed["evidence"] == []
    assert parsed["verification_plan"] == []

    invalid_r1d = dict(fields, status="complete", candidate_resource={"kind": "Service"})
    invalid_r1d.update(
        schema_id="clawgym.sregym_diagnosis_handoff.v2",
        run_manifest_digest=run.manifest_digest,
        agent_release_digest=run.agent_release.agent_release_digest,
    )
    assert (
        reference_runner._extract_r1d_handoff(
            ({"name": "log.txt", "text": "R1D_HANDOFF_JSON " + json.dumps(invalid_r1d)},), run
        )["status"]
        == "incomplete"
    )

    for text in ("{bad", json.dumps({"schema_id": "wrong"})):
        assert (
            reference_runner._extract_gate_event_journal(({"name": "r1f-gate-event-journal.json", "text": text},), run)
            is None
        )
    journal = {
        "schema_id": "clawgym.sregym_gate_event_journal.v1",
        "run_manifest_digest": run.manifest_digest,
        "agent_release_digest": run.agent_release.agent_release_digest,
        "state": {},
        "events": [],
    }
    journal["journal_digest"] = reference_runner._digest_document(journal, "journal_digest")
    assert (
        reference_runner._extract_gate_event_journal(
            ({"name": "r1f-gate-event-journal.json", "text": json.dumps(dict(journal, run_manifest_digest="f" * 64))},),
            run,
        )
        is None
    )


def test_action_ledger_and_transaction_projection_cover_success_and_failures() -> None:
    run = _fake_run()
    event = {
        "messages": [
            {
                "tool_calls": [
                    {
                        "id": "read",
                        "name": "kubectl",
                        "args": {"command": "kubectl get networkpolicy deny-all-recommendation -n hotel-reservation"},
                    },
                    {
                        "id": "mutate",
                        "name": "kubectl",
                        "args": {
                            "command": "kubectl delete networkpolicy deny-all-recommendation -n hotel-reservation"
                        },
                    },
                    {"id": "submit", "name": "submit_tool", "args": {"command": "submit"}},
                ]
            },
            {"tool_call_id": "read", "content": "ok"},
            {"tool_call_id": "mutate", "content": "error"},
            {"tool_call_id": "submit", "content": "forbidden"},
        ]
    }
    ledger = reference_runner._extract_action_ledger(({"name": "mitigation.jsonl", "text": json.dumps(event)},), run)
    assert ledger["summary"] == {"total": 3, "read": 1, "mutate": 1, "submit": 1, "unknown": 0, "executed_mutations": 0}
    assert ledger["records"][1]["result_summary"] == "error"
    handoff = dict(_handoff_fields(), status="complete", handoff_digest="h" * 64)
    projected = reference_runner._r1d_transaction(handoff, ledger, run)
    assert projected["status"] == "executed"
    assert reference_runner._r1d_verification_observation(ledger, run)["observations"] == []
    incomplete = reference_runner._r1d_transaction(
        {"status": "incomplete", "candidate_resource": {}}, {"records": []}, run
    )
    assert incomplete["status"] == "incomplete"


def test_receipt_parsers_reject_malformed_shapes_and_preserve_redacted_trajectory(tmp_path: Path) -> None:
    root = tmp_path / "trajectory"
    root.mkdir()
    (root / "events.jsonl").write_bytes(b"Authorization: Bearer abcdefghijklmnop\n")
    (root / "nested").mkdir()
    (root / "nested" / "event.txt").write_text("/var/run/docker.sock", encoding="utf-8")
    (root / "link").symlink_to(root / "events.jsonl")
    records = reference_runner._trajectory_records(root)
    assert {record["name"] for record in records} == {"events.jsonl", "nested/event.txt"}
    assert all("abcdefghijklmnop" not in record["text"] for record in records)
    assert reference_runner._dict_records("not-a-list") == []
    assert reference_runner._dict_records([{"id": 1}, "ignored", 3]) == [{"id": 1}]
    assert reference_runner._docker_mount_args(["a:ro", "b:rw"]) == ["-v", "a:ro", "-v", "b:rw"]
    invalid = tmp_path / "array.json"
    invalid.write_text("[]", encoding="utf-8")
    with pytest.raises(RuntimeError, match="JSON object"):
        reference_runner._load_json_object(invalid)


def test_action_ledger_handles_pending_duplicate_unknown_and_response_classes() -> None:
    run = _fake_run()
    records = (
        {
            "name": "diagnosis.jsonl",
            "text": json.dumps({"messages": [{"tool_call_id": "pending", "content": "ok"}]})
            + "\n"
            + json.dumps(
                {
                    "messages": [
                        {"tool_calls": [{"id": "no-id", "name": "x"}]},
                        {"tool_calls": [{"id": "pending", "name": "kubectl", "args": "{bad"}]},
                        {"tool_calls": [{"id": "unknown", "name": "other", "args": {}}]},
                        {
                            "tool_calls": [
                                {
                                    "id": "read",
                                    "name": "kubectl",
                                    "args": {"command": "kubectl get endpoints recommendation -n hotel-reservation"},
                                }
                            ]
                        },
                        {
                            "tool_calls": [
                                {
                                    "id": "mutate",
                                    "name": "kubectl",
                                    "args": {
                                        "command": "kubectl delete netpol deny-all-recommendation -n hotel-reservation"
                                    },
                                }
                            ]
                        },
                        {"tool_calls": [{"id": "submit", "name": "manual_submit_tool", "args": {}}]},
                        {
                            "tool_calls": [
                                {
                                    "id": "duplicate",
                                    "function": {"name": "kubectl", "arguments": {"command": "kubectl get pods"}},
                                }
                            ]
                        },
                    ]
                }
            )
            + "\n"
            + json.dumps(
                {
                    "messages": [
                        {"tool_call_id": "unknown", "content": "forbidden"},
                        {"tool_call_id": "read", "content": "addresses ready"},
                        {"tool_call_id": "mutate", "content": "error"},
                        {"tool_call_id": "submit", "content": "command rejected"},
                        {"tool_call_id": "duplicate", "content": "exception"},
                    ]
                }
            ),
        },
        {"name": "ignored.txt", "text": "not JSON"},
    )
    ledger = reference_runner._extract_action_ledger(records, run)
    assert ledger["summary"] == {
        "total": 7,
        "read": 2,
        "mutate": 1,
        "submit": 1,
        "unknown": 3,
        "executed_mutations": 0,
    }
    outcomes = [entry["outcome"] for entry in ledger["records"]]
    assert "unknown" in outcomes
    assert "rejected" in outcomes
    assert "executed" in outcomes
    assert "failed" in outcomes


def test_r1e_projection_and_gate_journal_identity() -> None:
    run = _fake_run()
    target = {"kind": "NetworkPolicy", "namespace": "hotel-reservation", "name": "deny-all-recommendation"}
    records = [
        {"operation": "read", "resource": target, "sequence": 1, "outcome": "executed", "result_summary": "present"},
        {
            "operation": "mutate",
            "resource": target,
            "sequence": 2,
            "outcome": "executed",
            "result_summary": "ok",
            "command_sha256": "c" * 64,
        },
        {"operation": "read", "resource": target, "sequence": 3, "outcome": "executed", "result_summary": "not_found"},
        {
            "operation": "read",
            "resource": {"kind": "Endpoints", "namespace": "hotel-reservation", "name": "recommendation"},
            "sequence": 4,
            "outcome": "executed",
            "result_summary": "ready",
        },
    ]
    handoff = dict(_handoff_fields(), status="complete", handoff_digest="h" * 64)
    transaction = reference_runner._r1e_transaction(handoff, {"records": records}, run)
    assert transaction["status"] == "executed"
    verification = reference_runner._r1e_verification_observation({"records": records}, run)
    assert {item["outcome"] for item in verification["observations"]} == {
        "target_present",
        "target_absent",
        "endpoint_ready",
    }
    journal = {
        "schema_id": "clawgym.sregym_gate_event_journal.v1",
        "run_manifest_digest": run.manifest_digest,
        "agent_release_digest": run.agent_release.agent_release_digest,
        "state": {"precondition_read": True, "target_reread": True, "endpoint_ready": True},
        "events": [],
    }
    journal["journal_digest"] = reference_runner._digest_document(journal, "journal_digest")
    assert reference_runner._extract_gate_event_journal(
        ({"name": "r1f-gate-event-journal.json", "text": json.dumps(journal)},), run
    )
    bad = dict(journal, journal_digest="0" * 64)
    assert (
        reference_runner._extract_gate_event_journal(
            ({"name": "r1f-gate-event-journal.json", "text": json.dumps(bad)},), run
        )
        is None
    )
