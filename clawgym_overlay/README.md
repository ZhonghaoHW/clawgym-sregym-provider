# ClawGym SREGym Provider Overlay

This namespace is the sole home for ClawGym-specific integration with SREGym.
WP3 implements the reviewed provider boundary here: versioned manifests and a
release builder, explicit composition of the five SREGym provider roles, and a
bridge to the inherited Conductor lifecycle seam. The bridge is covered by a
cluster-free integration test using the real provider classes with fake
infrastructure dependencies.

The inherited Conductor contains one registered, minimal core patch that
separates prepare, fault injection, evaluation, recovery, and cleanup while
preserving its default upstream behavior. ClawGym consumes immutable provider
commits through explicit registration; there is no plugin discovery, dynamic
import, hot loading, or candidate-supplied executable code.

WP5 adds the explicitly composed `SREGymReferenceAgentAdapter` control lane.
It freezes the Stratus invocation profile, accepts only `agent_validation`,
requires the filtered SREGym access handle, and uses an agent-only host secret
file. Its dedicated container path does not mount host credentials, an
administrator kubeconfig, the Docker socket, or oracle access. The local fake
and focused policy tests do not constitute a retained live baseline: a real
three-run matrix, offline bundle verification, and remote cleanup evidence
remain required before WP5 closure.

This overlay still does not implement ZeroClaw, candidate search, environment
evolution, automatic promotion, or an Evolution Lab integration.

The pinned source identity is recorded in `upstream-baseline.json`. Reviewed
upstream synchronization follows `docs/upstream-sync.md`.
