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
