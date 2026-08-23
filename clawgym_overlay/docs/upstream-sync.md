# Reviewed Upstream Synchronization

The provider baseline starts at upstream SREGym revision
`ba07faf1a322f9b6d4a279643bb796aa2f36f64b`. The local `upstream` remote must
resolve to `https://github.com/SREGym/SREGym.git`; `origin` is the maintained
ClawGym provider fork.

## Sync procedure

1. Fetch `upstream` without changing `provider-main`.
2. Create `sync/upstream-<short-sha>` from the current provider baseline.
3. Record the old and proposed upstream revisions, every recursive submodule
   revision, dependency-lock changes, affected provider contracts, regression
   results, and the rollback commit.
4. Update `upstream-baseline.json` and its provenance test in the same reviewed
   change.
5. Merge with an explicit non-fast-forward merge only after review. Do not
   rebase or force-push a released provider revision.

An upstream update is not an automatic compatibility claim. Any inherited-core
conflict or patch must be documented, tested, and distinguishable from the
ClawGym overlay. EnvironmentRelease identities always use immutable upstream
and overlay commit hashes, never branch names.
