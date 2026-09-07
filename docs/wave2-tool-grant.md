# Wave 2 ToolGrant boundary

`clawgym_overlay/tool_grant.py` is the provider-owned projection of a live
SREGym access grant. The kubeconfig path stays in the private access handle;
the public `clawgym.tool_grant.v1` document contains only run, agent,
environment, capability, scope, TTL and revocation identity.

Grant creation is transactional: a missing handle, failed host verification,
invalid descriptor or descriptor serialization failure stops the filtering
proxy before the error is returned. Revoke validates the grant against the
same provider and run before stopping the proxy. Verifier output is reduced to
a fixed safe summary, so paths and opaque handles cannot enter evidence.

The handle exposes only the ephemeral child-process environment required by a
generic AgentAdapter. It is not serialized into a receipt or public artifact.
No ZeroClaw-specific runner or upstream SREGym code is introduced.

Observed verification in the Wave 2 worktree:

- `PYTHONPATH=/private/tmp/wave2-clawgym:. pytest -q tests/clawgym_overlay/test_provider_composition.py`: 25 passed.
- Ruff focused check: passed.
- `git diff --check`: passed.
- After `uv sync --frozen` and recursive submodule initialization in the
  isolated worktree, `.venv/bin/pytest -q` reported `982 passed, 1 skipped,
  4 deselected`.
