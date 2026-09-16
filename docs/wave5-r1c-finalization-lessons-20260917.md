# Wave 5 R1c finalization lesson

## Observed failure

The v47 GLM Reference sentinel reached the live SREGym environment and
completed cleanup, but the lifecycle was blocked at invocation. The retained
facts contained no accepted diagnosis handoff or mutation evidence. This is a
current failed attempt, not an Oracle verdict and not evidence against
ZeroClaw.

The root cause was a state-machine gap in the R1c adapter: the upstream
diagnosis graph treats an assistant message without tool calls as terminal.
When the model expressed a diagnosis as ordinary text after a read-only tool
call, the graph ended before the adapter's explicit `force_submit` node could
request the typed handoff. The adapter then correctly failed closed, but it had
no opportunity to complete the protocol.

## Root fix

R1c now routes only this exact non-submission terminal case to its existing
explicit finalization node. The finalization turn still requires a
model-authored, identity-bound `R1C_HANDOFF_JSON` payload and the existing
strict validator. Invalid or missing output raises a protocol failure before
the conductor or Oracle can be invoked. No diagnosis, target, mutation or
verdict is synthesized by the host.

This preserves the upstream runtime and makes the adapter protocol complete:

```text
read-only investigation
→ ordinary text or valid typed submit
→ explicit finalization when needed
→ validated handoff
→ bounded mitigation
→ host Oracle
```

## Regression evidence

The focused Provider suite passed after the fix:

```text
uv run pytest tests/clawgym_overlay/test_reference_driver_wrappers.py \
  tests/clawgym_overlay/test_reference_runner.py -q
70 passed in 31.54s
```

The regression tests prove that ordinary terminal text enters finalization,
tool-call paths are not rewritten, already-submitted states remain terminal,
and an invalid finalization remains fail-closed without calling the upstream
submission boundary.

The failed ECS identity remains immutable and is not retried. A new Provider
revision, release identity, packet and run identity are required before the
next Reference sentinel.
