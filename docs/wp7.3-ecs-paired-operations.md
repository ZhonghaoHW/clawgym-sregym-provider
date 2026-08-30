# WP7.3 ECS paired execution record

## 2026-08-30 outcome

The WP7.3 six-episode control/candidate matrix was not admitted. The first
five attempts failed before model invocation because the SREGym filtering
proxy tried to overwrite `/tmp/sregym-agent-kubeconfig`, a `root:root`
`0644` file that was not writable by `ecs-user`. The lifecycle correctly
recorded `tool_grant_PermissionError`, blocked the Oracle, and ran recovery
and cleanup.

The retry used an explicit user-owned `TMPDIR`, so the stale-file permission
failure was removed. The E0 readiness control then failed its reset
postcondition: nodes, application readiness, and image inventory were healthy,
but all baseline frontend-to-recommendation connectivity samples were
unhealthy. Since E0 is a hard admission gate, the candidate matrix was
stopped; no semantic agent conclusion may be drawn from these attempts.

Remote evidence is archived outside Git at
`/Users/elizhong/Documents/project/_artifacts/wp7-reference-evolution/wp73/ecs-paired-action-efficiency-v2/remote-final`.
The final remote check showed four Ready nodes, baseline namespaces only, no
agent containers, and the gateway secret at mode `0600`.

## Recovery requirement

Before any future WP7.3 attempt, run an environment-only readiness check that
proves the baseline recommendation probe is healthy for the full configured
window. Set `TMPDIR` to a user-owned directory for every worker invocation;
do not remove or overwrite root-owned files. Only after E0 passes may the
approved six-episode matrix be started.
## Readiness failure diagnosis (2026-08-30)

The failed paired run is retained as infrastructure evidence.  The worker
created a shared `/tmp/sregym-agent-kubeconfig`; an earlier root-owned file
made the unprivileged worker fail before model invocation.  A later isolated
run removed that collision, but E0 reset still reported an unhealthy baseline
frontend-to-recommendation probe.  Nodes, deployments and locked image
inventory were healthy, so the Oracle error was not evidence of an agent
semantic failure.  Future attempts must use a private, empty per-attempt
`TMPDIR` and retain a redacted baseline connectivity diagnostic (service,
endpoint and deployment identities/counts only).  The probe result remains
Oracle-owned; diagnostics never turn a failed reset into a pass.

The diagnostic is emitted in the reset postconditions as
`baseline_connectivity_diagnostic`.  It contains no command output, paths,
exception text, kubeconfig, IP address or secret.  A missing or unhealthy
baseline is fail-closed and blocks the candidate matrix until the environment
is repaired and a new control attempt is recorded.

## Readiness repair attempt (2026-08-30, isolated checkout)

The new detached checkouts passed the no-model readiness probe
(`5b2070e86b416b677e1fd8a6e725f8bccc754709404c22679a671f67105588ee`). The
first E0 invocation was rejected before lifecycle execution because the caller
supplied a non-platform-locked EnvironmentRelease. The corrected invocation
used the exact E0 release and pinned Provider runtime, reached reset/deploy,
then blocked in the upstream Helm `dependency update` for Prometheus because
the ECS host could not reach `prometheus-community.github.io`. No bundle,
episode or Oracle verdict was produced. The process was terminated, the
OpenEBS namespace created by this attempt was removed, and the final check
showed four Ready nodes, baseline namespaces only and no agent process.

The blocked evidence is archived outside Git at
`_artifacts/wp7-reference-evolution/wp73/ecs-paired-action-efficiency-v3/remote-e0-blocked`.
This is an infrastructure block, not a candidate result. A future retry
requires verified offline Helm dependencies or restored network access without
changing the pinned Provider runtime; the WP7.3 matrix must remain stopped
until E0 completes.

## Paired rerun (2026-08-31)

After E0 readiness was restored, the fixed three-case control/candidate matrix
ran sequentially on Provider `f33c61118bc8aa8de93b96a7ad944386c8bbba15`.
All six admitted executions passed typed handoff, one exact NetworkPolicy
mutation, target and endpoint verification, Oracle `pass`, recovery and
cleanup. Control case-002 attempt1 remains a reset-stage infrastructure block;
only its pre-authorized distinct-claim attempt2 is admitted. The external
archive is
`/Users/elizhong/Documents/project/_artifacts/wp7-reference-evolution/wp73/ecs-paired-action-efficiency-v4/remote-formal-final-20260831`.
Offline aggregation measured 83 control actions versus 63 candidate actions
(24.10% lower). This is a same-family efficiency observation only; it is not
cross-agent conformance or automatic promotion. Final ECS state had four Ready
nodes, baseline namespaces only, no agent containers and secret mode 0600.
