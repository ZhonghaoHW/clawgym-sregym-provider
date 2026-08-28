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
