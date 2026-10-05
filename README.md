# RollForge

Open agent rollout and evaluation platform. Reuse Harbor for execution and E2B for
sandboxing; build the experiment control plane and rollout data layer around them.

**Status: development scaffold.** Health endpoints, shared schemas, state-machine
validation, workspace packaging and a web landing page are implemented. Job execution,
lease persistence, registries, results storage and real runtime integration are not yet
implemented. This is not a production deployment.

## Quick start

Requirements: Python 3.11+, uv, Node.js 20.9+, npm, Docker Compose (local services).
Harbor's Python requirement will be pinned separately during the runtime spike.

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
[compatibility spike](docs/compatibility.md) and [roadmap](docs/roadmap.md).
A license has not yet been selected; public visibility does not grant a software license.
