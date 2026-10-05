# RollForge

Open agent rollout and evaluation platform. Reuse Harbor for execution and E2B for
sandboxing; build the experiment control plane and rollout data layer around them.

**Status: S0 runtime spike implementation; real execution acceptance pending.**
Health endpoints, shared schemas, state-machine validation and a web landing page
are implemented. An isolated pinned Harbor/E2B runner, preflight and native result
projection are available. Job execution through Hub, lease persistence, registries
and production results storage are not yet implemented.

## Quick start

Requirements: Python 3.11+, uv, Node.js 20.9+, npm, Docker Compose (local services).
The isolated Harbor 0.24.0 runtime requires Python 3.12+.

```sh
cp .env.example .env
uv sync --all-packages --locked
npm --prefix apps/hub-web ci
make infra
make api
# In another terminal:
make web
```

API: http://localhost:8000/docs · Web: http://localhost:3000

```sh
make check
make worker-check
make scheduler-check
uv run alembic -c apps/hub-api/alembic.ini current
```

The migration environment is wired; domain tables are intentionally deferred until
the execution contract is reviewed. Worker/scheduler only support `--check` and
explicitly refuse to pretend to execute jobs.

## Layout

```text
apps/hub-api              FastAPI + SQLAlchemy + Alembic
apps/hub-web              Next.js + TypeScript
services/worker          execution process entry point
services/scheduler       scheduling process entry point
packages/schemas         authoritative domain contracts
packages/common          environment settings
packages/harbor-adapter   runtime integration boundary
packages/sandbox-provider provider configuration boundary
packages/hub-sdk          typed API client
infra/                   local infrastructure and E2B notes
tests/                   unit / integration / real E2E criteria
```

See [architecture](docs/architecture.md), [development](docs/development.md),
[compatibility spike](docs/compatibility.md), [spike commands](docs/spike.md),
[implementation plan](docs/implementation-plan.md) and [roadmap](docs/roadmap.md).
A license has not yet been selected; public visibility does not grant a software license.
