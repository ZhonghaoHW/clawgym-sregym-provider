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
