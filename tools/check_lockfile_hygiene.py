"""Validate that a checked-in uv.lock contains no host-local or secret data."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

_FORBIDDEN = (
    re.compile(r"(?im)^\s*(?:path|editable)\s*=\s*['\"](?:/|~|[A-Za-z]:[\\/])"),
    re.compile(r"(?i)file://"),
    re.compile(r"(?i)(?:sk-[A-Za-z0-9]{16,}|AKIA[0-9A-Z]{16}|BEGIN (?:RSA|OPENSSH|EC) PRIVATE KEY)"),
)


def check_lockfile(path: Path) -> list[str]:
    if path.is_symlink() or not path.is_file():
        return [f"lockfile is not a regular file: {path}"]
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return [f"lockfile cannot be read: {exc}"]
    failures: list[str] = []
    if "\x00" in text or "\r" in text:
        failures.append("lockfile contains NUL or CR bytes")
    for pattern in _FORBIDDEN:
        if pattern.search(text):
            failures.append(f"lockfile matches forbidden pattern: {pattern.pattern}")
    return failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="check-lockfile-hygiene")
    parser.add_argument("lockfile", type=Path)
    args = parser.parse_args(argv)
    failures = check_lockfile(args.lockfile)
    if failures:
        for failure in failures:
            print(failure, file=sys.stderr)
        return 1
    print(f"lockfile hygiene passed: {args.lockfile}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
