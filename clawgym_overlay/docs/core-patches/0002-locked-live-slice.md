# Core patch 0002: locked live-slice inputs

WP4 must deploy the selected environment from content-addressed inputs rather
than the mutable URLs, chart names, image tags, and recovery wrapper inherited
from the pinned upstream revision.

The neutral `ConductorConfig` therefore accepts optional local paths for the
metrics-server and OpenEBS manifests plus a repository-to-image-digest mapping.
All defaults preserve upstream behavior. The Hotel Reservation application
renders a temporary manifest tree and requires every supplied image override
to match. Loki accepts local, checksum-verified Loki and Promtail chart
archives. No ClawGym type is imported by these core modules.

The same neutral configuration seam pins two remaining runtime inputs that
upstream expresses as `latest`: the MCP deployment image and the wrk2 workload
image. MCP renders a temporary Kustomize tree with an OCI digest, and the
workload generator writes its configured digest into the Job before creation.
The tracked upstream manifests are never rewritten in place. The overlay also
compares the live container image identities with the deployment lock during
the reset postcondition; Kind-bundled control-plane images are covered by the
pinned Kind node image rather than represented as independently pullable
artifacts.

The public fault seam also activates the first configured evaluation stage
after injection. This is required by split host orchestration: `prepare` no
longer calls the combined upstream `start_problem()`, so the later Oracle must
still see `waiting_for_agent=True` and accept the deterministic validation
submission. The legacy `start_problem()` path continues to use the same
private injection primitive and retains its behavior.

The Kubernetes filtering proxy applies its existing hidden-label policy to
namespaced collection responses as well as cluster-wide collections. This
closes the workload-disclosure gap without broadening the agent's authority.

For `network_policy_block`, an absent NetworkPolicy is successful idempotent
recovery, while non-404 Kubernetes errors propagate to the host instead of
being hidden by the generic recovery decorator. Focused tests execute the
patched method bodies without a cluster; the formal WP4 episode remains the
live verification gate.
