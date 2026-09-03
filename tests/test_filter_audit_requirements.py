from __future__ import annotations

from tools.filter_audit_requirements import filter_lines


def test_filter_removes_only_allowlisted_first_party_entries() -> None:
    lines = [
        "./scripts/geni_lib/mod/geni_lib_xlab-1.0.0.tar.gz\n",
        "clawgym @ git+https://github.com/ZhonghaoHW/clawgym.git@deadbeef\n",
        "requests==2.32.4\n",
        "# via clawgym\n",
    ]
    assert filter_lines(lines) == ["requests==2.32.4\n", "# via clawgym\n"]


def test_filter_preserves_unrelated_local_like_requirement() -> None:
    lines = ["example-internal==1.0.0\n", "./vendor/package.whl\n"]
    assert filter_lines(lines) == lines
