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

## WP5.8 restart replay observed on ECS (2026-08-30)

The R1n runtime was replayed at the pinned Provider revision
`164f4924a99f98042adfd50a39772d1a0ed4f6ff`, with the no-model readiness
receipt and deterministic E0 control completed first. The three fixed runs used
the same E0 and seeds and were executed sequentially. Each invocation produced
a complete v2 handoff, exactly one delete of
`NetworkPolicy/hotel-reservation/deny-all-recommendation`, a target-absent
reread, a ready recommendation endpoint and a host-authoritative Oracle
`pass`; recovery and cleanup also succeeded. No provider source or release
was changed during replay.

The retained bundles and their source-bound provenance/attestation sidecars
are outside Git in the operator archive. This run confirms the provider's
runtime and observation contract is reusable on a restored worker; it does not
establish cross-agent conformance, change R0 or authorize environment
evolution. The final worker check found all four Kind nodes Ready, no test
namespace, no agent container and no temporary transfer archive.

## Provider test-gate closure lessons (2026-08-30)

The first post-replay local run reported `637 passed, 5 failed, 2 skipped`.
Those failures were deterministic test-contract problems, not intermittent
R1n, E0, model, Oracle or cluster behavior:

1. The OpenEBS smoke test correctly failed closed because the local Kind node
   did not contain the required `/run/udev` mount. This is a live host-capability
   failure and must not be counted as an offline unit-test failure or hidden by
   a permissive skip.
2. The kubectl tool helper created the SSE session header but omitted the
   required `session_id` argument when constructing `ExecKubectlCmdSafely`.
   One generated session identity is now passed to both surfaces.
3. Two HotelReservation rendering tests used `__new__` and bypassed the
   constructor's `deployment_image_overrides` field. They now use the real
   constructor so required state cannot silently disappear.
4. The train-ticket API test performs gateway discovery during module import.
   It is an upstream live-application test, not a Provider offline test, and is
   outside the default Provider test path. It remains explicitly runnable only
   with a configured `GATEWAY_URL` (or a live Kubernetes service).
5. The kubectl fixture validator discarded the command output even though
   its YAML expressions refer to that output as `opt`. This produced a
   deterministic `NameError` before any tool assertion ran. The validator now
   binds `opt` explicitly and evaluates only the documented fixture
   expression surface; this is a test-fixture repair, not an agent or Oracle
   capability change.

The ECS replay also established an important ordering rule: run the offline
gate first, then explicitly selected live suites on the target host. A live
suite can expose stale helper assumptions that local collection cannot see;
such failures must be fixed in the helper contract and replayed, never
converted into skips or folded into R1n evidence.

The Provider test contract is split into four explicit layers:

```text
uv run pytest
    offline Provider gate; no cluster, gateway or external application
uv run pytest -o addopts='' tests/integration -m integration
    live Kind/OpenEBS capability gate; fail closed on missing host state
uv run pytest -o addopts='' tests/kubectl_tool_tests -m integration
    live Kubernetes/MCP tool gate
uv run pytest -o addopts='' tests/llm_as_a_judge -m slow
    live model-judge gate; requires JUDGE_MODEL_ID
```

This separation makes a green offline gate meaningful without weakening the
live environment contract. Slow model-judge tests are excluded even when their
environment variable is present. It is test hygiene only; it does not change
R1n, the `164f…` runtime, E0, the Oracle or retained evidence.
