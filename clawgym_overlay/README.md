# ClawGym SREGym Provider Overlay

This namespace is the sole home for ClawGym-specific integration with SREGym.
WP3 implements the reviewed provider boundary here: versioned manifests and a
release builder, explicit composition of the five SREGym provider roles, and a
bridge to the inherited Conductor lifecycle seam. The bridge is covered by a
cluster-free integration test using the real provider classes with fake
infrastructure dependencies. Agent execution is composed separately through
the stable ClawGym `AgentAdapter` contract; the environment provider does not
own an agent runtime.

The inherited Conductor contains one registered, minimal core patch that
separates prepare, fault injection, evaluation, recovery, and cleanup while
preserving its default upstream behavior. ClawGym consumes immutable provider
commits through explicit registration; there is no plugin discovery, dynamic
import, hot loading, or candidate-supplied executable code.

WP5 adds the explicitly composed `SREGymReferenceAgentAdapter` control lane.
It freezes the Stratus invocation profile, accepts only `agent_validation`,
requires the filtered SREGym access handle, and uses an agent-only host secret
file. Its dedicated container path does not mount host credentials, an
administrator kubeconfig, the Docker socket, or oracle access. The worker also
supports the independent `zeroclaw.agent.v1` adapter through the same exact-ID
composition boundary. ZeroClaw receives only explicit released profile and
config-bundle paths plus host-owned executable/workspace bindings; it is never
selected from the lane or from model metadata.

The ZeroClaw binding is an execution composition seam, not a second provider
or evolution authority. Candidate search, environment evolution, automatic
promotion, and Evolution Lab governance remain outside this overlay. A live
ZeroClaw execution must provide the six `--zeroclaw-*` arguments documented by
the worker CLI and must separately inject any allowlisted credential into the
worker process environment; credentials are not read from released artifacts.

The pinned source identity is recorded in `upstream-baseline.json`. Reviewed
upstream synchronization follows `docs/upstream-sync.md`.
