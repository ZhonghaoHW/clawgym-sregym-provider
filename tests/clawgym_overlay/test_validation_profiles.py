from __future__ import annotations

import json
from pathlib import Path

import pytest

from clawgym.contracts import ContractValidationError, sha256_digest
from clawgym_overlay.validation_profiles import load_validation_profiles


ROOT = Path(__file__).resolve().parents[2]
MANIFESTS = ROOT / "clawgym_overlay" / "manifests"


def test_validation_profiles_fix_minimal_no_model_authority() -> None:
    adapter, sink = load_validation_profiles(MANIFESTS)
    assert adapter["lane"] == "environment_validation"
    assert adapter["model_access"] is False
    assert adapter["operation"] == "delete-network-policy"
    assert sink["write_mode"] == "exclusive-atomic-json"
    assert len(sha256_digest(adapter)) == 64
    assert len(sha256_digest(sink)) == 64


def test_validation_profile_cannot_expand_authority(tmp_path: Path) -> None:
    for source in MANIFESTS.glob("*.json"):
        (tmp_path / source.name).write_bytes(source.read_bytes())
    path = tmp_path / "agent.environment-validation.v1.json"
    document = json.loads(path.read_text())
    document["model_access"] = True
    path.write_text(json.dumps(document))
    with pytest.raises(ContractValidationError, match="fixed authority"):
        load_validation_profiles(tmp_path)
