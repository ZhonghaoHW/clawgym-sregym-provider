# WP8.1 environment qualification operations

The qualification backend is host-owned and model-free. It runs only explicit
recipe and release artifacts and emits redacted phase observations. The
Provider supplies SREGym reset/provision/inject/recover/cleanup hooks; ClawGym
owns leases, Oracle calls, attempt claims and evidence export.

The required state sequence is:

```text
baseline → injected → recovered → cleaned
```

For the fixed NetworkPolicy candidate, the expected Oracle sequence is
`pass → fail → pass → pass`. The injected `fail` is an expected fault-state
observation, not a candidate failure. The target is always
`NetworkPolicy/hotel-reservation/deny-all-recommendation`; non-target health,
filtered tool permissions and candidate ownership labels are checked at every
trial.

Provider callbacks are not trusted evidence envelopes. Phase receipts retain
only typed health/oracle booleans and a digest of an optional provider summary;
commands, paths, tool output, credentials and host identifiers are never
serialized into the qualification artifact.

WP8.1 runs two same-runtime control repetitions and three candidate
repetitions, serially, with a 1800-second lease. Each attempt has a new claim
and evidence root. Infrastructure failures may be retried only within the
declared budget; semantic mismatches and cleanup failures are retained and
fail closed. No Chaos Mesh, LitmusChaos or K8sGPT runtime is installed: their
state/probe/target-boundary practices are represented by first-party,
digest-bound controls.

## ECS readiness evidence (2026-09-01)

With Provider `bb4c708aed965b3bc2d537e75abcba00928007f9` and ClawGym
`69496a4d368af96b3eeb334a935f351080504037`, two corrected no-model E0.1
controls completed successfully. Their bundle, provenance-v2 and signed
attestation files are archived outside Git under
`_artifacts/wp8-reference-environment/wp81/ingress-only-qualification-v1/`.
The first attempt remains retained as an infrastructure dependency failure
(missing `kubernetes` module). At that time the five qualification trials had
not been run because the worker exported the legacy environment-validation
lane; the live exporter and final matrix result are recorded below.

## Qualification runner result (2026-09-01)

The explicit `clawgym_overlay.qualification_runner` is the model-free live
composition root for this lane. It accepts only an explicit trial and the
allowlisted component bundle, uses hard-coded target operations, and emits
typed state/tool/isolation artifacts. It never accepts candidate commands,
paths, YAML or executable callbacks. Provider revision
`b041cd3bc7d552d402d28892e469fbf54471274b` fixed the isolation attestation to
carry control-namespace ownership, which ClawGym verifies before aggregation.

With that revision, ECS ran two same-runtime controls and three
`ingress_only` candidate repetitions serially. All five completed with the
expected `pass → fail → pass → pass` Oracle sequence, healthy non-targets,
usable declared tools, isolated ownership and successful cleanup. The first
run set produced before the attestation fix remains retained but excluded;
it is not silently rewritten. Remote runner output was downloaded outside Git
and passed the ClawGym explicit-path verifier and Evolution Lab report v2.
