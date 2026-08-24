from __future__ import annotations

import hashlib
import io

import pytest

from clawgym_overlay.locked_runtime import LockedRuntimeError
from clawgym_overlay.materialize_lock import materialize_assets, preload_runtime_images
from test_deployment_lock import lock_fixture


def asset_payloads(document):
    payloads = {}
    for artifact in document["artifacts"]:
        if artifact["kind"] in {"manifest", "chart"}:
            payload = f"locked:{artifact['name']}".encode()
            payloads[artifact["source"]] = payload
            artifact["integrity"] = "sha256:" + hashlib.sha256(payload).hexdigest()
    return payloads


def test_materialize_assets_requires_empty_cache_and_verifies_every_digest(tmp_path) -> None:
    document = lock_fixture()
    payloads = asset_payloads(document)

    result = materialize_assets(document, tmp_path, opener=lambda url: io.BytesIO(payloads[url]))

    assert result["asset_count"] == len(payloads)
    assert {path.name for path in tmp_path.iterdir()} == {
        item["name"] for item in document["artifacts"] if item["kind"] in {"manifest", "chart"}
    }
    with pytest.raises(LockedRuntimeError, match="empty"):
        materialize_assets(document, tmp_path, opener=lambda url: io.BytesIO(payloads[url]))


def test_materialize_assets_removes_partial_cache_after_digest_failure(tmp_path) -> None:
    document = lock_fixture()
    payloads = asset_payloads(document)
    first = next(iter(payloads))
    payloads[first] = b"tampered"

    with pytest.raises(LockedRuntimeError, match="digest mismatch"):
        materialize_assets(document, tmp_path, opener=lambda url: io.BytesIO(payloads[url]))

    assert list(tmp_path.iterdir()) == []


def test_preload_images_uses_only_locked_sources_and_declared_targets() -> None:
    document = lock_fixture()
    calls = []

    result = preload_runtime_images(
        document,
        "clawgym-formal",
        runner=lambda command, **kwargs: calls.append((command, kwargs)),
    )

    images = [item for item in document["artifacts"] if item["name"].startswith("runtime-image.")]
    assert result["image_count"] == len(images)
    assert len(calls) == 3 * len(images)
    assert all(call[1]["check"] is True for call in calls)
    assert all("latest" not in call[0][2] for call in calls[::3])
