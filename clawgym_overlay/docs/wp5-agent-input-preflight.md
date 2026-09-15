# WP5 Agent-input preflight

The provider worker must validate every selected AgentAdapter's host-owned
inputs before it starts the SREGym conductor or enters the environment
lifecycle.  A missing or unsafe agent credential is therefore a zero-effect
admission failure: it must not create a namespace, workload, API server,
filtered kubeconfig, or agent process.

The Reference adapter now validates its explicit `0600` non-symlink secret
file while the adapter is composed.  The worker composes and validates the
selected adapter before importing or constructing the conductor.  The
ZeroClaw builder already performs the equivalent executable, directory,
profile, and config-bundle checks at that boundary.

The runner still reads the Reference credential again immediately before
creating the isolated child process.  That second read is intentional: the
worker preflight prevents environment side effects, while the runner keeps
the final child-process boundary authoritative.  The credential is never
retained in an artifact, receipt, log, or diagnostic.

This ordering is covered by focused tests for missing credentials, runner
construction, and worker startup.  A future AgentAdapter must provide the
same composition-time validation before it can be registered in the worker.
