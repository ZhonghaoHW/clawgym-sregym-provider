# WP5.7 R1e runtime-gated operations

R1e is an immutable R0-derived profile. It keeps the existing model gateway,
image, E0 and filtered tools, while adding an adapter-local fail-closed gate.
Diagnosis must emit a complete identity-bound `clawgym.sregym_diagnosis_handoff.v2`.
Mitigation then performs one exact delete of
`NetworkPolicy/hotel-reservation/deny-all-recommendation`, rereads the target,
checks recommendation endpoint readiness and only then permits submit.

The profile mounts four static config files read-only and uses an explicit
900-second timeout with one mitigation attempt. Compound commands, diagnosis
mutations, cross-resource changes, retries and early submit are rejected. Gate
failure emits a lifecycle marker so host recovery and cleanup still run; it is
never reported as agent success. R1d and all historical evidence remain
unchanged. Remote execution is paused until local gates pass and the operator
authorizes ECS.

## WP5.8 remote closure lessons (2026-08-29)

The R1n replay used this provider at the immutable detached revision
`164f4924a99f98042adfd50a39772d1a0ed4f6ff`. The revision is recorded in each
bundle's `host/source-checkouts.json`; the provider tree is therefore
reproducible from Git, while remote logs and credentials remain outside Git.
Downloaded E0/R1n bundles, provenance-v2 receipts and signed attestations are
archived under the operator-controlled `_artifacts/wp5-reference-evolution/`
directory and are consumed by path, not by an implicit provider workspace.

The successful run followed these provider-facing interface rules:

```text
validated handoff
→ exact pre-read of NetworkPolicy/hotel-reservation/deny-all-recommendation
→ one explicit delete mutation
→ target reread (NotFound)
→ recommendation endpoint read (address + port 8085)
→ gated submit
```

The key repair was observation parsing, not a model or Oracle change. The
Kubernetes endpoint command emits both compact `address:8085` and tabular
`address 8085/TCP` forms. The R1n parser accepts these known structured forms,
requires a non-empty address and the fixed port, and rejects `<none>`,
port-only and arbitrary “ready” text. Action execution and observation parsing
are recorded separately, so a parser failure cannot erase a mutation that
really happened. The provider must continue to run recovery and cleanup after
an invocation, timeout or gate failure; `awaiting_cleanup` is host state and
must never be converted to agent success.

Future adapters may use a different model or tool client, but they must enter
through the same declared AgentAdapter lifecycle and export identity-bound
receipts. They may not add permissions, load candidate executables, patch
upstream SREGym files or self-report the Oracle verdict. These constraints are
the provider-side contract for the later independent ZeroClaw adapter.
