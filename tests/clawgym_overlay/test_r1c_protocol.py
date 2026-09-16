from __future__ import annotations

import json

from clawgym_overlay.r1c_protocol import (
    MARKER,
    TARGET,
    incomplete_handoff,
    marker_payload,
    normalise_payload,
    validate_document,
)

RUN = "a" * 64
RELEASE = "b" * 64


def _payload() -> dict[str, object]:
    return {
        "symptom": "recommendation unavailable",
        "target_component": "recommendation",
        "evidence": ["endpoint unhealthy"],
        "root_cause_hypothesis": "network policy",
        "candidate_resource": dict(TARGET),
        "minimal_remediation": "delete the policy",
        "verification_plan": ["reread policy", "check endpoint"],
    }


def test_r1c_protocol_normalises_and_validates_one_canonical_document() -> None:
    document = normalise_payload(_payload(), run_manifest_digest=RUN, agent_release_digest=RELEASE)
    assert document is not None
    assert document["handoff_digest"]
    assert validate_document(document, run_manifest_digest=RUN, agent_release_digest=RELEASE)


def test_r1c_protocol_redacts_before_digest_and_preserves_collected_validation() -> None:
    payload = _payload()
    payload["target_component"] = "recommendation pod 10.20.1.27 at /var/run/example"
    payload["evidence"] = ["Authorization: Bearer abcdefghijklmnop", "endpoint 10.20.1.27"]
    document = normalise_payload(payload, run_manifest_digest=RUN, agent_release_digest=RELEASE)
    assert document is not None
    assert "10.20.1.27" not in document["target_component"]
    assert "abcdefghijklmnop" not in document["evidence"][0]
    assert validate_document(document, run_manifest_digest=RUN, agent_release_digest=RELEASE)


def test_r1c_protocol_rejects_unknown_fields_identity_drift_and_wrong_target() -> None:
    extra = dict(_payload(), unknown="must reject")
    assert normalise_payload(extra, run_manifest_digest=RUN, agent_release_digest=RELEASE) is None
    assert normalise_payload(_payload(), run_manifest_digest="short", agent_release_digest=RELEASE) is None
    assert (
        normalise_payload(
            dict(_payload(), candidate_resource={"kind": "Service"}),
            run_manifest_digest=RUN,
            agent_release_digest=RELEASE,
        )
        is None
    )
    document = normalise_payload(_payload(), run_manifest_digest=RUN, agent_release_digest=RELEASE)
    assert document is not None
    assert not validate_document(document, run_manifest_digest=RUN, agent_release_digest="c" * 64)


def test_r1c_marker_and_incomplete_documents_are_deterministic() -> None:
    encoded = json.dumps(_payload(), separators=(",", ":"))
    assert marker_payload(f"{MARKER}\n{encoded}") == _payload()
    assert marker_payload(f"prefix {MARKER} {encoded}") is None
    assert marker_payload(f"{MARKER} {encoded} trailing") is None
    first = incomplete_handoff(run_manifest_digest=RUN, agent_release_digest=RELEASE)
    second = incomplete_handoff(run_manifest_digest=RUN, agent_release_digest=RELEASE)
    assert first == second
    assert first["status"] == "incomplete"
