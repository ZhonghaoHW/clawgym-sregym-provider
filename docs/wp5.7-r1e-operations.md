# WP5.7 R1e runtime-gated operations

R1e is an immutable R0-derived profile. It keeps the existing model gateway,
image, E0 and filtered tools, while adding an adapter-local fail-closed gate.
Diagnosis must emit a complete identity-bound `clawgym.sregym_diagnosis_handoff.v2`.
Mitigation then performs one exact delete of
`NetworkPolicy/hotel-reservation/deny-all-recommendation`, rereads the target,
checks recommendation endpoint readiness and only then permits submit.

The profile mounts four static config files read-only and uses an explicit
900-second timeout with one mitigation attempt. Compound commands, diagnosis
mutations, cross-resource changes, retries and early submit are rejected. Gate
failure emits a lifecycle marker so host recovery and cleanup still run; it is
never reported as agent success. R1d and all historical evidence remain
unchanged. Remote execution is paused until local gates pass and the operator
authorizes ECS.
