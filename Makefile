.PHONY: install check test postgres-check api-types api web infra worker-check scheduler-check runtime-install runtime-check spike
export UV_CACHE_DIR := $(CURDIR)/.cache/uv
install:
	uv sync --all-packages --locked
	npm --prefix apps/hub-web ci
check:
	uv run ruff check .
	uv run ruff format --check .
	uv run python scripts/export_openapi.py --check
	uv run pytest
	npm --prefix apps/hub-web run typecheck
	npm --prefix apps/hub-web run build
test:
	uv run pytest
postgres-check:
	@test -n "$$ROLLFORGE_TEST_DATABASE_URL" || (echo "需要设置专用 PostgreSQL 测试地址 ROLLFORGE_TEST_DATABASE_URL"; exit 2)
	uv run pytest tests/integration/test_execution_postgres.py tests/integration/test_job_api_postgres.py tests/integration/test_worker_postgres.py
api-types:
	uv run python scripts/export_openapi.py
	npm --prefix apps/hub-web run api-types
api:
	uv run uvicorn rollforge_api.main:app --reload --host 127.0.0.1 --port 8000
web:
	npm --prefix apps/hub-web run dev
infra:
	@test -n "$$ROLLFORGE_INFRA_ENV" || (echo "请设置 ROLLFORGE_INFRA_ENV 指向权限为 0600 的独立 infra.env；禁止使用开发 .env"; exit 2)
	uv run python scripts/start_infrastructure.py --env-file "$$ROLLFORGE_INFRA_ENV"
worker-check:
	uv run python -m rollforge_worker --check
scheduler-check:
	uv run python -m rollforge_scheduler --check
runtime-install:
	uv sync --project integration/harbor-runtime --python 3.12 --locked
runtime-check:
	uv run --project integration/harbor-runtime pytest -c integration/harbor-runtime/pytest.ini integration/harbor-runtime/tests
spike:
	uv run --project integration/harbor-runtime python examples/run_one_trial.py
