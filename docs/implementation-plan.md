# Implementation plan and delivery workflow

Start date: 2026-10-05. Estimates use effective work weeks, not calendar promises.

## Milestones

| Milestone | Deliverables | Acceptance gate | Estimate |
|---|---|---|---|
| S0 | pinned runtime, preflight, two tasks, real single-trial runner, native result validation | real self-hosted E2B + agent + inference + verifier, failure samples, cleanup | 1–2 weeks |
| S1 | immutable snapshots, Job/Trial/Execution tables, authenticated APIs, transactional leases, fencing, bounded retry/recovery | real PostgreSQL two-worker race, expired owner rejected, idempotent completion | 2 weeks |
| S2 | object manifests, raw/normalized trajectories, retryable uploads, Trial viewer | diagnose a failed trial entirely through Portal | 1 week |
| S3 | revisions/registry, deterministic planner, FIFO/capacity, Create Job and Job viewer | 100 logical trials, progressively validated 1/5/10/20 concurrency | 2 weeks |
| S4 | cancel/cleanup, recovery tests, telemetry, deployment/runbook | restart/429/upload failure tests and user acceptance | 1–2 weeks |

The estimate assumes two backend/platform engineers and one frontend/full-stack
engineer with infrastructure support. AI assistance does not remove environment
provisioning, protocol compatibility or fault-testing dependencies.

## Per-feature workflow

1. Read actual pinned upstream interfaces and the relevant repo rules.
2. Record the requirement, invariant, dependency and acceptance criterion.
3. Define shared schema and state semantics; review every consumer.
4. Add a reversible database migration where persistence changes.
5. Implement service transactions, authorization and error paths.
6. Expose API contracts and generate consumer types from OpenAPI.
7. Run unit/integration tests; use real PostgreSQL for locking invariants.
8. Run real Harbor/E2B/inference acceptance for main-chain changes.
9. Build UI against the accepted API rather than inventing states.
10. Run `make check`, inspect the diff for public suitability, commit and normal push.
11. Update the milestone evidence and limitations. Never mark missing real E2E as passed.

## Current dependency gate

Runtime deployment, model endpoint protocol and first Harness need non-sensitive
configuration from the owner. Credentials must be configured locally, never in chat
or public commits. While these are unavailable, implementation may cover tooling,
fixtures and contract design; execution acceptance remains pending.

Advanced pause/resume/fork, analytics mining and training integration are deferred
until the reliable user experiment loop passes the MVP gate.
