from __future__ import annotations

import os
from pathlib import Path

import pytest

from clawgym_overlay.reference_runner import (
    ReferenceAgentSecretError,
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
    safe = _safe_text(b"Authorization: Bearer abcdefghijklmnop /var/run/example")
    assert "abcdefghijklmnop" not in safe
    assert "/var/run/example" not in safe
    assert "[REDACTED]" in safe
    assert "[HOST_PATH]" in safe
