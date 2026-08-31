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

Local WP8.2 control-plane artifacts are retained outside Git. Panel digest is
`16b37a5e7c96b02b351c03647fb03c857b4c2d33bef8ca890c6a877b184a77f1` and
measurement-plan digest is
`28d136d43ed610d90262afc50aa1edccc8b42dff2ec01d62521a011d8912ec7a`.
The Provider has not executed an ECS panel trial yet.
