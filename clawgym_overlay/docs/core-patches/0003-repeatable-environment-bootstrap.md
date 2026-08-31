# Core patch 0003: repeatable environment bootstrap

## Problem

WP7.4 exposed a lifecycle-layer reproducibility defect rather than an Agent
quality defect.  Consecutive trials rebuilt observability infrastructure as
part of `reset`, but the sequence was not a closed, locked transaction:

1. the Prometheus PVC was applied before the `observe` namespace existed;
2. a vendored chart still ran `helm dependency update` during every episode,
   introducing an external repository dependency into a locked runtime;
3. Helm installation did not request atomic rollback or a bounded readiness
   wait;
4. the 100%-success connectivity window started before service connectivity
   had produced its first healthy sample.

The observed results were a reset-stage
`baseline_connectivity_unhealthy` attempt and a subsequent recovery-stage
`provider_unclassified` attempt.  Neither reached fault injection or model
execution, so neither is evidence about Gen2-B semantics.

## Patch

The provider fork now applies the following bounded infrastructure policy:

- create and verify `observe` before applying its PVC;
- consume the checksum-locked official Prometheus 25.6.0 chart, including its
  packaged dependencies, without a runtime repository refresh;
- install Prometheus with `--atomic --wait --wait-for-jobs --timeout 300s`;
- treat the locked Loki chart as a complete local archive as well;
- wait for one healthy connectivity sample before starting the declared
  100%-success steady-state window;
- retain a failure if warm-up never succeeds or any sample inside the steady
  window fails.

This follows the Kubernetes separation between startup/readiness admission
and steady-state health, and Helm's documented atomic/wait/timeout controls:

- <https://kubernetes.io/docs/concepts/configuration/liveness-readiness-startup-probes/>
- <https://kubernetes.io/docs/concepts/workloads/pods/pod-lifecycle/>
- <https://helm.sh/docs/helm/helm_install/>
- <https://helm.sh/docs/helm/helm_dependency_build/>

The patch does not change the NetworkPolicy fault, target, Agent tools,
Oracle verdict, mutation gate or cleanup authority.  It changes environment
bootstrap behavior, so a remote replay must publish a new immutable Provider
revision and a new EnvironmentRelease compatibility identity (E0.1).  Frozen
E0 and its historical evidence must not be silently reinterpreted.

The new Prometheus asset SHA-256 is
`8c7e4cc95afe473deb586ea7b9039cc66c5b78493b7cf4c9ba18a08417fbbda5`.
The resulting deployment-lock digest is
`d68c8e384809d6e9142dd15f88322d819c5cc589b568b267265b0ce3d493728c`;
an empty-cache materialization produced six verified assets with set digest
`41f4fd0b148cdd2dfbcba48cf04a527a9a658b0bde6263e23a26b426954a449e`.

## Admission rule for the next WP7.4 attempt

Before any model invocation, the new release must pass two consecutive
no-model controls from a clean baseline.  Each control must prove namespace,
PVC, Helm release, Pod readiness, baseline connectivity, deterministic
recovery and cleanup.  Only then may a new campaign identity authorize Gen1
or Gen2 trials.  The exhausted historical Gen2-B attempts remain terminal.

## Local verification boundary

The implementation was verified offline in the provider worktree with the
following observed results:

- provider full suite: `653 passed, 1 skipped, 4 deselected`;
- upstream provenance/core-patch audit: `11 passed`;
- ClawGym regression: `135 passed`;
- Evolution Lab regression: `75 passed`;
- deployment-lock materialization and Prometheus chart templating succeeded;
- diff, compilation and secret-pattern checks passed.

These results prove the ordering, lock and readiness logic in isolation. They
do not prove that a new ECS host can materialize the cache or pass E0.1. The
remote gate remains: publish an immutable Provider revision, derive a new
EnvironmentRelease identity, then pass two consecutive no-model controls before
any campaign episode. Frozen E0 and the historical WP7.4 attempts remain the
only evidence for their original identities.
