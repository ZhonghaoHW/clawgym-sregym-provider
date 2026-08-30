import os
from pathlib import Path

import pytest

from clawgym_overlay.worker import prepare_runtime_workdir


def test_runtime_workdir_is_private_and_empty(tmp_path: Path) -> None:
    target = tmp_path / "attempt"
    original = os.environ.get("TMPDIR")
    try:
        result = prepare_runtime_workdir(target)
        assert result == target
        assert target.stat().st_mode & 0o777 == 0o700
        assert os.environ["TMPDIR"] == str(target)
    finally:
        if original is None:
            os.environ.pop("TMPDIR", None)
        else:
            os.environ["TMPDIR"] = original


def test_runtime_workdir_rejects_reuse(tmp_path: Path) -> None:
    target = tmp_path / "attempt"
    target.mkdir()
    (target / "stale").write_text("x", encoding="utf-8")
    with pytest.raises(ValueError, match="new empty"):
        prepare_runtime_workdir(target)
