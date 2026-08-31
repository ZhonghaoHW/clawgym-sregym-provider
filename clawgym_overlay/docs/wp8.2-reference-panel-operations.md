# WP8.2 Reference panel operations

WP8.1 qualified the `ingress_only` candidate without an Agent. WP8.2 is the
separate ECS measurement lane: the frozen R0 control and active Reference
release are run against a same-runtime E0.1 control and the qualified
candidate. The Provider receives only explicit, digest-bound RunManifests and
returns released evidence; it does not choose candidates or interpret the
panel report.

The matrix is two agents × two environment releases × three cases × two
repetitions. Case and seed are identical across both agents and both
environment releases. Runs are strictly serial and each attempt has an
exclusive claim. A missing provenance/attestation, non-authoritative Oracle,
incomplete evidence, or failed cleanup is fail-closed and cannot be hidden by
the panel aggregator.

The Provider must keep R0 and the active Reference runtime behavior unchanged,
use the same locked image/tool boundary, and retain all raw bundles outside
Git. The panel result is `reference_environment_only`; it is a measurement of
environment effect and agent outcomes, not a catalog promotion or
cross-agent-conformance result.

The panel plan records a provider runtime revision per agent role. R0 remains
on its frozen historical revision while the active Reference release uses its
own pinned runtime; the worker must not silently substitute one for the other.
Each panel trial must retain the provider revision used for that agent role so
offline aggregation can reject a runtime substitution.

Plans with a single provider revision or placeholder ClawGym SHA were rejected
before execution and remain retained as superseded preparation. The corrected
plan binds R0 Provider `cbe7b548…`, active Reference Provider `b3111a7…`, and
ClawGym `d75039e…`. Its panel digest is
`c3ff7ed1c6f6dea409c9e8df043287d4845abd4a17c7bdfb71be09879ec92c28` and
measurement-plan digest is
`d9b4c9425453d9c73c636b4d56d7d642ce4c0ff0ae6e43a620c206f2880fcc23`.
The Provider has not executed an ECS panel trial yet.
