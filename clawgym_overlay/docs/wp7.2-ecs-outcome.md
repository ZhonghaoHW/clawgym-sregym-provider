# WP7.2 ECS sentinel outcome

The approved WP7.2 execution bridge was exercised against the fixed E0 fault
with the materialized Reference profile. Host readiness, reset/fault injection,
tool grant, recovery and cleanup completed successfully. The sentinel reached
the DeepSeek model, so this was not a gateway, Kind or image failure.

The semantic failure was at the diagnosis handoff boundary. The model produced
all useful fields as a bare JSON object, but omitted the literal
`R1F_HANDOFF_JSON` marker required by `r1f_protocol.normalise_handoff_submission`.
The adapter rejected the value fail-closed; therefore no host-validated handoff
was persisted, mitigation did not receive an authorized handoff, and no target
NetworkPolicy mutation was attempted. The host Oracle correctly returned
`fail`. This is a protocol/model-output compatibility failure, not evidence
that the environment was unhealthy.

The retained bundle and its provenance/attestation are archived outside Git by
the ClawGym/Evolution Lab handoff. This attempt is recorded as
`WP72_SEMANTIC_FAIL`; it does not change R0, R1n, E0, Oracle or the historical
runtime pins, and it does not authorize a retry, promotion or ZeroClaw.

Future profile work must make the marker requirement explicit in the model
output contract or use the already-tested host normalization path that accepts
the structured handoff without relying on transcript-tail inference. Any such
change requires a new immutable candidate and a new approval; this historical
attempt remains immutable evidence.
