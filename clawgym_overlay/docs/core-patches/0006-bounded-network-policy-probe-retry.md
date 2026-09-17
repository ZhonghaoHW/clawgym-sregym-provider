# Core patch 0006: bounded NetworkPolicy mitigation probe retry

The Wave 5 Reference run completed the diagnosis handoff, executed the
allowlisted NetworkPolicy mutation, submitted the mitigation and cleaned the
environment, while the authoritative mitigation probe returned false. The
same run's independent causal observation later found the recommendation
endpoint healthy. The mismatch is consistent with a transient service or
network-policy convergence window between endpoint readiness and the first
application request.

The provider oracle therefore retries the existing read-only recommendation
probe at most three times within the existing 60-second probe budget. Each
attempt uses an independently named temporary probe Pod and is deleted before
the next attempt. A run succeeds only when a probe reaches `Succeeded` and
emits the existing `RECOMMENDATION_OK` marker; retries do not weaken the
endpoint, rollout, response-shape or cleanup checks. If the bounded budget is
exhausted, the oracle remains fail-closed.

This is a provider-boundary reliability fix, not an agent, prompt, Oracle
verdict override or Wave 6 evolution change. The focused oracle tests cover a
transient failure followed by success and the all-attempts-fail bound.
