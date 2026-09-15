# Core patch 0005: shared infrastructure cleanup ownership

The pinned Conductor captures a bare-cluster baseline before installing
OpenEBS and observability infrastructure. The original reconciliation logic
treated every resource absent from that snapshot as problem-owned. On a
long-lived cluster this deleted the `observe` and `openebs` namespaces during
normal problem cleanup, and could also delete their cluster-scoped bindings,
storage classes, custom resources or persistent volumes. A stale baseline
therefore became a destructive ownership decision.

This patch keeps the upstream lifecycle and cleanup seam, but makes the
ownership boundary explicit and fail-closed:

- shared execution namespaces (`sregym`, `khaos`, `openebs`, `observe`, the
  Kubernetes system namespaces and `chaos-mesh`) are never namespace-delete
  candidates;
- ClusterRoleBindings whose ServiceAccount subjects belong to those
  namespaces, and the referenced ClusterRoles, are retained even when the
  persisted baseline predates them;
- persistent volumes claimed by a shared namespace and OpenEBS cluster
  resources are retained; and
- the ownership inventory is completed before any deletion, with an inventory
  failure aborting reconciliation instead of treating an empty response as a
  safe state.

The patch does not grant an agent more access, change a problem, alter an
oracle verdict or make a shared namespace a problem-owned resource. The
focused regression test uses a legacy bare baseline and verifies both safe
retention and fail-closed behavior. The ECS run that exposed the defect is
retained as aborted evidence; it is not reinterpreted as a passing run.
