# WP5.8 replay operator contract

R1n replay is executed only from the immutable Provider revision
`164f4924a99f98042adfd50a39772d1a0ed4f6ff`, because that revision is embedded
in the released R1n AgentRelease. The worker must receive committed ClawGym
RunManifest and release files; external staging is not a valid normal path.

Every retained bundle must contain the worker-generated
`host/source-checkouts.json`. The offline ClawGym control plane derives
provenance from that artifact and rejects caller-supplied or mismatched source
revisions. This provider does not create provenance sidecars, access the
Evolution Lab, or change the R1n runtime behavior.

Run case-001, case-002 and case-003 sequentially with the fixed seeds. A
failed infrastructure attempt is retained and retried at most once with a new
attempt identity; semantic Oracle failure is evidence, not permission to alter
the environment or R1n.
