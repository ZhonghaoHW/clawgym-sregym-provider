from __future__ import annotations

import subprocess

import pytest

from clawgym_overlay.provenance import ProviderProvenanceError, source_revision


def _git(path, *args: str, check: bool = True):
    return subprocess.run(["git", "-C", str(path), *args], check=check, capture_output=True, text=True)


def test_source_revision_requires_clean_detached_checkout(tmp_path) -> None:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.name", "WP5.8 Test")
    _git(tmp_path, "config", "user.email", "wp58@example.invalid")
    (tmp_path / "tracked.txt").write_text("baseline", encoding="utf-8")
    _git(tmp_path, "add", "tracked.txt")
    _git(tmp_path, "commit", "-q", "-m", "baseline")
    revision = _git(tmp_path, "rev-parse", "HEAD").stdout.strip()
    _git(tmp_path, "checkout", "-q", "--detach", revision)
    assert source_revision(tmp_path) == revision
    (tmp_path / "tracked.txt").write_text("dirty", encoding="utf-8")
    with pytest.raises(ProviderProvenanceError, match="dirty"):
        source_revision(tmp_path)


def test_source_revision_rejects_symbolic_head(tmp_path) -> None:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.name", "WP5.8 Test")
    _git(tmp_path, "config", "user.email", "wp58@example.invalid")
    (tmp_path / "tracked.txt").write_text("baseline", encoding="utf-8")
    _git(tmp_path, "add", "tracked.txt")
    _git(tmp_path, "commit", "-q", "-m", "baseline")
    with pytest.raises(ProviderProvenanceError, match="detached"):
        source_revision(tmp_path)
