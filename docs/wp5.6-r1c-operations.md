# WP5.6 R1c operations

**Status:** closed as a sentinel-gated failed pilot; no promotion

R1c is an immutable Reference Agent experiment using the explicitly registered
`openai/glm-5.3-flash` profile. Its Chat Completions preflight returned HTTP
404, so a separately identified DeepSeek V4 Pro fallback profile was used; the
two releases are not interchangeable. Both use the fixed E0 environment, the
existing tool boundary, a 900-second container bound and eight
diagnosis/mitigation steps.

The fallback sentinel for `case-001 / seed-2026082701` completed reset, fault,
invocation, Oracle, recovery and cleanup, but the authoritative Oracle verdict
was `fail`. Its diagnosis handoff was `incomplete` and the action ledger had
zero attributable actions, so cases 002/003 were correctly stopped. No legal
three-pair report, review packet or promotion record exists for WP5.6.

The wrapper is mounted only for the explicitly registered R1c digest and does
not alter R0, R1, R1b7 or upstream SREGym. No candidate or provider code is
dynamically loaded, and Evolution Lab never starts a remote run.

## Root-cause correction for the next requalification

The v46 failure is an agent-handoff failure, not evidence that the Reference
runtime or ZeroClaw is unsuitable. The process exited zero and performed a
mutation, but the retained trajectory contained no diagnosis handoff emitted
by the model. The previous R1c wrapper then allowed a bounded fallback summary
to reach mitigation. That made a process result look complete while the
host-owned diagnosis contract was incomplete.

The corrective boundary is now explicit and shared by the container driver and
the host runner. A model submission must contain the exact
`R1C_HANDOFF_JSON` marker, all seven non-empty semantic fields, the registered
NetworkPolicy target, and the current run and AgentRelease identities. The
driver validates this before calling the benchmark submit tool and writes one
canonical handoff artifact. A missing or rejected handoff cannot start
mitigation; the bounded finalization path fails closed instead of inventing an
incomplete marker. The host adapter also treats a non-complete diagnosis
handoff as a failed invocation even when the process exit code is zero.

The `agent.env` file is an ephemeral credential input and is excluded from
trajectory retention. Other logs continue to use the existing redaction
boundary. This correction requires a fresh Provider revision and fresh
RunManifest/evidence identities before the R1c sentinel is run again; the v46
failure remains immutable historical evidence.
