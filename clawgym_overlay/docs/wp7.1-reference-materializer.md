# WP7.1 Reference materializer

`clawgym_overlay.materializer` is an offline, explicit-path compiler for
declarative Reference SOP components. It accepts a proposal, component bundle
and parent profile, validates canonical digests and bounded NFC/LF text, then
creates an exclusive profile/config/receipt directory. It never scans a
workspace, loads candidate Python, mounts a candidate path, calls a model or
touches Kubernetes.

The parent supplies the adapter, command, model, endpoint, secret injection,
tool policy and fixed artifact boundary. A candidate can change only the six
bounded diagnosis/mitigation text components and step limits. The generated
profile always retains the pinned `reference_driver_r1f` command and 900-second
timeout. Re-running into a second empty directory is byte-identical; reusing an
existing output directory fails closed.

`load_materialized_reference_profile` is the only runtime entry for a v2 bundle
and requires the exact profile digest from AgentRelease. Historical R0–R1n
loaders remain unchanged. The receipt is later verified by ClawGym before a
new AgentRelease or RunManifest is constructed; materialization itself grants
no execution authority.

## Offline compatibility evidence (2026-08-30)

The released R1n prompt files were compiled from explicit paths using Provider
revision `15782c6883a95698955f7e22b83a0f123a7e3d84`. The generated receipt is
`f84bf474274bf726d8d9351ecd17e0b70be2fb9c23ee868939d702bbb3a51754`; it
describes exactly four files under `reference-materialized/` and their
read-only container destinations. Two fresh output directories compared
byte-for-byte. This evidence is stored outside Git at
`_artifacts/wp7-reference-evolution/wp71/reference-r1n-compat/generated-v2/`.

The ClawGym consumer additionally rejects absolute/traversal paths, duplicate
entries and any file set other than the four compiler outputs. This is a
materialization-integrity check, not proof that an agent episode succeeds.
