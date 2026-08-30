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
## Protocol repair candidate (2026-08-30)

Provider `d441905dabf053c80c986c3403c256832fe08df3` separates the immutable
runtime protocol from the descriptive SOP variant. Materialized profiles now
pin `r1i-typed-handoff-journal-v1` and accept a complete, exact seven-field
structured `submit_tool.ans` JSON object without a transcript marker; the
legacy free-text path remains marker-required. The worker also accepts only an
explicit attempt request/ledger and writes a host-owned exclusive attempt
claim before lifecycle execution. These changes do not alter R0, R1n, E0,
Oracle or the frozen v9b evidence. The new candidate is not executed until a
human approval artifact binds its exact matrix and trial digests.
