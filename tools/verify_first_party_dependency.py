"""Create the Provider's auditable first-party dependency attestation."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from clawgym_overlay.first_party_dependency import (
    build_first_party_dependency_attestation,
    write_attestation_exclusive,
    write_sbom_exclusive,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="verify-first-party-dependency")
    parser.add_argument("--archive", required=True, type=Path)
    parser.add_argument("--package-name", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--upstream-url", required=True)
    parser.add_argument("--upstream-ref", required=True)
    parser.add_argument("--license-id", required=True)
    parser.add_argument("--dependency", action="append", default=[])
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--sbom-output", type=Path)
    args = parser.parse_args(argv)
    document = build_first_party_dependency_attestation(
        archive=args.archive,
        package_name=args.package_name,
        version=args.version,
        upstream_url=args.upstream_url,
        upstream_ref=args.upstream_ref,
        license_id=args.license_id,
        transitive_dependencies=args.dependency,
    )
    write_attestation_exclusive(document, args.output)
    if args.sbom_output is not None:
        write_sbom_exclusive(document, args.sbom_output)
    print(json.dumps({"status": "verified", "attestation_digest": document["attestation_digest"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
