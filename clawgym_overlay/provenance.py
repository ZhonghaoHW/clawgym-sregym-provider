"""Provider-side source identity for host-created provenance receipts."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path


class ProviderProvenanceError(ValueError):
    """Raised when the provider checkout cannot supply a stable revision."""


_REVISION = re.compile(r"^[0-9a-f]{40}$")


def source_revision(repository: str | Path) -> str:
    """Return the exact clean detached Git revision for this provider."""

    root = Path(repository)
    if not root.is_dir() or root.is_symlink():
        raise ProviderProvenanceError("provider repository is not a regular directory")
    try:
        revision = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain=v1", "--untracked-files=all"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        symbolic = subprocess.run(
            ["git", "-C", str(root), "symbolic-ref", "-q", "HEAD"],
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ProviderProvenanceError("provider repository is not a valid Git checkout") from exc
    if not _REVISION.fullmatch(revision):
        raise ProviderProvenanceError("provider revision is not a complete lowercase SHA")
    if dirty:
        raise ProviderProvenanceError("provider checkout is dirty")
    if symbolic.returncode == 0:
        raise ProviderProvenanceError("provider checkout must be detached")
    return revision
