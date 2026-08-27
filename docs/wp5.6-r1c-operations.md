# WP5.6 R1c operations

R1c is an immutable Reference Agent profile using `openai/glm-5.3-flash`, a
900-second container bound, and eight diagnosis/mitigation steps. The wrapper
is mounted only for the explicitly registered R1c digest and does not alter
R0, R1, R1b or upstream SREGym.

Each run emits a structured diagnosis handoff, a redacted action ledger, and
read-only Oracle attribution snapshots. The lifecycle remains invocation →
Oracle → recovery → cleanup. No candidate or provider code is dynamically
loaded, and no remote run is started by Evolution Lab.
