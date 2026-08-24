"""Provenance checks for the pinned ClawGym SREGym provider baseline."""

from __future__ import annotations

import hashlib
import json
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = ROOT / "clawgym_overlay" / "upstream-baseline.json"
ALLOWED_OVERLAY_PATHS = (
    "AGENTS.md",
    "clawgym_overlay/",
    "tests/clawgym_overlay/",
)


def git(*args: str, cwd: Path = ROOT) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


class UpstreamBaselineTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        cls.upstream_revision = cls.manifest["upstream"]["revision"]

    def test_manifest_has_expected_identity(self) -> None:
        self.assertEqual(self.manifest["schema_id"], "clawgym.sregym_upstream_baseline.v1")
        self.assertEqual(
            self.manifest["upstream"]["repository"],
            "https://github.com/SREGym/SREGym.git",
        )
        self.assertEqual(
            git("rev-parse", f"{self.upstream_revision}^{{tree}}"),
            self.manifest["upstream"]["tree"],
        )

    def test_license_is_unmodified(self) -> None:
        license_record = self.manifest["license"]
        content = (ROOT / license_record["path"]).read_bytes()
        self.assertEqual(hashlib.sha256(content).hexdigest(), license_record["sha256"])
        self.assertEqual(git("diff", "--name-only", self.upstream_revision, "--", "LICENSE.txt"), "")

    def test_recursive_submodules_match_manifest(self) -> None:
        status_lines = git("submodule", "status", "--recursive").splitlines()
        self.assertTrue(status_lines)
        for line in status_lines:
            self.assertNotIn(line[0], "-+U")

        for record in self.manifest["submodules"]:
            submodule_root = ROOT / record["path"]
            actual = git("rev-parse", "HEAD", cwd=submodule_root)
            self.assertEqual(actual, record["revision"], record["path"])
            actual_repository = git("remote", "get-url", "origin", cwd=submodule_root)
            self.assertEqual(
                actual_repository.removesuffix(".git").rstrip("/"),
                record["repository"].removesuffix(".git").rstrip("/"),
                record["path"],
            )

    def test_provider_history_descends_from_pin(self) -> None:
        subprocess.run(
            ["git", "merge-base", "--is-ancestor", self.upstream_revision, "HEAD"],
            cwd=ROOT,
            check=True,
        )
        descendants = git(
            "rev-list",
            "--ancestry-path",
            "--reverse",
            f"{self.upstream_revision}..HEAD",
        ).splitlines()
        if descendants:
            self.assertEqual(git("rev-parse", f"{descendants[0]}^"), self.upstream_revision)

    def test_changes_from_pin_stay_in_overlay_namespaces(self) -> None:
        changed = git("diff", "--name-only", f"{self.upstream_revision}..HEAD").splitlines()
        explicitly_controlled = set(self.manifest["controlled_root_changes"])
        core_patches = {record["path"] for record in self.manifest["core_patches"]}
        for path in changed:
            self.assertTrue(
                path in explicitly_controlled
                or path in core_patches
                or any(path == prefix or path.startswith(prefix) for prefix in ALLOWED_OVERLAY_PATHS),
                path,
            )

    def test_core_patches_are_auditable(self) -> None:
        for record in self.manifest["core_patches"]:
            self.assertEqual(
                set(record),
                {"path", "rationale", "tests", "sha256"},
            )
            self.assertTrue(record["rationale"].strip())
            self.assertTrue(record["tests"])
            content = (ROOT / record["path"]).read_bytes()
            self.assertEqual(hashlib.sha256(content).hexdigest(), record["sha256"])
            self.assertTrue((ROOT / record["rationale"]).is_file(), record["rationale"])
            for test_path in record["tests"]:
                self.assertTrue((ROOT / test_path).is_file(), test_path)


if __name__ == "__main__":
    unittest.main()
