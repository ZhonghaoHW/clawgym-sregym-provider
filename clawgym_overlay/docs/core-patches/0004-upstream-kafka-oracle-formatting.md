# Core patch 0004: upstream Kafka oracle formatting

The pinned upstream tree already contains the Kafka producer leak mitigation
oracle.  The provider fork's quality gate requires Ruff's import ordering and
formatting for first-party Python sources, while the pinned file predates that
formatting.  The Wave 3 reconciliation therefore records this behavior-
preserving normalization as an explicit core patch instead of treating an
unexplained root-file diff as overlay code.

This patch changes only import ordering and blank-line formatting in
`sregym/conductor/oracles/kafka_producer_leak_mitigation.py` and
`sregym/conductor/problems/kafka_producer_leak.py`.  It does not add an oracle
or problem, alter a verdict, change fault injection or recovery, or introduce
ClawGym-specific dependencies.  The upstream Kafka problem and oracle remain
owned by the SREGym-derived tree and retain their upstream history.

The provenance test verifies the file's exact SHA-256 and the rationale path.
The pinned upstream bytes are intentionally not restored because they fail the
provider's declared Ruff gate; no semantic workaround or test relaxation is
used.
