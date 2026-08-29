# WP5.8 replay operator contract

R1n replay is executed only from the immutable Provider revision
`164f4924a99f98042adfd50a39772d1a0ed4f6ff`, because that revision is embedded
in the released R1n AgentRelease. The worker must receive committed ClawGym
RunManifest and release files; external staging is not a valid normal path.

Every retained bundle must contain the worker-generated
`host/source-checkouts.json`. The offline ClawGym control plane derives
provenance from that artifact and rejects caller-supplied or mismatched source
revisions. This provider does not create provenance sidecars, access the
Evolution Lab, or change the R1n runtime behavior.

Before any control or R1n run, use the committed ClawGym host preflight to
create an exclusive `clawgym.execution_readiness.v1` receipt outside Git. It
checks the detached checkouts, `docker`/`kind`/`kubectl`/`helm`, the
checksum-locked deployment cache, workspace readability and four-node Kind
readiness without creating an episode. A blocked receipt must be repaired and
recreated before running the no-model E0 control release.

The E0 control release uses the static `sregym.environment-validation.v1`
adapter and Provider runtime reference `164f4924a99f98042adfd50a39772d1a0ed4f6ff`.
It is not R1n evidence and must complete reset, fault, Oracle, recovery and
cleanup before the R1n matrix. The fixed Provider checkout is not changed.

Run case-001, case-002 and case-003 sequentially with the fixed seeds. A
failed infrastructure attempt is retained and retried at most once with a new
attempt identity; semantic Oracle failure is evidence, not permission to alter
the environment or R1n.

## Recovery after loss of the ECS worker

The downloaded bundles are sufficient for offline audit, but they are not a
portable live cluster. To execute a new episode, restore the exact Provider
and ClawGym revisions, initialize recursive submodules, and materialize the
committed deployment lock on a fresh Linux-amd64 host. The cache may be copied
from a protected backup or re-downloaded from the lock's immutable sources;
every manifest, chart and OCI image must pass its recorded digest check. Do
not substitute a mutable image tag or a different Kind node image.

Recreate Docker, Kind, kubectl, Helm, uv, the four-node Kind topology and the
filtered kubeconfig/MCP setup, then inject the gateway key through a new
`0600` secret file. The old readiness receipt only describes the old host and
cannot be reused as proof for the new one. Run a new no-model readiness check,
the E0 control, and then the three fixed R1n cases in order. Upload the new
bundles to a new external archive and create fresh provenance/attestation
sidecars; never reuse an attempt identity or the attestation private key.

## GitHub reserve

The sanitized offline reserve and exact source snapshots are published in the
private repository `https://github.com/ZhonghaoHW/clawgym-recovery-archive`,
release `wp5.8-recovery-2026-08-30`. Restore the deployment cache, Kind config
and recorded source/submodule snapshots from that release and verify
`SHA256SUMS` before use. Locked image layers are intentionally absent and must
be re-materialized from the deployment lock or a separately retained custom
image. Secrets, kubeconfig and signing private keys are never part of the
reserve and must be injected/generated on the replacement worker.

## Test and adapter boundary

Provider validation has four deliberately separate entry points. The default
`uv run pytest` command uses only the repository's offline `tests/` tree and
must not contact a cluster, gateway, model endpoint or external application.
Integration tests are selected explicitly with `-o addopts='' -m integration`;
they retain their host-capability preflights and fail closed when Kind/OpenEBS
state is absent. Slow model-judge tests are selected explicitly with
`-o addopts='' -m slow` and require `JUDGE_MODEL_ID`.
The upstream train-ticket API suite is a separate live-application command and
requires an explicit `GATEWAY_URL` or an already configured Kubernetes service.

The kubectl fixture suite additionally relies on a small, explicit helper
contract: each validator expression receives the captured command output as
`opt`. If that binding is missing, the suite fails deterministically before
testing the agent. Keep this helper in the integration layer and verify it
with an offline regression; do not weaken production tool identity or hide the
failure with a skip.

Before running this suite on a restored worker, re-materialize the checked-in
MCP ClusterRole and ClusterRoleBinding and verify the tool service account can
perform the fixture operations in its test namespace. A missing binding or a
stale port-forward is an infrastructure-gate failure, not an agent verdict.
The fixture harness also pins its working directory and uses the async graph
API required by the MCP tool node; these properties are part of the live test
contract.

The AgentAdapter boundary has two recurring invariants. A session identity is
created once and passed unchanged through the transport, tool constructor and
retry path. Test doubles must use real constructors (or a shared complete
factory) rather than `__new__`, because newly required release/tool state must
be initialized explicitly. A missing host capability remains an infrastructure
finding; it must never be converted into a successful episode by changing the
Oracle or relaxing the adapter gate.
