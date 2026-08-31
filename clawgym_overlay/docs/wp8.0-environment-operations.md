# WP8.0 environment recipe operations

WP8.0 is a local materialization boundary, not a live-cluster runner. The
overlay compiler accepts only an explicit proposal, recipe bundle, request,
base EnvironmentRelease and output directory. It emits canonical component
JSON plus a digest-bound receipt; it never reads an implicit workspace,
contacts Kubernetes, invokes a model or creates a lease.

Three closed recipe families are supported:

- `fault`: `ingress_egress` or `ingress_only`, always targeting the frozen
  `NetworkPolicy/hotel-reservation/deny-all-recommendation` contract;
- `workload`: `baseline`, `low` or `high` static profiles;
- `observability`: `standard` or `high_frequency` static capture profiles.

One recipe changes one EnvironmentRelease component digest. Upstream revision,
Oracle, ToolAccessProvider, execution backend, target namespace and image are
invariants. Candidates cannot supply YAML, commands, paths, modules, plugins,
images or secrets. Output creation is exclusive and regular-file-only, so a
second materialization cannot overwrite the first.

ClawGym verifies the receipt and creates the same-runtime control and temporary
candidate releases. Its fake lifecycle uses a host-derived control namespace,
an exclusive lease, monotonic TTL, abort/expiry recovery and idempotent cleanup.
WP8.0 records this lifecycle for offline replay; WP8.1 is the first stage that
may qualify a candidate on ECS.

Local verification on 2026-08-31: the Provider suite observed 655 passing
tests, one pre-existing environment-dependent skip and four deselected tests;
the two focused environment-materializer tests pass. These are local
implementation results and are not ECS evidence.

On 2026-09-01 the compiler was also exercised on the restored ECS host in an
isolated source snapshot. The explicit `fault/ingress_only` receipt digest was
`b55a8e25f936d160a593a9f73ef09deebb16f4c9fc78941c62c174ecaf108ef9`; no
Kubernetes candidate resources were created. This remote replay is retained
outside Git and is supplemental to the local protocol gate.
