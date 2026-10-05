# RollForge development rules

- Read docs/architecture.md and docs/development.md before implementing features.
- Shared domain contracts live only in packages/schemas. Generate frontend contracts
  from OpenAPI once business APIs exist; never invent independent status fields.
- Inspect pinned Harbor source before integration changes. No fabricated APIs or core forks.
- Harbor owns sandbox lifecycle; the platform supplies configuration and extensions.
- Distinguish logical Trial/attempt from physical Execution/retry.
- State changes require state-machine validation and transactional lease ownership.
- PostgreSQL is authoritative. Redis must not be the only durable task record.
- Store large trajectory/artifact/log objects in object storage, not database rows.
- Keep credentials out of config snapshots, logs, fixtures and public commits.
- Main-chain integration changes require real Harbor/E2B/inference validation;
  mocks do not qualify. Report missing environment access explicitly.
- Run make check before commits. Do not automatically repair unrelated user changes.
- User authorizes periodic normal commits/pushes to origin. Never force push,
  rewrite history, publish local planning inputs or resolve conflicts unattended.
