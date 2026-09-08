# Wave 3 local projection status

**Last verified:** 2026-09-08
**State:** `IN_PROGRESS_LOCAL_ONLY`

The Provider worktree contains the strict, secret-free
`sregym.environment_projection.v1` producer. It covers environment release,
readiness, lifecycle, ToolGrant status, lease and cleanup summaries, Oracle
availability category and evidence digests. It performs deterministic
canonical hashing, rejects duplicate grants, unknown fields and conflicting
identities, and does not import ClawGym or Evolution Lab code.

Observed: `PYTHONPATH=.:/Users/elizhong/Documents/project/_wave3/clawgym
/Users/elizhong/Documents/project/.venv/bin/pytest -q
tests/clawgym_overlay/test_environment_projection.py` passed (`3 passed`);
focused Ruff, compileall and `git diff --check` passed. The Provider full
suite was attempted but collection stops because this machine image lacks the
upstream `kubernetes` dependency. No ECS or live episode ran.

The generated Provider projection was also accepted by ClawGym's schema loader
in a cross-repository local check; no Python implementation was imported from
ClawGym or Evolution Lab. Repository-wide Ruff reports two pre-existing
findings outside the Wave 3 projection files; the changed-file Ruff check is
clean.
