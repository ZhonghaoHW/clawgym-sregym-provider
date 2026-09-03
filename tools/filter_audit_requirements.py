"""Remove explicitly identified first-party packages from a pip-audit input.

The Provider lock contains local first-party archives and the ClawGym git
checkout.  They are audited through source attestations/SBOMs, not PyPI's
advisory index.  This filter is intentionally allowlisted and fails closed for
unknown formats instead of silently dropping arbitrary requirements.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

_INTERNAL = (
    re.compile(r"^\./scripts/geni_lib/"),
    re.compile(r"^geni-lib-xlab\s*@\s"),
    re.compile(r"^clawgym\s*@\s+git\+"),
    re.compile(r"^-e\s+\.$"),
    re.compile(r"^\./$"),
)


def filter_lines(lines: list[str]) -> list[str]:
    result: list[str] = []
    for line in lines:
        stripped = line.strip()
        if any(pattern.search(stripped) for pattern in _INTERNAL):
            continue
        result.append(line)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Filter first-party entries from pip-audit requirements")
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    lines = args.input.read_text(encoding="utf-8").splitlines(keepends=True)
    args.output.write_text("".join(filter_lines(lines)), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
