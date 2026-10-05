# Architecture

Portal → Hub API → Job/Trial/Execution → Scheduler → Worker → Harbor → E2B → Agent
→ inference endpoint → verifier → results → object storage + Hub metadata.

The Hub is a modular application, not a collection of microservices. PostgreSQL
will own state and leases. Redis can notify/cache; recovery must work from PostgreSQL.
Harbor owns the sandbox lifecycle. Provider abstractions supply validated configuration
and capabilities; they must not create a second sandbox outside Harbor.

Logical Trial identity is task revision × agent revision × model revision × attempt
within a Job. Retry creates a new Execution without increasing benchmark sample count.
Execution callbacks will require fencing tokens; only the current owner may finalize.
The state-machine helper is validation only, not a distributed lease implementation.

Versioned original outputs are the source for viewer projections. Missing usage and
reasoning fields are unknown, not zero or invented. Reward zero is a valid evaluation
result, not an infrastructure failure. Metrics must separate score coverage from pass rate.

The isolated S0 runtime pins Harbor 0.24.0 and E2B SDK 2.25.0. Their actual Task,
TrialConfig, Trial.create and SDK configuration contracts are checked offline.
Self-hosted runtime deployment, inference protocol and real execution still require
environment-specific evidence; the Hub execution capability remains disabled.
