from __future__ import annotations

from pathlib import Path

from tools.check_lockfile_hygiene import check_lockfile


def test_lockfile_hygiene_accepts_current_lockfile() -> None:
    assert check_lockfile(Path("uv.lock")) == []


def test_lockfile_hygiene_rejects_local_and_secret_content(tmp_path: Path) -> None:
    path = tmp_path / "uv.lock"
    path.write_text('path = "/tmp/local"\nkey = "sk-" + "12345678901234567890"\n', encoding="utf-8")
    failures = check_lockfile(path)
    assert any("forbidden pattern" in item for item in failures)
