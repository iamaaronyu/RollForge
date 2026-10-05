# Roadmap

1. S0: real Harbor + self-hosted E2B + compatible inference integration.
2. S1: durable single-task Job, Trial/Execution, transactional leases/fencing,
   idempotent finalization and bounded recovery.
3. S2: versioned result manifests, original outputs, upload recovery and Trial viewer.
4. S3: immutable Task/Dataset revisions, registry, FIFO/capacity, 100 logical trials,
   gradually validated 1/5/10/20 concurrency, Job creation/viewer.
5. S4: cancellation/cleanup, fault tests, monitoring and MVP operational acceptance.
6. Later: enterprise governance, comparison, data mining and training exports.

S0 tooling is implemented: pinned runtime, input validation, preflight, two example
tasks, native runner and result projection. Real rollout acceptance remains pending.
See implementation-plan.md for the full delivery workflow and spike.md for execution.
Pause/resume/fork and RL are deferred.
