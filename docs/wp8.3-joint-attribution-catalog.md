# WP8.3 Provider Boundary

WP8.3 does not change the SREGym runtime, fault recipe, Oracle or ToolAccess
Provider. The Provider remains responsible for explicit environment
qualification actions and safe lifecycle evidence; Evolution Lab consumes
released artifacts only. The case-003 WP8.2 failure must not be diagnosed as
an environment defect without a matching environment receipt: incomplete
handoff and zero mutation are Agent-side evidence.

The four WP8.3 stability confirmations are no-model runs using the existing
immutable same-runtime control and ingress-only releases. They are strictly
serial, TTL-bounded and cleanup-gated. No gateway secret is required or
mounted, and no new candidate or dynamic plugin is introduced.

The 2026-09-01 ECS confirmation used the immutable WP8.1 qualification runner
at Provider revision `b041cd3…` with SREGym-applications submodule
`2b2f9c6…`. A missing submodule caused one preflight attempt to fail before
lifecycle execution; after explicit initialization, all four formal trials
completed and cleanup left only baseline namespaces and Ready nodes. This
preflight incident is infrastructure evidence, not an environment semantic
failure.
