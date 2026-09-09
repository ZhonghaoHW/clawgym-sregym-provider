from __future__ import annotations

import re
from pathlib import Path

_CLAWGYM_REVISION = re.compile(
    r'"clawgym\s+@\s+git\+https://github\.com/ZhonghaoHW/clawgym\.git@([0-9a-f]{40,64})"'
)
_LOCKED_CLAWGYM_REVISION = re.compile(
    r'source = \{ git = "https://github\.com/ZhonghaoHW/clawgym\.git\?rev=([0-9a-f]{40,64})#([0-9a-f]{40,64})" \}'
)


def test_clawgym_dependency_and_lockfile_pin_the_same_published_revision() -> None:
    root = Path(__file__).parents[1]
    pyproject = (root / "pyproject.toml").read_text(encoding="utf-8")
    lockfile = (root / "uv.lock").read_text(encoding="utf-8")
    pyproject_match = _CLAWGYM_REVISION.search(pyproject)
    lockfile_match = _LOCKED_CLAWGYM_REVISION.search(lockfile)
    assert pyproject_match is not None, "pyproject.toml must pin ClawGym to a full revision"
    assert lockfile_match is not None, "uv.lock must retain the same ClawGym Git source"
    declared = pyproject_match.group(1)
    assert lockfile_match.group(1) == declared
    assert lockfile_match.group(2) == declared
