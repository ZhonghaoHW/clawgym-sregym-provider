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

Provider `0c62f7373a9c4f4901abbcca3ca97e82a005ac11` separates the immutable
runtime protocol from the descriptive SOP variant. Materialized profiles now
pin `r1i-typed-handoff-journal-v1` and accept a complete, exact seven-field
structured `submit_tool.ans` JSON object without a transcript marker; the
legacy free-text path remains marker-required. The worker also accepts only an
explicit attempt request/ledger and writes a host-owned exclusive attempt
claim before lifecycle execution. These changes do not alter R0, R1n, E0,
Oracle or the frozen v9b evidence. The new candidate is not executed until a
human approval artifact binds its exact matrix and trial digests.

## Approved sentinel closure (2026-08-30)

The approved R1n-compatible candidate was run only after the fixed E0 control
passed. The first control attempt failed closed with
`filesystem_dependency_missing` because a fresh detached checkout had not
initialized the repository's pinned `SREGym-applications` submodule. The
submodule was initialized without changing the Provider commit, and the one
permitted control retry completed reset, fault, deterministic control repair,
Oracle, recovery and cleanup.

The R1n-compatible `case-001 / seed-2026082701` sentinel then completed via
the approved execution bridge. The retained bundle digest is
`9069cd3d531408d55c25ef762e83859d08448b114a78a1928ebabfb775790673`; the
episode digest is
`ab1e35d76a458bbf5932235af6c2796f1554afc3bc7154eada954fa7f74dbd00`. Runtime
evidence records a complete handoff, one exact NetworkPolicy mutation, target
absence reread, recommendation endpoint readiness, authoritative Oracle
`pass`, recovery and cleanup. The model invocation exited normally. This is a
successful single sentinel, not a three-case matrix or cross-agent result;
R0/R1n/E0 and all historical evidence remain unchanged.
