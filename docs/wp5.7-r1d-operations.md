# WP5.7 R1d Reference execution-protocol pilot

**Status:** local implementation and replay gates complete; remote execution gated on ECS

**Last verified:** 2026-08-28

R1d is a new immutable Reference Agent release derived directly from frozen
R0. R1c and its failed sentinel remain historical evidence and are not an R1d
parent. The release uses the existing E0, provider selection, image, filtered
kubeconfig/MCP boundary and lifecycle semantics.

The overlay implements an explicit sequence:

```text
observe → typed handoff → local validation → mitigation
→ one explicit bounded mutation → reread → existing read-only verification
→ host Oracle submission → recovery → cleanup
```

The typed handoff and mutation evidence are new versioned artifacts. The
overlay must never turn `awaiting_cleanup` into `done`, use transcript tails as
handoff data, or invoke mitigation without a validated handoff. Tool-call and
tool-result events are reduced only after all snapshots are joined by ID.

R1d may use only existing declared tools. Adding a probe, permission or
mutation verb changes the EnvironmentRelease and is outside this pilot. No
upstream SREGym file, ZeroClaw runtime, dynamic plugin or live cluster is
modified by the local implementation stage.

Local replay, fixture tests and fake-conductor tests are complete: the focused
provider gate is green and historical R1c markers classify as incomplete. A
future remote sentinel remains `same_family_provisional` and requires human
review; it cannot authorize WP8.
