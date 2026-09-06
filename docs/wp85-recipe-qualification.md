# WP8.5 recipe qualification

This additive Provider path closes the workload and observability recipe
families left defined-only by WP8.4A.  The historical fault-only
`qualification_runner.py` is unchanged.  `recipe_qualification_runner.py`
accepts only the static component bundle and explicit trial, uses a typed
SREGym backend seam, and exports safe state/metric summaries.

Workload trials use the fixed wrk2 profiles and require achieved rate within
the controlled band, error rate at most five percent, and saturation
protection.  Observability trials use fixed observer selectors in the
`observe` namespace and require fresh, continuous, bounded-cardinality
Prometheus/Loki/Jaeger/collector signals.  Both paths require healthy
baseline/configured/recovered/cleaned states, Oracle pass, and cleanup with no
control namespace residue.

All remote operations are no-model, serial, TTL-bounded and retained outside
Git.  A qualified report remains `reference_environment_only` and cannot
automatically change the Catalog or active baseline.

## ECS observation (2026-09-06)

The final Provider runner completed all eight workload and five observability
trials on the four-node Kind cluster. The release-backed runner is Provider
commit `752f302ec1434a67eb8658409c2a57f69c39ad3f`; its source fingerprint is
`bdfe7fb22ef49255090adf631a10eadff6db16306947485222287d1d0bb4002e`, which
matches the committed remote runner. Workload metrics stayed within the
fixed rate/error/saturation gates; observability retained the required signals
with freshness, continuity and cardinality checks. Every selected trial ended
with cleanup and no residue.

An initial observability control attempt exposed a real service-start race:
Pods were Ready before the recommendation Service responded. The runner now
uses a bounded 60-second health wait and records the failed pre-fix attempt
separately; the retry passed. This is an explicit runtime fix, not a hidden
retry or a changed qualification threshold. The remote archive and signed
evidence index are retained outside Git; no candidate resource or temporary
lease remains on the cluster. The release-backed signed evidence index and
13 selected trial receipts are retained outside Git; the single bounded retry
is recorded separately and does not replace the failed attempt.
