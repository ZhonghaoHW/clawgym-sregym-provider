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
