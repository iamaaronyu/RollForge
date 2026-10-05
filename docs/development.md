# Development and publication

Install using the committed uv.lock and npm package-lock.json. Run `make check`
before committing; CI runs Python lint/format/tests and frontend typecheck/build.
Business features need tests appropriate to their invariants. Execution changes also
need real runtime acceptance. Check endpoints separately for liveness and readiness;
readiness currently verifies only the database, because other data services are not used yet.

## Periodic commits

A local Codex chat schedule checks this checkout every four hours and commits/pushes
verified public-safe changes. No changes means no commit. This schedule is configured
in the app, not a server-side GitHub workflow. Keep the desktop app and machine running;
Git authentication and network access must remain available.

Review diff and selected paths, then run checks. Never use an indiscriminate `git add .`
for unattended publication. Exclude credentials, `.env`, original internal planning
inputs, datasets, raw trajectories, logs and generated outputs. If checks fail or there
is a conflict, preserve work and report the issue. Never force push or bypass checks.

GitHub CI checks commits already pushed; it cannot upload edits sitting on a laptop.
License selection remains an owner decision; no license has been added by the scaffold.
