# ClawGym SREGym Provider Contributor Instructions

## Status and authority

This repository is the ClawGym-maintained provider fork of SREGym. The
cross-repository architecture authority is the ClawGym charter at
`/Users/elizhong/Documents/project/clawgym/docs/architecture/charter.md`.
Upstream SREGym history and its MIT license remain authoritative for inherited
code. `clawgym_overlay/` owns ClawGym-specific integration.

## Required reading

Before editing, read completely:

1. `README.md` and `CONTRIBUTING.md`;
2. `clawgym_overlay/README.md`;
3. `clawgym_overlay/upstream-baseline.json`;
4. `clawgym_overlay/docs/upstream-sync.md`;
5. the ClawGym charter, reference model, ADR 0001, and provider contract docs.

## Ownership and invariants

- Preserve upstream history, `LICENSE.txt`, submodule identities, and notices.
- Put provider bridges and integration tests in the dedicated overlay
  namespaces. Do not duplicate SREGym tasks, faults, or oracles in ClawGym.
- Patch inherited SREGym core only when no public seam exists. Every core patch
  requires a rationale, focused test, and overlay release note.
- Keep AgentAdapter implementations at the ClawGym boundary. This repository
  owns the SRE environment implementation, not ZeroClaw or evolution control.
- Never add plugin discovery, candidate-supplied executable providers, runtime
  hot reload, or automatic promotion.
- Never rebase or force-push a released provider revision.

## Working rules

- Use `rg` for discovery and `apply_patch` for manual edits.
- Preserve user changes; do not reset, clean, delete, or rewrite history.
- Run focused tests and the provenance check before committing.
- Do not install infrastructure or run a live episode without separate explicit
  authorization.
- Do not commit credentials, private keys, cloud inventory, task secrets,
  generated episode artifacts, or raw provider output.
