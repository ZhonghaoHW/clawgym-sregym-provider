"""Consumer-side admission checks for the released G04 schema bundle.

This test deliberately stays in the provider repository's test namespace.  It
does not import ClawGym or register a second runtime schema authority.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

FIXTURE = Path(__file__).parent / "fixtures" / "g04_public_schema_bundle" / "clawgym-g04-public-v1"
MANIFEST = FIXTURE / "bundle.json"
SENSITIVE = re.compile(r"(?:sk-|api[_-]?key|bearer\s|token\b|kubectl\b|(?:/Users|/home)/)", re.IGNORECASE)


def _canonical_bytes(document: object) -> bytes:
    return json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _load(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def _verify_bundle(document: dict[str, object]) -> None:
    schema = _load(FIXTURE / "released" / "clawgym.public_schema_bundle.v1.schema.json")
    Draft202012Validator(schema).validate(document)
    unsigned = dict(document)
    declared_digest = unsigned.pop("bundle_digest")
    assert hashlib.sha256(_canonical_bytes(unsigned)).hexdigest() == declared_digest

    refs = document["schema_refs"]
    assert isinstance(refs, list) and refs
    for ref in refs:
        assert isinstance(ref, dict)
        source = FIXTURE / ref["source_path"]
        released = FIXTURE / ref["released_path"]
        source_bytes = source.read_bytes()
        released_bytes = released.read_bytes()
        assert source_bytes == released_bytes
        digest = hashlib.sha256(source_bytes).hexdigest()
        assert digest == ref["source_sha256_digest"] == ref["released_sha256_digest"]
        assert _load(source)["$id"] == ref["schema_id"]


def _reject_sensitive_content(document: object) -> None:
    if SENSITIVE.search(json.dumps(document, ensure_ascii=False)):
        raise ValueError("sensitive content is not admitted")


def test_g04_public_bundle_passes_offline_admission() -> None:
    document = _load(MANIFEST)
    assert isinstance(document, dict)
    _reject_sensitive_content(document)
    _verify_bundle(document)
    assert document["bundle_digest"] == "7695e77f6df52c0e92ea3a9aabe9a9cb09e736e7a1a974e5fbec492bd081bc43"


@pytest.mark.parametrize(
    ("fixture_name", "expected_marker"),
    [
        ("reject-unknown-version.json", "clawgym.public_schema_bundle.v2"),
        ("reject-unknown-field.json", "unexpected"),
        ("reject-identity-or-digest-mismatch.json", "source_sha256_digest"),
    ],
)
def test_g04_public_bundle_rejects_structural_fixture(fixture_name: str, expected_marker: str) -> None:
    document = _load(FIXTURE / "rejections" / fixture_name)
    assert expected_marker in json.dumps(document)
    with pytest.raises((ValidationError, AssertionError)):
        _verify_bundle(document)


def test_g04_public_bundle_rejects_sensitive_fixture() -> None:
    document = _load(FIXTURE / "rejections" / "reject-sensitive-content.json")
    with pytest.raises(ValueError, match="sensitive content"):
        _reject_sensitive_content(document)
