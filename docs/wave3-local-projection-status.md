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
reconciliation candidate `c80634019b3844ca04e4ae4be0e9dc9b15a5e097`, the
Provider full suite passed (`985 passed, 1 skipped, 4 deselected`); focused
Ruff, compileall and `git diff --check` also passed. No ECS or live episode
ran.

The generated Provider projection was also accepted by ClawGym's schema loader
in a cross-repository local check; no Python implementation was imported from
ClawGym or Evolution Lab. The provider dependency and lockfile now pin the
published ClawGym revision; the upstream-baseline manifest explicitly includes
the already tracked `docs/wave3-local-projection-status.md` root change.

## Published ClawGym dependency pin correction (2026-09-09)

The Provider package and lockfile now both pin ClawGym
`c80634019b3844ca04e4ae4be0e9dc9b15a5e097`, the published mainline containing
the current ZeroClaw terminal-receipt classification boundary. The
`tests/test_clawgym_dependency_pin.py` regression rejects a stale or divergent
`pyproject.toml`/`uv.lock` pair, so a clean Provider installation cannot
silently consume the earlier `a24fe9fb` platform revision. This is a dependency
alignment correction only; no SREGym upstream code or lifecycle semantics were
changed.

## Reconciliation addendum (2026-09-09)

The current published Provider head is `7165dd0e`. The full Provider suite was
re-run in the declared dependency environment with recursive submodules
initialized: exit 0, `987 passed, 1 skipped, 4 deselected`. The materialized
Reference adapter regression through the retained worker also passed: exit 0,
`27 passed`. Ruff, compileall and `git diff --check` remained green. No
Provider Wave 4 product change was required; the Provider remains the owner of
SREGym lifecycle, ToolGrant, lease, cleanup and Oracle boundaries.

## Current published base refresh — 2026-09-09

The current Provider mainline is `88cd372cf670331a6ba09b0e9407e36f6c9e84e0`
on `provider-main`, exactly matching `origin/provider-main` and passing the
ancestor check. The base worktree is clean; its uninitialized submodule state
is pre-existing checkout state. A separate recursively initialized worktree
verified the locked Provider suite with `988 passed, 1 skipped, 4 deselected`,
Ruff and `git diff --check` passing.

This is a revision/consumer-alignment refresh only. The Provider continues to
own SREGym upstream-derived environment behavior, ToolGrant, lease, lifecycle,
Oracle, recovery and cleanup. It does not close the Wave 3 paired matrix or
the Wave 4 proposer, human approval, activation, rollback or ECS gates.

## Ledger correction: actual Provider base before this documentation commit — 2026-09-09

The preceding historical checkpoints must not be used as the current base.
The verified Provider revision before this ledger-only correction is
`40a9991b88aef895918075c56cf3b8b75d0a9992` on `provider-main`, exactly equal
to `origin/provider-main` and ancestor-checked. The base worktree is clean;
its uninitialized upstream submodule state is pre-existing and preserved.
The Provider remains a read-only validator for the open Wave 3 paired/live
proposer and Wave 4 human-gated activation/rollback work; no product code is
changed by this correction.
