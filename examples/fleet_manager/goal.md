# Goal: production-ready health checks for the orders service

The `orders` service (a small FastAPI app) needs health checks before it moves to the shared Kubernetes
cluster. Keep the changes small and reviewable; every task should leave the service runnable.

## Tasks

- Add a `/healthz` liveness endpoint that returns `{"status": "ok"}` and the build version.
- Add a `/readyz` readiness endpoint that checks the database connection with a 2 second timeout.
- Write pytest tests for both endpoints, including the database-down case.
- Document the endpoints and the Kubernetes probe settings in `docs/health.md`.

## Constraints

- Python 3.12, FastAPI, no new runtime dependencies.
- Tests must run offline.
