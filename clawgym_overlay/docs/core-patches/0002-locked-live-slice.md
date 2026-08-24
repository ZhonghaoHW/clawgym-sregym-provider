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

For `network_policy_block`, an absent NetworkPolicy is successful idempotent
recovery, while non-404 Kubernetes errors propagate to the host instead of
being hidden by the generic recovery decorator. Focused tests execute the
patched method bodies without a cluster; the formal WP4 episode remains the
live verification gate.
