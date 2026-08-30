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
