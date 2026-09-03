import json
from pathlib import Path

import pytest

from clawgym_overlay.platform_observation import (
    PlatformObservationError,
    build_platform_host_observation,
    collect_platform_host_observation,
    require_clean_observation,
)

CHECKS = {
    "nodes_ready": True,
    "baseline_namespaces_only": True,
    "agent_containers_absent": True,
    "leases_absent": True,
    "candidate_resources_absent": True,
    "temporary_access_material_absent": True,
}


def test_host_observation_is_digest_bound_and_clean() -> None:
    observation = build_platform_host_observation(
        collector_revision="a" * 40, source_digest="b" * 64, checks=CHECKS, observed_at="2026-09-01T00:00:00+08:00"
    )
    assert require_clean_observation(observation)["observation_digest"] == observation["observation_digest"]


def test_host_observation_rejects_false_and_tampering() -> None:
    checks = dict(CHECKS)
    checks["leases_absent"] = False
    observation = build_platform_host_observation(
        collector_revision="a" * 40, source_digest="b" * 64, checks=checks, observed_at="2026-09-01T00:00:00+08:00"
    )
    with pytest.raises(PlatformObservationError):
        require_clean_observation(observation)
    tampered = dict(observation)
    tampered["observed_at"] = "2026-09-01T00:01:00+08:00"
    with pytest.raises(PlatformObservationError):
        require_clean_observation(tampered)


def test_collector_reads_only_explicit_sanitized_source(tmp_path) -> None:
    source = tmp_path / "checks.json"
    source.write_text(json.dumps(CHECKS, sort_keys=True, separators=(",", ":")), encoding="utf-8")
    observation = collect_platform_host_observation(source_path=source, collector_revision="a" * 40)
    assert observation["source_digest"]
    source.write_text(json.dumps({**CHECKS, "secret": "redacted"}), encoding="utf-8")
    with pytest.raises(PlatformObservationError):
        collect_platform_host_observation(source_path=source, collector_revision="a" * 40)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"collector_revision": "not-a-revision"},
        {"source_digest": "not-a-digest"},
        {"checks": {**CHECKS, "extra": True}},
        {"checks": {**CHECKS, "nodes_ready": 1}},
        {"observed_at": "2026-09-01\n00:00:00"},
    ],
)
def test_host_observation_rejects_untrusted_shape(kwargs: dict[str, object]) -> None:
    values: dict[str, object] = {
        "collector_revision": "a" * 40,
        "source_digest": "b" * 64,
        "checks": CHECKS,
        "observed_at": "2026-09-01T00:00:00+08:00",
    }
    values.update(kwargs)
    with pytest.raises(PlatformObservationError):
        build_platform_host_observation(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "value",
    [
        {},
        {"schema_id": "wrong", "checks": CHECKS},
        {"schema_id": "clawgym.platform_host_observation.v1"},
        {"schema_id": "clawgym.platform_host_observation.v1", "checks": {}},
    ],
)
def test_require_clean_observation_rejects_missing_identity_or_checks(value: dict[str, object]) -> None:
    with pytest.raises(PlatformObservationError):
        require_clean_observation(value)


def test_collector_rejects_missing_directory_symlink_and_invalid_json(tmp_path: Path) -> None:
    with pytest.raises(PlatformObservationError):
        collect_platform_host_observation(source_path=tmp_path / "missing", collector_revision="a" * 40)
    directory = tmp_path / "directory"
    directory.mkdir()
    with pytest.raises(PlatformObservationError):
        collect_platform_host_observation(source_path=directory, collector_revision="a" * 40)
    invalid = tmp_path / "invalid"
    invalid.write_bytes(b"not-json")
    with pytest.raises(PlatformObservationError):
        collect_platform_host_observation(source_path=invalid, collector_revision="a" * 40)
    non_object = tmp_path / "array"
    non_object.write_text("[]", encoding="utf-8")
    with pytest.raises(PlatformObservationError):
        collect_platform_host_observation(source_path=non_object, collector_revision="a" * 40)
    target = tmp_path / "target"
    target.write_text(json.dumps(CHECKS), encoding="utf-8")
    link = tmp_path / "link"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symlinks are unavailable on this filesystem")
    with pytest.raises(PlatformObservationError):
        collect_platform_host_observation(source_path=link, collector_revision="a" * 40)
