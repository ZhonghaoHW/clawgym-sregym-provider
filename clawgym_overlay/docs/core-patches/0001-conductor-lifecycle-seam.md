# Core patch 0001: Conductor lifecycle seam

Upstream revision `ba07faf1a322f9b6d4a279643bb796aa2f36f64b` combines
deployment, fault injection, oracle advancement, recovery and teardown inside
`start_problem()`, `_advance_to_next_stage()` and `_cleanup_sync()`. That shape
cannot produce independently attributable ClawGym lifecycle receipts without
parsing logs or duplicating Conductor behavior.

The provider fork therefore exposes neutral public methods for prepare, fault,
evaluation wait, recovery and cleanup. `ConductorConfig.defer_cleanup` defaults
to `False`, so inherited callers retain automatic cleanup. The ClawGym bridge
sets it to `True`, records the host-side oracle result, then invokes recovery
and cleanup explicitly. `task_stages` provides a static, release-controlled
alternative to a mutable checkout-local tasklist file.

This patch does not import ClawGym, add a problem, replace an oracle, or change
the default runner sequence. The focused lifecycle seam test executes the
actual patched class bodies in isolation from Kubernetes and other heavyweight
services. A real cluster validation remains a WP4 gate.

WP4 extends `ConductorConfig` with optional locked deployment inputs. They
default to `None`, preserving the inherited mutable-source behavior for
non-ClawGym callers; the live ClawGym composition requires verified local
assets and digest-qualified application images.
