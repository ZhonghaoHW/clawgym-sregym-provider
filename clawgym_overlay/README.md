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

WP3 does not run Kubernetes, Docker, telemetry services, or a real SREGym
episode. It does not implement a ZeroClaw or reference-agent adapter and does
not promote environment candidates. Those capabilities remain in later work
packages.

The pinned source identity is recorded in `upstream-baseline.json`. Reviewed
upstream synchronization follows `docs/upstream-sync.md`.
