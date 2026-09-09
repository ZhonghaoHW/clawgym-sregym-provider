# Wave 3 local projection status

**Last verified:** 2026-09-08
**State:** `IN_PROGRESS_LOCAL_ONLY`

The Provider worktree contains the strict, secret-free
`sregym.environment_projection.v1` producer. It covers environment release,
readiness, lifecycle, ToolGrant status, lease and cleanup summaries, Oracle
availability category and evidence digests. It performs deterministic
canonical hashing, rejects duplicate grants, unknown fields and conflicting
identities, and does not import ClawGym or Evolution Lab code.

Observed: with `PYTHONPATH` explicitly pointing to the published ClawGym
reconciliation candidate `a24fe9fb0a037aebe0ef14d3ab4bdf19c36dcf59`, the
Provider full suite passed (`985 passed, 1 skipped, 4 deselected`); focused
Ruff, compileall and `git diff --check` also passed. No ECS or live episode
ran.

The generated Provider projection was also accepted by ClawGym's schema loader
in a cross-repository local check; no Python implementation was imported from
ClawGym or Evolution Lab. The provider dependency and lockfile now pin the
published ClawGym revision; the upstream-baseline manifest explicitly includes
the already tracked `docs/wave3-local-projection-status.md` root change.

## Reconciliation addendum (2026-09-09)

The current published Provider head is `0498df19`. The full Provider suite was
re-run in the declared dependency environment with recursive submodules
initialized: exit 0, `987 passed, 1 skipped, 4 deselected`. The materialized
Reference adapter regression through the retained worker also passed: exit 0,
`27 passed`. Ruff, compileall and `git diff --check` remained green. No
Provider Wave 4 product change was required; the Provider remains the owner of
SREGym lifecycle, ToolGrant, lease, cleanup and Oracle boundaries.
