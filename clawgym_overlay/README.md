# ClawGym SREGym Provider Overlay

This namespace is the sole home for ClawGym-specific integration with SREGym.
It is intentionally scaffolding-only in WP2: no EnvironmentProvider,
OracleProvider, ToolAccessProvider, ObservationProvider, ExecutionBackend, or
Conductor bridge is implemented yet.

WP3 will adapt the pinned SREGym implementation through public seams first.
When no suitable seam exists, a minimal inherited-core patch may be proposed
under the repository contributor rules. ClawGym consumes released provider
commits through explicit registration and immutable release manifests; it does
not copy SREGym core code into the stable kernel.

The pinned source identity is recorded in `upstream-baseline.json`. Reviewed
upstream synchronization follows `docs/upstream-sync.md`.
