# API scaffold

| Route | Meaning |
|---|---|
| `GET /health/live` | Process liveness, no external dependencies |
| `GET /health/ready` | Database connectivity; 503 when unavailable |
| `GET /api/v1/platform` | Version and explicit scaffold capability status |
| `GET /openapi.json` | Generated API contract |
| `GET /docs` | Interactive API documentation |

No Job/Trial write endpoint is exposed yet. Those endpoints require immutable
config snapshots, authenticated users/workers, transactional leases, fencing,
idempotency and persistent execution state before enabling actual execution.

Health responses never contain connection strings or raw dependency errors.
Redis and object storage are not readiness dependencies until the API uses them.

Bind the development API to loopback. Authentication and production deployment
hardening must be implemented before exposing it outside a trusted development host.
